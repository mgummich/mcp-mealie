"""Ingredient parsing and taxonomy management.

Taxonomy is the one grouped tool: five resources times three actions is
fifteen tools nobody reaches for often enough to deserve the room.
"""

from __future__ import annotations

from collections.abc import Callable

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .. import shape
from ..client import TAXONOMY_PATHS, MealieClient

GetClient = Callable[[], MealieClient]

RESOURCES = tuple(TAXONOMY_PATHS)
ACTIONS = ("list", "create", "delete")


def register(mcp: FastMCP, get_client: GetClient, read_only: bool) -> None:
    @mcp.tool
    async def parse_ingredients(lines: list[str]) -> dict:
        """Parse free-text ingredient lines into quantity, unit, and food.

        Useful for inspecting how Mealie will read a list. create_recipe
        accepts plain text directly, so this is not required before writing.
        """
        results = await get_client().request(
            "POST", "/api/parser/ingredients", json={"ingredients": lines}
        )
        return {"items": [shape.parsed_ingredient(r) for r in results or []]}

    allowed_actions = ("list",) if read_only else ACTIONS

    @mcp.tool
    async def manage_taxonomy(
        resource: str,
        action: str = "list",
        name: str | None = None,
        item_id: str | None = None,
        search: str | None = None,
    ) -> dict:
        """List, create, or delete Mealie's organizing entities.

        resource: foods, units, tags, categories, or tools
        action:   list (default), create (needs name), delete (needs item_id)

        Recipe tools accept tag and category names directly and create them as
        needed, so this is mostly for tidying up.
        """
        if resource not in RESOURCES:
            raise ToolError(f"resource must be one of {', '.join(RESOURCES)} (got {resource!r})")
        if action not in allowed_actions:
            if action in ACTIONS:
                raise ToolError("server is in read-only mode")
            raise ToolError(f"action must be one of {', '.join(allowed_actions)}")

        client = get_client()
        path = TAXONOMY_PATHS[resource]

        if action == "list":
            page = await client.request(
                "GET", path, params={"search": search, "perPage": 200}
            )
            return shape.paginated(page, shape.taxonomy_item)

        if action == "create":
            if not name:
                raise ToolError("create requires a name")
            created = await client.request("POST", path, json={"name": name})
            client.forget_taxonomy(resource)
            return shape.taxonomy_item(created)

        if not item_id:
            raise ToolError("delete requires an item_id (get it from action='list')")
        await client.request(
            "DELETE", f"{path}/{item_id}", not_found=f"{resource} item {item_id!r} not found"
        )
        client.forget_taxonomy(resource)
        return {"deleted": item_id}
