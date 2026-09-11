"""Integration tests against an ephemeral Mealie instance.

Opt-in: set MEALIE_INTEGRATION=1 plus MEALIE_URL and MEALIE_API_TOKEN.
`scripts/integration.sh` spins up a throwaway Mealie in Docker, mints a
token, runs these, and tears everything down.

These tests write real data — never point them at an instance you care about.
"""

from __future__ import annotations

import base64
import os
from datetime import date, timedelta

import httpx
import pytest
from conftest import data
from fastmcp import Client
from fastmcp.exceptions import ToolError

from mealie_mcp.config import Config
from mealie_mcp.server import build_server

pytestmark = pytest.mark.skipif(
    os.environ.get("MEALIE_INTEGRATION") != "1",
    reason="integration tests run via scripts/integration.sh (MEALIE_INTEGRATION=1)",
)


def _mealie_major() -> int:
    """Major version of the instance under test, or 0 when not running."""
    if os.environ.get("MEALIE_INTEGRATION") != "1":
        return 0
    about = httpx.get(f"{os.environ['MEALIE_URL']}/api/app/about", timeout=10).json()
    return int(str(about.get("version") or "0").lstrip("v").split(".")[0])


#: Recipe import from images is a Mealie 3.x endpoint; 2.x answers 405.
needs_mealie_3 = pytest.mark.skipif(_mealie_major() < 3, reason="needs Mealie 3.x")


@pytest.fixture
async def client():
    async with Client(build_server(Config.from_env())) as c:
        yield c


async def test_all_tools_registered(client):
    # The exact inventory is pinned in test_tools.py; a count repeated here
    # only ever fails for having drifted. What this proves is that every
    # module registers against a real instance.
    tools = {t.name for t in await client.list_tools()}
    assert {"search_recipes", "create_recipe", "get_meal_plan", "list_cookbooks"} <= tools


async def test_read_tools(client):
    search = await client.call_tool("search_recipes", {"limit": 3})
    assert "items" in data(search) and "count" in data(search)

    tags = await client.call_tool("manage_taxonomy", {"resource": "tags", "action": "list"})
    assert "items" in data(tags)

    meals = await client.call_tool("get_todays_meals", {})
    assert data(meals)["date"] == date.today().isoformat()  # noqa: DTZ011
    assert data(meals)["count"] == len(data(meals)["items"])

    books = await client.call_tool("list_cookbooks", {})
    assert "items" in data(books)


async def test_write_roundtrip(client):
    created = await client.call_tool(
        "create_recipe",
        {
            "name": "Integration Test Pancakes",
            "description": "Created by tests/integration/test_live.py",
            "ingredients": ["2 cups flour", "1 tbsp sugar", "a pinch of salt"],
            "instructions": ["Mix everything.", "Fry until golden."],
            "tags": ["Integration Test", "Breakfast"],
            "recipe_yield": "4 servings",
        },
    )
    slug = data(created)["slug"]
    try:
        assert set(data(created)["tags"]) >= {"Integration Test", "Breakfast"}
        assert len(data(created)["ingredients"]) == 3

        updated = await client.call_tool("update_recipe", {"slug": slug, "tags": ["Quick"]})
        # Tags must merge with the existing ones, not replace them.
        assert set(data(updated)["tags"]) >= {"Integration Test", "Breakfast", "Quick"}

        today = date.today()  # noqa: DTZ011
        entry = await client.call_tool(
            "add_meal_plan_entry",
            {"date": today.isoformat(), "recipe_slug": slug, "entry_type": "breakfast"},
        )
        assert data(entry)["recipe_slug"] == slug

        plan = await client.call_tool(
            "get_meal_plan",
            {
                "start_date": today.isoformat(),
                "end_date": (today + timedelta(days=1)).isoformat(),
            },
        )
        assert any(e.get("recipe_slug") == slug for e in data(plan)["items"])

        deleted_entry = await client.call_tool(
            "delete_meal_plan_entry", {"entry_id": data(entry)["entry_id"]}
        )
        assert data(deleted_entry) == {"deleted": data(entry)["entry_id"]}

        book = await client.call_tool(
            "create_cookbook",
            {
                "name": "Integration Test Book",
                "query_filter": 'tags.name IN ["Integration Test"]',
            },
        )
        try:
            recipes = await client.call_tool(
                "get_cookbook_recipes", {"cookbook": data(book)["cookbook_id"]}
            )
            assert any(r.get("slug") == slug for r in data(recipes)["items"])
        finally:
            await client.call_tool("delete_cookbook", {"cookbook_id": data(book)["cookbook_id"]})
    finally:
        deleted = await client.call_tool("delete_recipe", {"slug": slug, "confirm_slug": slug})
        assert data(deleted) == {"deleted": slug}


async def test_bulk_tag_and_image_upload(client, tmp_path):
    slugs = []
    for name in ("Bulk Test One", "Bulk Test Two"):
        created = await client.call_tool("create_recipe", {"name": name})
        slugs.append(data(created)["slug"])
    try:
        result = await client.call_tool(
            "bulk_tag_recipes",
            {"slugs": slugs, "tags": ["Bulk Tagged"], "categories": ["Bulk Category"]},
        )
        assert data(result)["recipes"] == 2

        for slug in slugs:
            recipe = await client.call_tool("get_recipe", {"slug": slug})
            assert "Bulk Tagged" in data(recipe)["tags"]
            assert "Bulk Category" in data(recipe)["categories"]

        # A 1x1 PNG is enough to prove the multipart body Mealie wants.
        photo = tmp_path / "pixel.png"
        photo.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
            )
        )
        uploaded = await client.call_tool(
            "upload_recipe_image", {"slug": slugs[0], "path": str(photo)}
        )
        assert data(uploaded)["slug"] == slugs[0]
    finally:
        for slug in slugs:
            await client.call_tool("delete_recipe", {"slug": slug, "confirm_slug": slug})


async def test_parse_ingredients(client):
    parsed = await client.call_tool("parse_ingredients", {"lines": ["3 cups oats"]})
    items = data(parsed)["items"]
    assert len(items) == 1
    assert items[0]["input"] == "3 cups oats"


async def test_parser_selection(client):
    parsed = await client.call_tool(
        "parse_ingredients", {"lines": ["3 cups oats"], "parser": "brute"}
    )
    assert data(parsed)["items"][0]["input"] == "3 cups oats"


async def test_shopping_list_roundtrip(client):
    created = await client.call_tool("create_recipe", {"name": "Shopping Test Stew"})
    slug = data(created)["slug"]
    book = await client.call_tool("create_shopping_list", {"name": "Integration Test List"})
    list_id = data(book)["list_id"]
    try:
        assert any(
            b["list_id"] == list_id
            for b in data(await client.call_tool("list_shopping_lists"))["items"]
        )

        added = await client.call_tool(
            "add_shopping_item", {"list_id": list_id, "item": "2 lemons"}
        )
        item_id = data(added)["item_id"]
        # quantity 0 is what keeps Mealie from rendering this as "1 2 lemons".
        assert data(added)["item"] == "2 lemons"

        checked = await client.call_tool(
            "update_shopping_item", {"item_id": item_id, "checked": True}
        )
        assert data(checked)["checked"] is True

        await client.call_tool(
            "update_recipe", {"slug": slug, "ingredients": ["1 onion", "2 carrots"]}
        )
        filled = await client.call_tool(
            "add_recipe_to_shopping_list", {"list_id": list_id, "recipe_slug": slug}
        )
        assert data(filled)["count"] > 1

        fetched = await client.call_tool("get_shopping_list", {"list_id": list_id})
        assert data(fetched)["count"] == data(filled)["count"]

        deleted = await client.call_tool("delete_shopping_item", {"item_id": item_id})
        assert data(deleted) == {"deleted": item_id}
    finally:
        await client.call_tool(
            "delete_shopping_list", {"list_id": list_id, "confirm_list_id": list_id}
        )
        await client.call_tool("delete_recipe", {"slug": slug, "confirm_slug": slug})


async def test_cooking_history_roundtrip(client):
    created = await client.call_tool("create_recipe", {"name": "History Test Curry"})
    slug = data(created)["slug"]
    try:
        made = await client.call_tool("mark_recipe_made", {"slug": slug, "when": "2026-01-02"})
        assert data(made)["last_made"].startswith("2026-01-02")
        assert data(await client.call_tool("get_recipe", {"slug": slug}))["last_made"].startswith(
            "2026-01-02"
        )

        rated = await client.call_tool("rate_recipe", {"slug": slug, "rating": 4, "favorite": True})
        assert data(rated) == {"slug": slug, "rating": 4, "favorite": True}
        assert data(await client.call_tool("get_recipe_rating", {"slug": slug})) == {
            "slug": slug,
            "rating": 4,
            "favorite": True,
        }

        comment = await client.call_tool("add_recipe_comment", {"slug": slug, "text": "Too salty"})
        comment_id = data(comment)["comment_id"]
        listed = data(await client.call_tool("get_recipe_comments", {"slug": slug}))
        assert [c["text"] for c in listed["items"]] == ["Too salty"]
        await client.call_tool("delete_recipe_comment", {"comment_id": comment_id})

        # Mealie writes a "Recipe Created" event of its own, so the timeline is
        # never empty for a recipe this test just made.
        timeline = data(await client.call_tool("get_recipe_timeline", {"slug": slug}))
        assert timeline["count"] >= 1
    finally:
        await client.call_tool("delete_recipe", {"slug": slug, "confirm_slug": slug})


async def test_duplicate_and_meal_plan_update(client):
    created = await client.call_tool("create_recipe", {"name": "Duplicate Test Soup"})
    slug = data(created)["slug"]
    copy_slug = data(
        await client.call_tool("duplicate_recipe", {"slug": slug, "name": "Duplicate Test Copy"})
    )["slug"]
    try:
        assert copy_slug != slug
        assert data(await client.call_tool("get_recipe", {"slug": copy_slug}))["name"] == (
            "Duplicate Test Copy"
        )

        today = date.today()  # noqa: DTZ011
        entry = await client.call_tool(
            "add_meal_plan_entry", {"date": today.isoformat(), "recipe_slug": slug}
        )
        entry_id = data(entry)["entry_id"]
        try:
            updated = await client.call_tool(
                "update_meal_plan_entry",
                {"entry_id": entry_id, "entry_type": "lunch", "recipe_slug": copy_slug},
            )
            assert data(updated)["meal"] == "lunch"
            assert data(updated)["recipe_slug"] == copy_slug
            assert data(updated)["date"] == today.isoformat()
        finally:
            await client.call_tool("delete_meal_plan_entry", {"entry_id": entry_id})
    finally:
        for victim in (copy_slug, slug):
            await client.call_tool("delete_recipe", {"slug": victim, "confirm_slug": victim})


@needs_mealie_3
async def test_image_import_reports_a_missing_ai_provider(client, tmp_path):
    # The throwaway instance has no AI provider, so this proves the request
    # shape reaches Mealie and that its refusal is passed through readably.
    photo = tmp_path / "pixel.png"
    photo.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
    )
    with pytest.raises(ToolError, match="AI services are not enabled"):
        await client.call_tool("import_recipe_from_images", {"paths": [str(photo)]})
