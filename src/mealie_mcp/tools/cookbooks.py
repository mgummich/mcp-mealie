"""Cookbook tools. A Mealie cookbook is a saved filter, not a folder of recipes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .. import shape
from ..client import MealieClient

GetClient = Callable[[], MealieClient]

#: The field each taxonomy filters on inside a queryFilterString.
FILTER_FIELDS = {
    "tags": "tags.name",
    "categories": "recipeCategory.name",
    "tools": "tools.name",
}


async def build_filter(
    client: MealieClient,
    tags: list[str] | None,
    categories: list[str] | None,
    tools: list[str] | None,
    require_all: bool,
) -> str:
    """Assemble a queryFilterString from plain name lists.

    Names are resolved to their stored casing first: Mealie's filter parser
    matches names exactly, so ["vegan"] would otherwise build a filter that
    silently matches nothing.

    Args:
        client: Used to look up canonical names.
        tags: Tag names, or None.
        categories: Category names, or None.
        tools: Tool names, or None.
        require_all: Match recipes carrying every name (CONTAINS ALL) rather
            than any of them (IN).

    Returns:
        The filter string, or "" if no names were given.
    """
    operator = "CONTAINS ALL" if require_all else "IN"
    parts = []
    for resource, values in (("tags", tags), ("categories", categories), ("tools", tools)):
        if not values:
            continue
        names = await client.taxonomy_names(resource, values)
        listed = ", ".join(f'"{n.replace(chr(34), "")}"' for n in names)
        parts.append(f"{FILTER_FIELDS[resource]} {operator} [{listed}]")
    return " AND ".join(parts)


async def resolve_filter(
    client: MealieClient,
    query_filter: str | None,
    tags: list[str] | None,
    categories: list[str] | None,
    tools: list[str] | None,
    require_all: bool,
) -> str | None:
    """Pick the filter to write: the literal one, the built one, or neither.

    Raises:
        ToolError: If both a literal filter and name lists were given, since
            silently dropping one of them would be worse.
    """
    named = any((tags, categories, tools))
    if query_filter and named:
        raise ToolError(
            "pass either query_filter or tags/categories/tools, not both — "
            "drop query_filter to have it built for you"
        )
    if named:
        return await build_filter(client, tags, categories, tools, require_all)
    return query_filter


def register(mcp: FastMCP, get_client: GetClient, read_only: bool) -> None:
    @mcp.tool
    async def list_cookbooks() -> dict:
        """List cookbooks with their names, ids, and filters."""
        result = await get_client().request(
            "GET", "/api/households/cookbooks", params={"perPage": 200}
        )
        return shape.paginated(result, shape.cookbook)

    @mcp.tool
    async def get_cookbook_recipes(cookbook: str, page: int = 1, limit: int = 20) -> dict:
        """List the recipes a cookbook currently matches.

        `cookbook` is the cookbook's id or slug (from list_cookbooks).
        """
        result = await get_client().request(
            "GET",
            "/api/recipes",
            params={"cookbook": cookbook, "page": page, "perPage": limit},
        )
        return shape.paginated(result, shape.recipe_summary, page_number=page)

    if read_only:
        return

    @mcp.tool
    async def create_cookbook(
        name: str,
        description: str | None = None,
        tags: list[str] | None = None,
        categories: list[str] | None = None,
        tools: list[str] | None = None,
        require_all: bool = False,
        query_filter: str | None = None,
        public: bool = False,
    ) -> dict:
        """Create a cookbook: a saved filter over recipes.

        Prefer tags/categories/tools: pass plain names and the filter string
        is built for you, with require_all switching from any-of to all-of.
        Names are matched to Mealie's stored casing on the way in.

        query_filter is the escape hatch for filters the name lists cannot
        express — dates, ratings, mixed operators. It cannot be combined with
        the name lists.

        query_filter uses Mealie's filter syntax. Worked examples:
          tags.name IN ["Dinner"]
          recipeCategory.name IN ["Dessert"] AND rating > 3
          tags.name CONTAINS ALL ["Vegan", "Quick"]
          createdAt > "2026-01-01" AND tools.name IN ["Air Fryer"]

        Leave everything but name empty for a cookbook you fill by hand in
        the UI.
        """
        client = get_client()
        resolved = await resolve_filter(client, query_filter, tags, categories, tools, require_all)
        payload = {
            "name": name,
            "description": description or "",
            "queryFilterString": resolved or "",
            "public": public,
        }
        book = await client.request("POST", "/api/households/cookbooks", json=payload)
        return shape.cookbook(book)

    @mcp.tool
    async def update_cookbook(
        cookbook_id: str,
        name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        categories: list[str] | None = None,
        tools: list[str] | None = None,
        require_all: bool = False,
        query_filter: str | None = None,
        public: bool | None = None,
    ) -> dict:
        """Rename or re-filter an existing cookbook. Only fields you pass change.

        Use this instead of delete plus create: the cookbook keeps its id, so
        anything pointing at it still works.

        The filter arguments behave exactly as in create_cookbook — names in
        tags/categories/tools, or a literal query_filter, never both. Passing
        any of them replaces the whole filter; pass query_filter="" to clear
        it.
        """
        client = get_client()
        missing = f"cookbook {cookbook_id!r} not found"
        # Mealie's PUT replaces the whole row, so patch onto the current one.
        current = await client.request(
            "GET", f"/api/households/cookbooks/{cookbook_id}", not_found=missing
        )
        resolved = await resolve_filter(client, query_filter, tags, categories, tools, require_all)

        payload: dict[str, Any] = {**(current or {})}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if public is not None:
            payload["public"] = public
        if resolved is not None:
            payload["queryFilterString"] = resolved

        updated = await client.request(
            "PUT", f"/api/households/cookbooks/{cookbook_id}", json=payload, not_found=missing
        )
        return shape.cookbook(updated or payload)

    @mcp.tool
    async def delete_cookbook(cookbook_id: str) -> dict:
        """Delete a cookbook. The recipes it matched are not touched."""
        await get_client().request(
            "DELETE",
            f"/api/households/cookbooks/{cookbook_id}",
            not_found=f"cookbook {cookbook_id!r} not found",
        )
        return {"deleted": cookbook_id}
