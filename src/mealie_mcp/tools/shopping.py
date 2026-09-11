"""Shopping list tools."""

from __future__ import annotations

from collections.abc import Callable

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .. import shape
from ..client import MealieClient

GetClient = Callable[[], MealieClient]


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
    async def add_shopping_item(list_id: str, item: str) -> dict:
        """Add a free-text item to a shopping list, e.g. "2 lemons"."""
        result = await get_client().request(
            "POST",
            "/api/households/shopping/items",
            json={"shoppingListId": list_id, "note": item, "quantity": 0},
        )
        return shape.shopping_item(result["createdItems"][0])

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
    async def delete_shopping_item(item_id: str) -> dict:
        """Remove one item from its shopping list."""
        await get_client().request(
            "DELETE",
            f"/api/households/shopping/items/{item_id}",
            not_found=f"shopping item {item_id!r} not found",
        )
        return {"deleted": item_id}

    @mcp.tool
    async def add_recipe_to_shopping_list(
        list_id: str, recipe_slug: str, quantity: int = 1
    ) -> dict:
        """Add a recipe's ingredients to a shopping list. quantity is how many batches."""
        client = get_client()
        recipe_id = await client.recipe_id(recipe_slug)
        book = await client.request(
            "POST",
            f"/api/households/shopping/lists/{list_id}/recipe/{recipe_id}",
            json={"recipeIncrementQuantity": quantity},
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
