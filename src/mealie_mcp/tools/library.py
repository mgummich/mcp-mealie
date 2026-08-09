"""Whole-library reports: usage rollups, duplicate names, dead source links.

Everything here sweeps the recipe list rather than answering from a single
endpoint, because Mealie has no "how many recipes use this food" API. The
point is that the sweep happens once, server-side, instead of the model
issuing one search per name.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable
from functools import partial
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from ..client import MAX_LIBRARY_RECIPES, MealieClient, map_concurrent

GetClient = Callable[[], MealieClient]

#: Resources library_stats can count usage for.
STATS_RESOURCES = ("tags", "categories", "tools", "foods", "units")
#: Where each of those lives on a recipe summary; foods and units are absent
#: from summaries and have to come out of the ingredient list instead.
SUMMARY_KEYS = {"tags": "tags", "categories": "recipeCategory", "tools": "tools"}

#: Default ceiling for check_recipe_links — every source URL is one outbound
#: request to somebody else's server.
MAX_LINK_CHECKS = 200
#: How many used items library_stats lists before it stops; the rest are a
#: count. Every row carries a UUID, and a 300-tag table is thousands of tokens.
TOP_USED = 50
LINK_TIMEOUT_SECONDS = 10.0
#: Statuses that mean "this host will not answer a probe", not "dead link".
UNVERIFIABLE_STATUSES = {401, 403, 405, 429, 501}


def normalize_name(name: str | None) -> str:
    """Fold a recipe name to its comparison key: lowercase, alphanumeric only.

    "Grandma's Chili!" and "grandma s chili" both become "grandma s chili",
    which is the point — imports from two sites rarely agree on punctuation.

    Args:
        name: A recipe name; None tolerated.

    Returns:
        The normalized key, possibly empty.
    """
    return re.sub(r"[^a-z0-9]+", " ", (name or "").casefold()).strip()


def register(mcp: FastMCP, get_client: GetClient, read_only: bool) -> None:
    @mcp.tool
    async def library_stats(
        resource: str,
        include_unused: bool = True,
        top: int = TOP_USED,
        max_recipes: int = MAX_LIBRARY_RECIPES,
    ) -> dict:
        """Count how many recipes use each tag, category, tool, food, or unit.

        One call instead of one search per name. This is what answers "which
        tags are unused", "what is my most-used food", and "is this safe to
        delete". Items come back sorted by recipe_count, highest first.

        At most top used and top unused items are listed — raise top for more,
        and read "used" and "unused" for how many there are in total. Pass
        include_unused=false to leave the unused ones out entirely.

        resource: tags, categories, tools, foods, or units.

        tags, categories, and tools are read straight off the recipe list — a
        handful of requests. foods and units are not in that payload, so they
        need one request per recipe; that sweep is slower and honors
        max_recipes. A recipe using the same food twice still counts once.
        """
        if resource not in STATS_RESOURCES:
            raise ToolError(
                f"resource must be one of {', '.join(STATS_RESOURCES)} (got {resource!r})"
            )

        client = get_client()
        recipes, total = await client.all_recipes(max_recipes=max_recipes)

        counts: Counter[str] = Counter()
        failed = 0
        if resource in SUMMARY_KEYS:
            key = SUMMARY_KEYS[resource]
            for recipe in recipes:
                counts.update({i["id"] for i in recipe.get(key) or [] if i.get("id")})
        else:
            singular = resource[:-1]  # foods -> food, units -> unit
            slugs = [recipe["slug"] for recipe in recipes if recipe.get("slug")]
            for detail in await client.recipe_details(slugs):
                if detail is None:
                    failed += 1
                    continue
                counts.update(
                    {
                        entity["id"]
                        for item in detail.get("recipeIngredient") or []
                        if isinstance(entity := item.get(singular), dict) and entity.get("id")
                    }
                )

        known = {
            i["id"]: i.get("name") for i in await client.taxonomy_items(resource) if i.get("id")
        }

        rows = [
            {"id": item_id, "name": name, "recipe_count": counts.get(item_id, 0)}
            for item_id, name in known.items()
        ]
        # A count against an id the taxonomy list didn't return means the two
        # views disagree; surfacing it beats dropping the usage silently.
        rows += [
            {"id": item_id, "name": None, "recipe_count": count}
            for item_id, count in counts.items()
            if item_id not in known
        ]
        rows.sort(key=lambda r: (-r["recipe_count"], (r["name"] or "").casefold()))
        used = [r for r in rows if r["recipe_count"]]
        unused = [r for r in rows if not r["recipe_count"]] if include_unused else []

        # The long tail of the used list is what nobody reads: "my most-used
        # food" is answered by the first few rows, and each row carries a UUID.
        # The unused list is the one people act on, so it gets its own budget
        # rather than sharing — but it is bounded too: 193 unused tags is
        # 15k characters, and nothing is deleted 193 at a time.
        shown = used[:top] + unused[:top]

        result = {
            "resource": resource,
            "items": shown,
            "count": len(shown),
            "used": len(used),
            "unused": sum(1 for r in rows if not r["recipe_count"]),
            "recipes_scanned": len(recipes),
        }
        notes = []
        if len(used) > top:
            notes.append(f"showing the {top} most-used of {len(used)} — raise top for the rest")
        if len(unused) > top:
            notes.append(f"showing {top} of {len(unused)} unused — raise top for the rest")
        if len(recipes) < total:
            notes.append(
                f"scanned {len(recipes)} of {total} recipes — raise max_recipes for the rest"
            )
        if failed:
            notes.append(f"{failed} recipes could not be read; their ingredients are not counted")
        if notes:
            result["note"] = "; ".join(notes)
        return result

    @mcp.tool
    async def find_duplicate_recipes(max_recipes: int = MAX_LIBRARY_RECIPES) -> dict:
        """Group recipes whose names match once punctuation and case are ignored.

        Catches the same recipe imported twice from two sites. Names only —
        two genuinely different takes on "Pancakes" show up here too, so read
        the group before deleting anything.
        """
        recipes, total = await get_client().all_recipes(max_recipes=max_recipes)

        by_key: dict[str, list[dict]] = defaultdict(list)
        for recipe in recipes:
            key = normalize_name(recipe.get("name"))
            if key:
                by_key[key].append({"slug": recipe.get("slug"), "name": recipe.get("name")})

        groups: list[dict[str, Any]] = [
            {"name": key, "count": len(members), "recipes": members}
            for key, members in by_key.items()
            if len(members) > 1
        ]
        groups.sort(key=lambda g: (-g["count"], g["name"]))

        result = {
            "groups": groups,
            "count": len(groups),
            "duplicate_recipes": sum(g["count"] for g in groups),
            "recipes_scanned": len(recipes),
        }
        if len(recipes) < total:
            result["note"] = (
                f"scanned {len(recipes)} of {total} recipes — raise max_recipes for the rest"
            )
        return result

    @mcp.tool
    async def check_recipe_links(
        check_sources: bool = True,
        check_images: bool = True,
        max_recipes: int = MAX_LINK_CHECKS,
    ) -> dict:
        """Find recipes with a dead source URL or no image.

        Source URLs are probed with HEAD (a GET fallback for hosts that refuse
        HEAD), unauthenticated — your Mealie token is never sent to a third
        party. A host that answers 401/403/405/429/501 is reported as
        unverified, not broken.

        Images are not probed: Mealie downloads and stores them itself, so
        there is nothing to rot. What this reports instead is recipes that
        have no image at all — the ones a scrape left blank.
        """
        client = get_client()
        recipes, total = await client.all_recipes(max_recipes=max_recipes)

        missing_images = (
            [{"slug": r.get("slug"), "name": r.get("name")} for r in recipes if not r.get("image")]
            if check_images
            else []
        )

        broken: list[dict] = []
        unverified: list[dict] = []
        with_source = [r for r in recipes if r.get("orgURL")] if check_sources else []
        if with_source:
            async with httpx.AsyncClient(
                timeout=LINK_TIMEOUT_SECONDS, follow_redirects=True
            ) as http:
                probes = await map_concurrent(
                    [partial(_probe, http, r["orgURL"]) for r in with_source]
                )
            for recipe, probe in zip(with_source, probes, strict=True):
                if isinstance(probe, BaseException):
                    probe = {"status": None, "error": type(probe).__name__}
                entry = {
                    "slug": recipe.get("slug"),
                    "name": recipe.get("name"),
                    "source_url": recipe["orgURL"],
                    **probe,
                }
                if probe["status"] in UNVERIFIABLE_STATUSES:
                    unverified.append(entry)
                elif probe["status"] is None or probe["status"] >= 400:
                    broken.append(entry)

        result = {
            "sources_checked": len(with_source),
            "broken_sources": broken,
            "unverified_sources": unverified,
            "missing_images": missing_images,
            "recipes_scanned": len(recipes),
        }
        if len(recipes) < total:
            result["note"] = (
                f"scanned {len(recipes)} of {total} recipes — raise max_recipes for the rest"
            )
        return result


async def _probe(http: httpx.AsyncClient, url: str) -> dict:
    """HEAD one URL, falling back to a streamed GET for hosts that refuse it."""
    try:
        response = await http.head(url)
        if response.status_code in UNVERIFIABLE_STATUSES:
            # Plenty of sites 403 a HEAD and serve the GET fine. Stream it so
            # the body is never downloaded.
            async with http.stream("GET", url) as streamed:
                return {"status": streamed.status_code}
        return {"status": response.status_code}
    except httpx.HTTPError as exc:
        return {"status": None, "error": type(exc).__name__}
