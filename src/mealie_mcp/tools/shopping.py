"""Shopping list tools."""

from __future__ import annotations

from collections.abc import Callable

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .. import shape
from ..client import MealieClient

GetClient = Callable[[], MealieClient]

# One request carries the whole batch, so the cap only bounds a runaway call.
MAX_BATCH = 100


def _batch(value: str | list[str], field: str) -> list[str]:
    """Normalize a one-or-many argument to a non-empty, bounded list."""
    values = [value] if isinstance(value, str) else list(value)
    if not values:
        raise ToolError(f"{field} is empty — pass at least one")
    if len(values) > MAX_BATCH:
        raise ToolError(
            f"{field} has {len(values)} entries; the cap is {MAX_BATCH} per call. "
            "Split it across calls."
        )
    return values


def register(mcp: FastMCP, get_client: GetClient, read_only: bool) -> None:
    @mcp.tool
    async def list_shopping_lists(page: int = 1) -> dict:
        """List shopping lists with their names and ids."""
        page = max(page, 1)
        result = await get_client().request(
            "GET", "/api/households/shopping/lists", params={"page": page, "perPage": 50}
        )
        return shape.paginated(result, shape.shopping_list, page_number=page)

    @mcp.tool
    async def get_shopping_list(list_id: str) -> dict:
        """Get one shopping list's items."""
        book = await get_client().request(
            "GET",
            f"/api/households/shopping/lists/{list_id}",
            not_found=f"shopping list {list_id!r} not found",
        )
        items = [shape.shopping_item(i) for i in book.get("listItems") or []]
        return {**shape.shopping_list(book), "items": items, "count": len(items)}

    if read_only:
        return

    @mcp.tool
    async def create_shopping_list(name: str) -> dict:
        """Create a new, empty shopping list."""
        book = await get_client().request(
            "POST", "/api/households/shopping/lists", json={"name": name}
        )
        return shape.shopping_list(book)

    @mcp.tool
    async def add_shopping_item(list_id: str, item: str | list[str]) -> dict:
        """Add free-text items to a shopping list, e.g. "2 lemons".

        Pass a list to add many in one call: ["2 lemons", "1 bunch cilantro"].
        """
        if isinstance(item, str):
            result = await get_client().request(
                "POST",
                "/api/households/shopping/items",
                json={"shoppingListId": list_id, "note": item, "quantity": 0},
            )
            return shape.shopping_item(result["createdItems"][0])

        notes = _batch(item, "item")
        result = await get_client().request(
            "POST",
            "/api/households/shopping/items/create-bulk",
            json=[{"shoppingListId": list_id, "note": note, "quantity": 0} for note in notes],
        )
        created = [shape.shopping_item(i) for i in result.get("createdItems") or []]
        return {"items": created, "count": len(created)}

    @mcp.tool
    async def update_shopping_item(
        item_id: str, item: str | None = None, checked: bool | None = None
    ) -> dict:
        """Edit an item's text or checked state. Only fields you pass change."""
        if item is None and checked is None:
            raise ToolError("pass at least one field to change")

        client = get_client()
        # PUT is a full replace and requires shoppingListId, so patch onto the
        # current row rather than guessing at what it needs.
        current = await client.request(
            "GET",
            f"/api/households/shopping/items/{item_id}",
            not_found=f"shopping item {item_id!r} not found",
        )
        if item is not None:
            current["note"] = item
            current["quantity"] = 0
        if checked is not None:
            current["checked"] = checked

        result = await client.request(
            "PUT",
            f"/api/households/shopping/items/{item_id}",
            json=current,
            not_found=f"shopping item {item_id!r} not found",
        )
        return shape.shopping_item(result["updatedItems"][0])

    @mcp.tool
    async def delete_shopping_item(item_id: str | list[str]) -> dict:
        """Remove items from their shopping list by item_id.

        Pass a list of ids to clear many in one call, e.g. everything checked
        off after a shop.
        """
        if isinstance(item_id, str):
            await get_client().request(
                "DELETE",
                f"/api/households/shopping/items/{item_id}",
                not_found=f"shopping item {item_id!r} not found",
            )
            return {"deleted": item_id}

        ids = _batch(item_id, "item_id")
        await get_client().request("DELETE", "/api/households/shopping/items", params={"ids": ids})
        return {"deleted": ids, "count": len(ids)}

    @mcp.tool
    async def add_recipe_to_shopping_list(
        list_id: str, recipe_slug: str | list[str], quantity: int = 1
    ) -> dict:
        """Add recipes' ingredients to a shopping list. quantity is how many batches of each.

        Pass a list of slugs to add a week of recipes in one call; Mealie
        merges matching ingredients across them.
        """
        slugs = _batch(recipe_slug, "recipe_slug")
        client = get_client()
        # Resolve every slug before writing, so a typo fails the call instead
        # of leaving a list with half the recipes on it.
        recipe_ids = [await client.recipe_id(slug) for slug in slugs]
        # The list endpoint, not /recipe/{id}: that one is deprecated in Mealie
        # and takes a single recipe per request.
        book = await client.request(
            "POST",
            f"/api/households/shopping/lists/{list_id}/recipe",
            json=[{"recipeId": rid, "recipeIncrementQuantity": quantity} for rid in recipe_ids],
            not_found=f"shopping list {list_id!r} not found",
        )
        items = book.get("listItems") or []
        return {**shape.shopping_list(book), "count": len(items)}

    @mcp.tool
    async def delete_shopping_list(list_id: str, confirm_list_id: str) -> dict:
        """Permanently delete a shopping list and its items. Pass the id twice to confirm."""
        if list_id != confirm_list_id:
            raise ToolError(
                f"confirm_list_id {confirm_list_id!r} does not match list_id {list_id!r} "
                "— nothing deleted"
            )
        await get_client().request(
            "DELETE",
            f"/api/households/shopping/lists/{list_id}",
            not_found=f"shopping list {list_id!r} not found",
        )
        return {"deleted": list_id}
