"""Cookbook tools. A Mealie cookbook is a saved filter, not a folder of recipes."""

from __future__ import annotations

from collections.abc import Callable

from fastmcp import FastMCP

from .. import shape
from ..client import MealieClient

GetClient = Callable[[], MealieClient]

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
        query_filter: str | None = None,
        public: bool = False,
    ) -> dict:
        """Create a cookbook: a saved filter over recipes.

        query_filter uses Mealie's filter syntax. Worked examples:
          tags.name IN ["Dinner"]
          recipeCategory.name IN ["Dessert"] AND rating > 3
          tags.name CONTAINS ALL ["Vegan", "Quick"]
          createdAt > "2026-01-01" AND tools.name IN ["Air Fryer"]

        Leave query_filter empty for a cookbook you fill by hand in the UI.
        """
        payload = {
            "name": name,
            "description": description or "",
            "queryFilterString": query_filter or "",
            "public": public,
        }
        book = await get_client().request("POST", "/api/households/cookbooks", json=payload)
        return shape.cookbook(book)

    @mcp.tool
    async def delete_cookbook(cookbook_id: str) -> dict:
        """Delete a cookbook. The recipes it matched are not touched."""
        await get_client().request(
            "DELETE",
            f"/api/households/cookbooks/{cookbook_id}",
            not_found=f"cookbook {cookbook_id!r} not found",
        )
        return {"deleted": cookbook_id}
