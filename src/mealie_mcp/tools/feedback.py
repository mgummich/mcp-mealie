"""Cooking history: last-made, timeline, ratings, and comments."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .. import shape
from ..client import MealieClient, MealieError

GetClient = Callable[[], MealieClient]

TIMELINE_PAGE_SIZE = 50


def _timestamp(when: str | None) -> str:
    if when is None:
        dt = datetime.now(UTC)
    else:
        try:
            dt = datetime.fromisoformat(when)
        except ValueError:
            raise ToolError(f"when must be an ISO date or datetime (got {when!r})") from None
        dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def register(mcp: FastMCP, get_client: GetClient, read_only: bool) -> None:
    @mcp.tool
    async def get_recipe_timeline(slug: str, page: int = 1) -> dict:
        """Get a recipe's cooking history: system, info, and comment events."""
        page = max(page, 1)
        client = get_client()
        recipe_id = await client.recipe_id(slug)
        result = await client.request(
            "GET",
            "/api/recipes/timeline/events",
            params={
                "queryFilter": f"recipeId={recipe_id}",
                "page": page,
                "perPage": TIMELINE_PAGE_SIZE,
            },
        )
        return shape.paginated(result, shape.timeline_event, page_number=page)

    @mcp.tool
    async def get_recipe_rating(slug: str) -> dict:
        """Get your own rating and favorite flag for a recipe."""
        client = get_client()
        recipe_id = await client.recipe_id(slug)
        try:
            result = await client.request("GET", f"/api/users/self/ratings/{recipe_id}")
        except MealieError as exc:
            # Mealie 404s a recipe this user has never rated. That is an
            # answer, not a failure — the slug itself already resolved.
            if exc.status != 404:
                raise
            result = {}
        return {"slug": slug, "rating": result.get("rating"), "favorite": result.get("isFavorite")}

    @mcp.tool
    async def get_recipe_comments(slug: str) -> dict:
        """List the comments on a recipe."""
        result = await get_client().request(
            "GET", f"/api/recipes/{slug}/comments", not_found=f"recipe {slug!r} not found"
        )
        items = [shape.recipe_comment(c) for c in result or []]
        return {"items": items, "count": len(items)}

    if read_only:
        return

    @mcp.tool
    async def mark_recipe_made(slug: str, when: str | None = None) -> dict:
        """Record that a recipe was cooked. when is an ISO date/datetime; default is now."""
        timestamp = _timestamp(when)
        await get_client().request(
            "PATCH",
            f"/api/recipes/{slug}/last-made",
            json={"timestamp": timestamp},
            not_found=f"recipe {slug!r} not found",
        )
        return {"slug": slug, "last_made": timestamp}

    @mcp.tool
    async def rate_recipe(
        slug: str, rating: float | None = None, favorite: bool | None = None
    ) -> dict:
        """Set your rating (0-5, 0 removes it) and/or favorite flag for a recipe."""
        if rating is None and favorite is None:
            raise ToolError("pass at least one field to change")
        if rating is not None and not 0 <= rating <= 5:
            raise ToolError(f"rating must be between 0 and 5 (got {rating!r})")

        client = get_client()
        user_id = await client.user_id()
        payload: dict = {}
        result: dict = {"slug": slug}
        if rating is not None:
            payload["rating"] = rating
            result["rating"] = rating
        if favorite is not None:
            payload["isFavorite"] = favorite
            result["favorite"] = favorite

        await client.request(
            "POST",
            f"/api/users/{user_id}/ratings/{slug}",
            json=payload,
            not_found=f"recipe {slug!r} not found",
        )
        return result

    @mcp.tool
    async def add_recipe_comment(slug: str, text: str) -> dict:
        """Add a comment to a recipe."""
        client = get_client()
        recipe_id = await client.recipe_id(slug)
        comment = await client.request(
            "POST", "/api/comments", json={"recipeId": recipe_id, "text": text}
        )
        return shape.recipe_comment(comment)

    @mcp.tool
    async def delete_recipe_comment(comment_id: str) -> dict:
        """Delete a comment by its comment_id (from get_recipe_comments)."""
        await get_client().request(
            "DELETE",
            f"/api/comments/{comment_id}",
            not_found=f"comment {comment_id!r} not found",
        )
        return {"deleted": comment_id}
