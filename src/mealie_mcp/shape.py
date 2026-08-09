"""Pure functions from Mealie JSON to trimmed dicts.

No network, no I/O. A full Mealie recipe costs 2-3k tokens; almost all of it is
nutrition blocks, assets, settings, comments, and nested IDs that an agent never
reads. This module is where that gets cut, and where the tests concentrate.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _clean(data: dict) -> dict:
    """Drop keys that are None, empty string, or empty list."""
    return {k: v for k, v in data.items() if v not in (None, "", [], {})}


def recipe_summary(recipe: dict) -> dict:
    """Shape one search/list result: enough to pick a recipe and fetch it.

    Args:
        recipe: A raw Mealie recipe object (full or summary form).

    Returns:
        Dict with slug, name, and description; empty fields dropped.
    """
    return _clean(
        {
            "slug": recipe.get("slug"),
            "name": recipe.get("name"),
            "description": recipe.get("description"),
        }
    )


def ingredient(item: dict) -> str | dict:
    """Flatten one ingredient to a display string.

    Prefers Mealie's own precomputed "display" field; falls back to
    assembling quantity, unit, food, and note by hand.

    Args:
        item: A raw RecipeIngredient object, or already a plain string.

    Returns:
        A human-readable line like "2 cup flour"; empty string if the
        ingredient carries no text at all.
    """
    if isinstance(item, str):
        return item

    display = item.get("display")
    if display:
        return display

    food = (item.get("food") or {}).get("name")
    unit = (item.get("unit") or {}).get("name")
    parts = [
        _format_quantity(item.get("quantity")),
        unit,
        food,
        item.get("note"),
    ]
    text = " ".join(p for p in parts if p)
    return text or item.get("originalText") or ""


def _format_quantity(quantity: Any) -> str | None:
    if quantity in (None, 0):
        return None
    if isinstance(quantity, float) and quantity.is_integer():
        return str(int(quantity))
    return str(quantity)


def instruction(step: dict) -> str:
    """Flatten one instruction step to "title: text", or just the text.

    Args:
        step: A raw RecipeStep object, or already a plain string.

    Returns:
        The step as a single string.
    """
    if isinstance(step, str):
        return step
    title = (step.get("title") or "").strip()
    text = (step.get("text") or "").strip()
    return f"{title}: {text}" if title else text


def names(items: list[dict] | None) -> list[str]:
    """Extract the "name" field from a list of taxonomy objects.

    Args:
        items: Raw tag/category/tool objects; None tolerated.

    Returns:
        The non-empty names, in order.
    """
    return [i["name"] for i in (items or []) if isinstance(i, dict) and i.get("name")]


def recipe_detail(recipe: dict) -> dict:
    """Shape the curated read view of one recipe.

    This is the cut that turns a 2-3k-token payload into a few hundred.
    The raw payload is still reachable via get_recipe(full=true).

    Args:
        recipe: A raw Mealie recipe object.

    Returns:
        Dict with name, slug, times, flattened ingredients/instructions,
        tag/category names, source URL, rating, and notes; empty fields
        dropped.
    """
    return _clean(
        {
            "name": recipe.get("name"),
            "slug": recipe.get("slug"),
            "description": recipe.get("description"),
            "yield": recipe.get("recipeYield"),
            "prep_time": recipe.get("prepTime"),
            "cook_time": recipe.get("cookTime"),
            "total_time": recipe.get("totalTime"),
            "ingredients": [ingredient(i) for i in recipe.get("recipeIngredient") or []],
            "instructions": [instruction(s) for s in recipe.get("recipeInstructions") or []],
            "tags": names(recipe.get("tags")),
            "categories": names(recipe.get("recipeCategory")),
            "source_url": recipe.get("orgURL"),
            "rating": recipe.get("rating"),
            "notes": [
                _clean({"title": n.get("title"), "text": n.get("text")})
                for n in recipe.get("notes") or []
            ],
        }
    )


def meal_plan_entry(entry: dict) -> dict:
    """Shape one meal plan entry.

    Args:
        entry: A raw ReadPlanEntry object.

    Returns:
        Dict with entry_id, date, meal type, and either the linked recipe's
        slug and name or the free-text title; empty fields dropped.
    """
    recipe = entry.get("recipe") or {}
    return _clean(
        {
            "entry_id": entry.get("id"),
            "date": entry.get("date"),
            "meal": entry.get("entryType"),
            # A plan entry is either a linked recipe or a free-text title.
            "recipe_slug": recipe.get("slug"),
            "name": recipe.get("name") or entry.get("title"),
            "note": entry.get("text"),
        }
    )


def cookbook(book: dict) -> dict:
    """Shape one cookbook.

    Args:
        book: A raw ReadCookBook object.

    Returns:
        Dict with cookbook_id, name, slug, description, query_filter, and
        public flag; empty fields dropped.
    """
    return _clean(
        {
            "cookbook_id": book.get("id"),
            "name": book.get("name"),
            "slug": book.get("slug"),
            "description": book.get("description"),
            "query_filter": book.get("queryFilterString"),
            "public": book.get("public"),
        }
    )


def parsed_ingredient(parsed: dict) -> dict:
    """Shape one result from Mealie's ingredient parser.

    Args:
        parsed: A raw ParsedIngredient object.

    Returns:
        Dict with the original input line, quantity, unit and food names,
        note, and average confidence; empty fields dropped.
    """
    item = parsed.get("ingredient") or {}
    return _clean(
        {
            "input": parsed.get("input"),
            "quantity": item.get("quantity"),
            "unit": (item.get("unit") or {}).get("name"),
            "food": (item.get("food") or {}).get("name"),
            "note": item.get("note"),
            "confidence": (parsed.get("confidence") or {}).get("average"),
        }
    )


def taxonomy_item(item: dict) -> dict:
    """Shape one tag/category/food/unit/tool down to id, name, and slug.

    Args:
        item: A raw taxonomy object.

    Returns:
        Dict with id, name, and slug; empty fields dropped.
    """
    return _clean(
        {"id": item.get("id"), "name": item.get("name"), "slug": item.get("slug")}
    )


def paginated(page: dict, shaper: Callable[[dict], Any], *, page_number: int = 1) -> dict:
    """Shape a paginated response: items plus a hint, never the raw envelope.

    Args:
        page: A raw Mealie pagination envelope with "items" and "total".
        shaper: Function applied to each item, e.g. recipe_summary.
        page_number: The page that was requested, used to phrase the
            "pass page=N+1 for more" hint.

    Returns:
        Dict with items, count, and — when the server reports more rows than
        were returned — total and a one-line pagination note.
    """
    items = [shaper(i) for i in page.get("items") or []]
    total = page.get("total")
    result: dict[str, Any] = {"items": items, "count": len(items)}

    if isinstance(total, int):
        result["total"] = total
        if total > len(items):
            result["note"] = (
                f"showing {len(items)} of {total} — pass page={page_number + 1} for more"
            )
    return result
