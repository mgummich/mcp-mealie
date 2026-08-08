"""Recipe tools: search, read, create, update, delete, import, suggest."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .. import shape
from ..client import MealieClient

GetClient = Callable[[], MealieClient]


def _normalize_ingredient(item: dict, original: str = "") -> dict:
    """Coerce one ingredient into something Mealie's DB layer accepts.

    IngredientFood/IngredientUnit require an id, and the parser happily returns
    foods it did not match to an existing row. Rather than silently creating
    taxonomy for every ingredient, unresolved names fold back into the note so
    no information is lost.
    """
    food, unit = item.get("food"), item.get("unit")
    payload: dict[str, Any] = {"quantity": item.get("quantity") or 0}

    unresolved: list[str] = []
    for value, key in ((unit, "unit"), (food, "food")):
        if isinstance(value, dict) and value.get("id"):
            payload[key] = value
        elif isinstance(value, dict) and value.get("name"):
            unresolved.append(value["name"])
        elif isinstance(value, str):
            unresolved.append(value)

    note = " ".join(p for p in [*unresolved, item.get("note") or ""] if p).strip()
    payload["note"] = note
    payload["originalText"] = item.get("originalText") or original or note
    # Mealie mints reference ids client-side; a null one fails validation.
    payload["referenceId"] = item.get("referenceId") or str(uuid.uuid4())
    if item.get("title"):
        payload["title"] = item["title"]
    return payload


def _instruction_payload(steps: list[Any]) -> list[dict]:
    """A step needs more than text: the ORM requires ingredientReferences."""
    out = []
    for step in steps:
        if isinstance(step, dict):
            out.append(
                {
                    "title": step.get("title") or "",
                    "text": step.get("text") or "",
                    "ingredientReferences": step.get("ingredientReferences") or [],
                }
            )
        else:
            out.append({"title": "", "text": str(step), "ingredientReferences": []})
    return out


async def _ingredient_payload(client: MealieClient, items: list[Any]) -> list[dict]:
    """Accept plain strings or structured objects, per item.

    A model writing from "two cups flour" produces strings; one that already
    called parse_ingredients produces dicts. Strings go through Mealie's parser
    so we never guess at its ingredient schema ourselves.
    """
    if not items:
        return []

    texts = [i for i in items if isinstance(i, str)]
    parsed_by_text: dict[str, dict] = {}
    if texts:
        results = await client.request(
            "POST", "/api/parser/ingredients", json={"ingredients": texts}
        )
        for text, result in zip(texts, results):
            parsed_by_text[text] = result.get("ingredient") or {"note": text}

    return [
        _normalize_ingredient(parsed_by_text[i], original=i)
        if isinstance(i, str)
        else _normalize_ingredient(i)
        for i in items
    ]


async def _fetch_recipe(client: MealieClient, slug: str, full: bool = False) -> dict:
    recipe = await client.request(
        "GET", f"/api/recipes/{slug}", not_found=f"recipe {slug!r} not found"
    )
    return recipe if full else shape.recipe_detail(recipe)


async def _taxonomy_payload(
    client: MealieClient, resource: str, names: list[str] | None
) -> tuple[list[dict] | None, list[str]]:
    if names is None:
        return None, []
    objects, created = await client.resolve_taxonomy(resource, names)
    return objects, created


def register(mcp: FastMCP, get_client: GetClient, read_only: bool) -> None:
    @mcp.tool
    async def search_recipes(
        query: str | None = None,
        tags: list[str] | None = None,
        categories: list[str] | None = None,
        tools: list[str] | None = None,
        require_all: bool = False,
        page: int = 1,
        limit: int = 20,
    ) -> dict:
        """Search recipes by text and/or filter by tags, categories, or tools.

        Returns slim results (slug, name, description). Use get_recipe with a
        slug for full detail. Filtering happens server-side, so prefer filters
        over fetching many recipes and sorting them yourself.
        """
        client = get_client()
        params: dict[str, Any] = {"search": query, "page": page, "perPage": limit}
        if tags:
            params["tags"] = await client.taxonomy_slugs("tags", tags)
            params["requireAllTags"] = require_all
        if categories:
            params["categories"] = await client.taxonomy_slugs("categories", categories)
            params["requireAllCategories"] = require_all
        if tools:
            params["tools"] = await client.taxonomy_slugs("tools", tools)
            params["requireAllTools"] = require_all

        result = await client.request("GET", "/api/recipes", params=params)
        return shape.paginated(result, shape.recipe_summary, page_number=page)

    @mcp.tool
    async def get_recipe(slug: str, full: bool = False) -> dict:
        """Get one recipe by slug.

        Returns a trimmed view by default: ingredients, instructions, times,
        tags, source, rating, notes. Pass full=true for the raw Mealie payload
        (nutrition, assets, settings) — it is several times larger.
        """
        return await _fetch_recipe(get_client(), slug, full)

    @mcp.tool
    async def suggest_recipes(
        foods: list[str] | None = None,
        tools: list[str] | None = None,
        max_missing_foods: int = 2,
        limit: int = 10,
    ) -> dict:
        """Suggest recipes cookable from ingredients on hand.

        Give the foods you have by name. Recipes needing up to
        max_missing_foods extra ingredients are still included, with the
        missing ones listed.
        """
        client = get_client()
        params: dict[str, Any] = {"limit": limit, "maxMissingFoods": max_missing_foods}
        if foods:
            resolved, _ = await client.resolve_taxonomy("foods", foods, create_missing=False)
            params["foods"] = [f["id"] for f in resolved]
        if tools:
            resolved, _ = await client.resolve_taxonomy("tools", tools, create_missing=False)
            params["tools"] = [t["id"] for t in resolved]

        result = await client.request("GET", "/api/recipes/suggestions", params=params)
        items = [
            {
                **shape.recipe_summary(entry.get("recipe") or {}),
                "missing_foods": shape.names(entry.get("missingFoods")),
                "missing_tools": shape.names(entry.get("missingTools")),
            }
            for entry in result.get("items") or []
        ]
        return {"items": items, "count": len(items)}

    if read_only:
        return

    @mcp.tool
    async def create_recipe(
        name: str,
        description: str | None = None,
        ingredients: list[Any] | None = None,
        instructions: list[str] | None = None,
        recipe_yield: str | None = None,
        prep_time: str | None = None,
        cook_time: str | None = None,
        tags: list[str] | None = None,
        categories: list[str] | None = None,
        source_url: str | None = None,
    ) -> dict:
        """Create a recipe.

        Ingredients may be plain text ("2 cups flour") or structured objects
        from parse_ingredients — mix freely. Tags and categories are given as
        plain names and created in Mealie if they do not exist yet.
        """
        client = get_client()

        # Mealie's POST accepts {name} only and returns a slug; everything else
        # has to land in a follow-up PATCH.
        slug = await client.request("POST", "/api/recipes", json={"name": name})
        if not isinstance(slug, str):
            slug = (slug or {}).get("slug")

        try:
            payload: dict[str, Any] = {}
            if description is not None:
                payload["description"] = description
            if recipe_yield is not None:
                payload["recipeYield"] = recipe_yield
            if prep_time is not None:
                payload["prepTime"] = prep_time
            if cook_time is not None:
                payload["cookTime"] = cook_time
            if source_url is not None:
                payload["orgURL"] = source_url
            if ingredients:
                payload["recipeIngredient"] = await _ingredient_payload(client, ingredients)
            if instructions:
                payload["recipeInstructions"] = _instruction_payload(instructions)

            tag_objects, created_tags = await _taxonomy_payload(client, "tags", tags)
            if tag_objects is not None:
                payload["tags"] = tag_objects
            category_objects, created_categories = await _taxonomy_payload(
                client, "categories", categories
            )
            if category_objects is not None:
                payload["recipeCategory"] = category_objects

            if payload:
                await client.request(
                    "PATCH",
                    f"/api/recipes/{slug}",
                    json=payload,
                    not_found=f"recipe {slug!r} not found",
                )
        except ToolError as exc:
            # The stub exists on the server now. Name it, so it is never
            # silently orphaned.
            raise ToolError(
                f"recipe {slug!r} was created but could not be filled in: {exc}. "
                f"Fix with update_recipe({slug!r}, ...) or delete it."
            ) from exc

        result = await _fetch_recipe(client, slug)
        return _with_created(result, created_tags, created_categories)

    @mcp.tool
    async def update_recipe(
        slug: str,
        name: str | None = None,
        description: str | None = None,
        ingredients: list[Any] | None = None,
        instructions: list[str] | None = None,
        recipe_yield: str | None = None,
        prep_time: str | None = None,
        cook_time: str | None = None,
        tags: list[str] | None = None,
        categories: list[str] | None = None,
        source_url: str | None = None,
        replace_tags: bool = False,
        replace_categories: bool = False,
    ) -> dict:
        """Update a recipe. Only the fields you pass are touched.

        Tags and categories MERGE with what is already there; pass
        replace_tags/replace_categories to overwrite instead. Ingredients and
        instructions always replace the existing list — pass the whole list.
        """
        client = get_client()
        current = await client.request(
            "GET", f"/api/recipes/{slug}", not_found=f"recipe {slug!r} not found"
        )

        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if recipe_yield is not None:
            payload["recipeYield"] = recipe_yield
        if prep_time is not None:
            payload["prepTime"] = prep_time
        if cook_time is not None:
            payload["cookTime"] = cook_time
        if source_url is not None:
            payload["orgURL"] = source_url
        if ingredients is not None:
            payload["recipeIngredient"] = await _ingredient_payload(client, ingredients)
        if instructions is not None:
            payload["recipeInstructions"] = _instruction_payload(instructions)

        created_tags: list[str] = []
        created_categories: list[str] = []
        if tags is not None:
            objects, created_tags = await client.resolve_taxonomy("tags", tags)
            payload["tags"] = _merge(current.get("tags"), objects, replace_tags)
        if categories is not None:
            objects, created_categories = await client.resolve_taxonomy("categories", categories)
            payload["recipeCategory"] = _merge(
                current.get("recipeCategory"), objects, replace_categories
            )

        if not payload:
            return {"slug": slug, "note": "nothing to update — no fields were provided"}

        await client.request(
            "PATCH", f"/api/recipes/{slug}", json=payload, not_found=f"recipe {slug!r} not found"
        )
        result = await _fetch_recipe(client, slug)
        return _with_created(result, created_tags, created_categories)

    @mcp.tool
    async def delete_recipe(slug: str, confirm_slug: str) -> dict:
        """Permanently delete a recipe. Pass the same slug twice to confirm."""
        if slug != confirm_slug:
            raise ToolError(
                f"confirm_slug {confirm_slug!r} does not match slug {slug!r} — nothing deleted"
            )
        await get_client().request(
            "DELETE", f"/api/recipes/{slug}", not_found=f"recipe {slug!r} not found"
        )
        return {"deleted": slug}

    @mcp.tool
    async def import_recipe_from_url(
        url: str, include_tags: bool = True, include_categories: bool = True
    ) -> dict:
        """Scrape a recipe from a URL into Mealie and return what was imported."""
        client = get_client()
        slug = await client.request(
            "POST",
            "/api/recipes/create/url",
            json={
                "url": url,
                "includeTags": include_tags,
                "includeCategories": include_categories,
            },
        )
        if not isinstance(slug, str):
            slug = (slug or {}).get("slug")
        return await _fetch_recipe(client, slug)


def _merge(existing: list[dict] | None, incoming: list[dict], replace: bool) -> list[dict]:
    """Mealie replaces list fields wholesale, so merging is our job."""
    if replace:
        return incoming
    merged = {item["id"]: item for item in (existing or []) if item.get("id")}
    merged.update({item["id"]: item for item in incoming if item.get("id")})
    return list(merged.values())


def _with_created(result: dict, tags: list[str], categories: list[str]) -> dict:
    """Surface auto-created taxonomy so a typo is visible immediately."""
    notes = []
    if tags:
        notes.append("created new tags: " + ", ".join(tags))
    if categories:
        notes.append("created new categories: " + ", ".join(categories))
    if notes:
        result = {**result, "note": "; ".join(notes)}
    return result
