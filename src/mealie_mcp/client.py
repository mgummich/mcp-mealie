"""HTTP layer. Knows about Mealie; knows nothing about MCP tools."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx
from fastmcp.exceptions import ToolError

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15.0
GET_RETRIES = 2
RETRY_BACKOFF_SECONDS = 0.5

TAXONOMY_PATHS = {
    "foods": "/api/foods",
    "units": "/api/units",
    "tags": "/api/organizers/tags",
    "categories": "/api/organizers/categories",
    "tools": "/api/organizers/tools",
}


class MealieClient:
    """Thin async wrapper over Mealie's REST API.

    Also owns two process-lifetime caches: recipe slug -> UUID, and taxonomy
    name -> object. Both exist because meal planning re-resolves the same
    handful of recipes and tags over and over.
    """

    def __init__(self, url: str, token: str, verify_ssl: bool = True) -> None:
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

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ HTTP

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        not_found: str | None = None,
    ) -> Any:
        """Issue a request and return decoded JSON, or raise ToolError.

        `not_found` is the message used for a 404, e.g. "recipe 'x' not found".
        """
        method = method.upper()
        attempts = GET_RETRIES + 1 if method == "GET" else 1
        params = {k: v for k, v in (params or {}).items() if v is not None}

        last_transport_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = await self._http.request(method, path, params=params, json=json)
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
            raise ToolError(f"Mealie returned {status} — the server is unhealthy")
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
        """Resolve a recipe slug to its UUID, caching the result."""
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

        Mealie's RecipeTag/RecipeCategory require both name and slug, so a bare
        ["Vegan"] cannot be written through. Returns the objects plus the names
        that had to be created, so tools can report them.
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

    async def taxonomy_slugs(self, resource: str, names: list[str]) -> list[str]:
        """Map names to slugs for filtering. Unknown names slugify optimistically.

        Filtering by a tag that does not exist should return no recipes, not
        raise — so this never creates and never errors.
        """
        if not names:
            return []
        await self._load_taxonomy(resource)
        known = self._taxonomy[resource]
        return [
            item["slug"] if (item := known.get(name.casefold())) else slugify(name)
            for name in names
        ]

    async def _load_taxonomy(self, resource: str) -> None:
        if resource not in self._taxonomy:
            page = await self.request("GET", TAXONOMY_PATHS[resource], params={"perPage": 1000})
            self._taxonomy[resource] = {
                item["name"].casefold(): item for item in page.get("items", [])
            }

    def forget_taxonomy(self, resource: str) -> None:
        self._taxonomy.pop(resource, None)


def slugify(name: str) -> str:
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
