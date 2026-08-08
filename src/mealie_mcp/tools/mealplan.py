"""Meal plan tools."""

from __future__ import annotations

from collections.abc import Callable

# Aliased because add_meal_plan_entry has a `date` parameter (part of the tool
# schema, so not renameable) that would otherwise shadow the class.
from datetime import date as date_type
from datetime import timedelta
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .. import shape
from ..client import MealieClient

GetClient = Callable[[], MealieClient]

ENTRY_TYPES = ("breakfast", "lunch", "dinner", "side")
MAX_RANDOM_DAYS = 14


def _as_date(value: str, field: str) -> date_type:
    try:
        return date_type.fromisoformat(value)
    except (TypeError, ValueError):
        raise ToolError(f"{field} must be an ISO date like 2026-08-09 (got {value!r})") from None


def _today() -> str:
    """Resolve "today" on the host running this server.

    The server runs on the user's own machine, so local time is right in
    practice; the tool echoes this date back so a mismatch is visible.
    """
    return date_type.today().isoformat()  # noqa: DTZ011


def _check_entry_type(entry_type: str) -> str:
    if entry_type not in ENTRY_TYPES:
        raise ToolError(f"entry_type must be one of {', '.join(ENTRY_TYPES)} (got {entry_type!r})")
    return entry_type


def register(mcp: FastMCP, get_client: GetClient, read_only: bool) -> None:
    @mcp.tool
    async def get_meal_plan(start_date: str, end_date: str) -> dict:
        """Get planned meals between two ISO dates, inclusive."""
        start, end = _as_date(start_date, "start_date"), _as_date(end_date, "end_date")
        if end < start:
            raise ToolError("end_date is before start_date")

        result = await get_client().request(
            "GET",
            "/api/households/mealplans",
            params={
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "perPage": 200,
            },
        )
        return shape.paginated(result, shape.meal_plan_entry)

    @mcp.tool
    async def get_todays_meals() -> dict:
        """Get what is planned for today.

        The date is resolved on the machine running this server, and echoed
        back so a timezone mismatch is visible rather than silent.
        """
        result = await get_client().request("GET", "/api/households/mealplans/today")
        entries = [shape.meal_plan_entry(e) for e in result or []]
        return {"date": _today(), "items": entries, "count": len(entries)}

    if read_only:
        return

    @mcp.tool
    async def add_meal_plan_entry(
        date: str,
        entry_type: str = "dinner",
        recipe_slug: str | None = None,
        title: str | None = None,
        note: str | None = None,
    ) -> dict:
        """Plan one meal on one day.

        Pass recipe_slug to link an existing recipe, or title for a free-text
        entry like "leftovers". entry_type is breakfast, lunch, dinner, or side.
        """
        if not recipe_slug and not title:
            raise ToolError("pass either recipe_slug or title")

        client = get_client()
        payload: dict[str, Any] = {
            "date": _as_date(date, "date").isoformat(),
            "entryType": _check_entry_type(entry_type),
        }
        if recipe_slug:
            # CreatePlanEntry wants a UUID, not a slug; the client caches these
            # because planning a week re-resolves the same recipes repeatedly.
            payload["recipeId"] = await client.recipe_id(recipe_slug)
        if title:
            payload["title"] = title
        if note:
            payload["text"] = note

        entry = await client.request("POST", "/api/households/mealplans", json=payload)
        return shape.meal_plan_entry(entry)

    @mcp.tool
    async def delete_meal_plan_entry(entry_id: str | int) -> dict:
        """Remove one planned meal by its entry_id (from get_meal_plan)."""
        await get_client().request(
            "DELETE",
            f"/api/households/mealplans/{entry_id}",
            not_found=f"meal plan entry {entry_id!r} not found",
        )
        return {"deleted": entry_id}

    @mcp.tool
    async def random_meal_plan(
        start_date: str, end_date: str, entry_type: str = "dinner"
    ) -> dict:
        """Fill a date range with random recipes, one meal per day.

        Honors any meal plan rules configured in Mealie. The range is capped at
        14 days. Existing entries are not replaced — this adds to them.
        """
        start, end = _as_date(start_date, "start_date"), _as_date(end_date, "end_date")
        if end < start:
            raise ToolError("end_date is before start_date")
        days = (end - start).days + 1
        if days > MAX_RANDOM_DAYS:
            raise ToolError(
                f"range covers {days} days; the cap is {MAX_RANDOM_DAYS}. "
                "Call again for later dates."
            )

        _check_entry_type(entry_type)
        client = get_client()
        # The API creates one random entry per request, so a week is 7 POSTs.
        # Looping here keeps it to a single tool call for the model. A mid-loop
        # failure must not hide the entries already written — report them.
        added = []
        failed: str | None = None
        for offset in range(days):
            day = (start + timedelta(days=offset)).isoformat()
            try:
                entry = await client.request(
                    "POST",
                    "/api/households/mealplans/random",
                    json={"date": day, "entryType": entry_type},
                )
            except ToolError as exc:
                failed = f"stopped at {day}: {exc}"
                break
            added.append(shape.meal_plan_entry(entry))

        if failed and not added:
            raise ToolError(failed)
        result: dict[str, Any] = {"items": added, "count": len(added)}
        if failed:
            result["failed"] = failed
        return result
