"""HTTP layer. Knows about Mealie; knows nothing about MCP tools."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

import httpx
from fastmcp.exceptions import ToolError

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15.0
GET_RETRIES = 2
RETRY_BACKOFF_SECONDS = 0.5

#: Page size and default ceiling for whole-library sweeps.
LIBRARY_PAGE_SIZE = 100
#: Page size for taxonomy sweeps. Bigger than the recipe page because the rows
#: are small; a typical library still comes back in one request.
TAXONOMY_PAGE_SIZE = 500
MAX_LIBRARY_RECIPES = 2000
#: How many per-recipe requests a sweep runs at once.
FANOUT = 8

TAXONOMY_PATHS = {
    "foods": "/api/foods",
    "units": "/api/units",
    "labels": "/api/groups/labels",
    "tags": "/api/organizers/tags",
    "categories": "/api/organizers/categories",
    "tools": "/api/organizers/tools",
}

#: Resources with a server-side merge endpoint, and the body keys it wants.
MERGE_KEYS = {
    "foods": ("fromFood", "toFood"),
    "units": ("fromUnit", "toUnit"),
}

#: POSTs that change nothing, so they must not invalidate the recipe cache.
NON_MUTATING_POSTS = frozenset({"/api/parser/ingredients"})

#: Ceiling on cached recipe bodies. One sweep of a large library is ~20MB of
#: JSON; past this the cache is dropped rather than grown for the process
#: lifetime.
MAX_CACHED_DETAILS = MAX_LIBRARY_RECIPES


class MealieClient:
    """Thin async wrapper over Mealie's REST API.

    Also owns two process-lifetime caches: recipe slug -> UUID, and taxonomy
    name -> object. Both exist because meal planning re-resolves the same
    handful of recipes and tags over and over.
    """

    def __init__(self, url: str, token: str, verify_ssl: bool = True) -> None:
        """Set up the HTTP client; no request is made until the first call.

        Args:
            url: Base URL of the Mealie instance, no trailing slash.
            token: Long-lived Mealie API token, sent as a bearer token.
            verify_ssl: Verify TLS certificates. Disable only for
                self-signed homelab setups.
        """
        self.url = url
        self._http = httpx.AsyncClient(
            base_url=url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            verify=verify_ssl,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        self._slug_ids: dict[str, str] = {}
        self._taxonomy: dict[str, dict[str, dict]] = {}
        self._details: dict[str, dict] = {}

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._http.aclose()

    # ------------------------------------------------------------------ HTTP

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        files: dict | None = None,
        data: dict | None = None,
        not_found: str | None = None,
    ) -> Any:
        """Issue a request and return the decoded response body.

        GETs are retried on transport errors and 5xx responses (up to
        GET_RETRIES times with linear backoff); writes never are, because
        Mealie has no idempotency key and a retried create would duplicate
        the recipe.

        Args:
            method: HTTP method, case-insensitive.
            path: API path relative to the base URL, e.g. "/api/recipes".
            params: Query parameters; None values are dropped.
            json: JSON-serializable request body.
            files: Multipart file parts, e.g. {"image": (name, bytes)}.
            data: Multipart form fields, sent alongside files.
            not_found: Message to raise on a 404, e.g. "recipe 'x' not found".

        Returns:
            Decoded JSON on success. None for 204 or an empty body. A bare
            string for the create endpoints that return a quoted slug.

        Raises:
            ToolError: On any HTTP error status or if Mealie is unreachable
                after retries.
        """
        method = method.upper()
        attempts = GET_RETRIES + 1 if method == "GET" else 1
        params = {k: v for k, v in (params or {}).items() if v is not None}

        last_transport_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = await self._http.request(
                    method, path, params=params, json=json, files=files, data=data
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # Only GETs get here more than once; writes are never retried
                # because Mealie has no idempotency key and a retried create
                # would duplicate the recipe.
                last_transport_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                raise ToolError(f"Mealie unreachable at {self.url}: {exc}") from exc

            if response.status_code >= 500 and attempt + 1 < attempts:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue

            log.debug("%s %s -> %s", method, path, response.status_code)
            if method != "GET" and path not in NON_MUTATING_POSTS:
                # Any write can change ingredients, so the cached recipe
                # bodies are no longer trustworthy. Clearing on every write
                # is cheaper than reasoning about which path touched what.
                self._details.clear()
            return self._handle(response, not_found)

        raise ToolError(f"Mealie unreachable at {self.url}: {last_transport_error}")

    def _handle(self, response: httpx.Response, not_found: str | None) -> Any:
        status = response.status_code

        if status in (401, 403):
            raise ToolError("authentication failed — check MEALIE_API_TOKEN")
        if status == 404:
            raise ToolError(not_found or "not found")
        if status == 422:
            raise ToolError(f"Mealie rejected the request: {_validation_detail(response)}")
        if status >= 500:
            # Mealie logs the Pydantic error server-side and often puts nothing
            # in the body, but when it does that text is the only clue.
            body = response.text[:200].strip()
            detail = f": {body}" if body else " — the server is unhealthy"
            raise ToolError(f"Mealie returned {status}{detail}")
        if status >= 400:
            raise ToolError(f"Mealie returned {status}: {response.text[:200]}")

        if status == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            # Several create endpoints return a bare slug as a quoted string,
            # but a plain-text body is possible too.
            return response.text.strip().strip('"')

    # --------------------------------------------------------------- caches

    async def recipe_id(self, slug: str) -> str:
        """Resolve a recipe slug to its UUID, caching the result.

        Args:
            slug: Recipe slug as shown by search_recipes.

        Returns:
            The recipe's UUID.

        Raises:
            ToolError: If no recipe has that slug.
        """
        if slug not in self._slug_ids:
            recipe = await self.request(
                "GET", f"/api/recipes/{slug}", not_found=f"recipe {slug!r} not found"
            )
            self._slug_ids[slug] = recipe["id"]
        return self._slug_ids[slug]

    async def resolve_taxonomy(
        self, resource: str, names: list[str], *, create_missing: bool = True
    ) -> tuple[list[dict], list[str]]:
        """Map plain names to Mealie taxonomy objects.

        Mealie's RecipeTag/RecipeCategory require both name and slug, so a
        bare ["Vegan"] cannot be written through. Matching is case-insensitive
        against a cached snapshot of the resource.

        Args:
            resource: Key into TAXONOMY_PATHS ("tags", "categories", ...).
            names: Plain names to resolve, e.g. ["Vegan", "Quick"].
            create_missing: Create names that don't exist yet instead of
                raising.

        Returns:
            A tuple of (resolved objects, names that had to be created), the
            latter so tools can report what was new.

        Raises:
            ToolError: If a name does not exist and create_missing is False.
        """
        if not names:
            return [], []

        path = TAXONOMY_PATHS[resource]
        await self._load_taxonomy(resource)
        known = self._taxonomy[resource]

        resolved: list[dict] = []
        created: list[str] = []
        for name in names:
            key = name.casefold()
            if key not in known:
                if not create_missing:
                    raise ToolError(f"{resource[:-1]} {name!r} does not exist")
                known[key] = await self.request("POST", path, json={"name": name})
                created.append(name)
            resolved.append(known[key])
        return resolved, created

    async def taxonomy_names(self, resource: str, names: list[str]) -> list[str]:
        """Map names to their canonical casing as stored in Mealie.

        Cookbook filter strings match on name, and Mealie's parser is
        case-sensitive there, so "vegan" has to become "Vegan" before it goes
        into a filter. Unknown names pass through untouched — filtering on one
        is not an error, it just matches nothing.

        Args:
            resource: Key into TAXONOMY_PATHS ("tags", "categories", ...).
            names: Plain names, matched case-insensitively.

        Returns:
            One name per input name, in order.
        """
        if not names:
            return []
        await self._load_taxonomy(resource)
        known = self._taxonomy[resource]
        return [item["name"] if (item := known.get(n.casefold())) else n for n in names]

    async def taxonomy_slugs(self, resource: str, names: list[str]) -> list[str]:
        """Map names to slugs for filtering.

        Filtering by a tag that does not exist should return no recipes, not
        raise — so this never creates and never errors; unknown names are
        slugified optimistically instead.

        Args:
            resource: Key into TAXONOMY_PATHS ("tags", "categories", ...).
            names: Plain names, matched case-insensitively.

        Returns:
            One slug per input name, in order.
        """
        if not names:
            return []
        await self._load_taxonomy(resource)
        known = self._taxonomy[resource]
        return [
            item["slug"] if (item := known.get(name.casefold())) else slugify(name)
            for name in names
        ]

    async def taxonomy_items(self, resource: str) -> list[dict]:
        """Page through every item of a taxonomy resource.

        Asking for one huge page instead would truncate silently, and a name
        missing from the snapshot is not a lookup miss — resolve_taxonomy
        would create a second copy of a food that already exists.

        Args:
            resource: Key into TAXONOMY_PATHS ("tags", "categories", ...).

        Returns:
            Every item Mealie holds for that resource.
        """
        path = TAXONOMY_PATHS[resource]
        items: list[dict] = []
        page = 1
        while True:
            result = await self.request(
                "GET", path, params={"page": page, "perPage": TAXONOMY_PAGE_SIZE}
            )
            batch = (result or {}).get("items") or []
            items.extend(batch)
            total = (result or {}).get("total")
            if len(batch) < TAXONOMY_PAGE_SIZE or (isinstance(total, int) and len(items) >= total):
                break
            page += 1
        return items

    async def _load_taxonomy(self, resource: str) -> None:
        if resource not in self._taxonomy:
            self._taxonomy[resource] = {
                item["name"].casefold(): item
                for item in await self.taxonomy_items(resource)
                if item.get("name")
            }

    # ---------------------------------------------------------- library sweep

    async def all_recipes(
        self, *, max_recipes: int = MAX_LIBRARY_RECIPES
    ) -> tuple[list[dict], int]:
        """Page through the whole recipe list.

        These are summaries: name, slug, tags, categories, tools, rating,
        orgURL, image. Ingredients and instructions are not in this payload —
        anything that needs them has to fetch each recipe.

        Args:
            max_recipes: Stop after this many, so a huge library cannot hang
                a single tool call.

        Returns:
            A tuple of (summaries, the total Mealie reports). The two differ
            when the cap cut the sweep short.
        """
        items: list[dict] = []
        total = 0
        page = 1
        while len(items) < max_recipes:
            result = await self.request(
                "GET", "/api/recipes", params={"page": page, "perPage": LIBRARY_PAGE_SIZE}
            )
            batch = result.get("items") or []
            items.extend(batch)
            reported = result.get("total")
            total = reported if isinstance(reported, int) else len(items)
            if len(batch) < LIBRARY_PAGE_SIZE or len(items) >= total:
                break
            page += 1
        return items[:max_recipes], max(total, len(items))

    async def recipe_details(self, slugs: list[str]) -> list[dict | None]:
        """Fetch full recipe bodies for slugs, reusing ones already fetched.

        The sweep is the expensive part of any ingredient-level report: one
        request per recipe, and a library-wide run takes seconds. Foods and
        units are two such reports over the same bodies, so the second one
        should cost nothing. Any write clears the cache, as does growing it
        past MAX_CACHED_DETAILS.

        Args:
            slugs: Recipe slugs, in the order the caller wants results.

        Returns:
            One entry per slug: the recipe body, or None if it could not be
            read.
        """
        unique = list(dict.fromkeys(slugs))
        # ponytail: drop the whole cache rather than evict one by one; the
        # caller is a sweep, and a half-populated cache re-fetches anyway.
        if len(self._details) + len(unique) > MAX_CACHED_DETAILS:
            self._details.clear()

        missing = [slug for slug in unique if slug not in self._details]
        if missing:
            fetched = await map_concurrent(
                [partial(self.request, "GET", f"/api/recipes/{slug}") for slug in missing]
            )
            for slug, result in zip(missing, fetched, strict=True):
                if isinstance(result, dict):
                    self._details[slug] = result
        return [self._details.get(slug) for slug in slugs]

    def forget_recipe(self, slug: str) -> None:
        """Drop a cached slug -> UUID entry after a rename or delete.

        Args:
            slug: The slug that no longer resolves.
        """
        self._slug_ids.pop(slug, None)
        self._details.pop(slug, None)

    def forget_taxonomy(self, resource: str) -> None:
        """Drop the cached snapshot for a resource after an external change.

        Args:
            resource: Key into TAXONOMY_PATHS ("tags", "categories", ...).
        """
        self._taxonomy.pop(resource, None)


async def map_concurrent(
    factories: list[Callable[[], Awaitable[Any]]], limit: int = FANOUT
) -> list:
    """Run coroutine factories with a concurrency cap, keeping input order.

    Args:
        factories: Zero-argument callables returning a coroutine. Factories,
            not coroutines, so nothing starts before its slot is free.
        limit: How many run at once.

    Returns:
        One entry per factory: its result, or the exception it raised.
    """
    semaphore = asyncio.Semaphore(limit)

    async def run(factory: Callable[[], Awaitable[Any]]) -> Any:
        async with semaphore:
            return await factory()

    return await asyncio.gather(*(run(f) for f in factories), return_exceptions=True)


def slugify(name: str) -> str:
    """Approximate Mealie's slug format: lowercase, hyphen-separated ASCII.

    Args:
        name: Human-readable name, e.g. "Comfort Food".

    Returns:
        The slugified form, e.g. "comfort-food".
    """
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")


def _validation_detail(response: httpx.Response) -> str:
    """Trim FastAPI's 422 payload down to the failing fields."""
    try:
        detail = response.json().get("detail")
    except ValueError:
        return response.text[:200]

    if isinstance(detail, str):
        return detail[:200]
    if isinstance(detail, list):
        parts = []
        for item in detail[:5]:
            if isinstance(item, dict):
                field = ".".join(str(p) for p in item.get("loc", [])[1:])
                parts.append(f"{field}: {item.get('msg', '')}".strip(": "))
        if parts:
            return "; ".join(parts)
    return str(detail)[:200]
