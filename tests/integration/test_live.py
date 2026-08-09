"""Integration tests against an ephemeral Mealie instance.

Opt-in: set MEALIE_INTEGRATION=1 plus MEALIE_URL and MEALIE_API_TOKEN.
`scripts/integration.sh` spins up a throwaway Mealie in Docker, mints a
token, runs these, and tears everything down.

These tests write real data — never point them at an instance you care about.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from fastmcp import Client

from mealie_mcp.config import Config
from mealie_mcp.server import build_server

pytestmark = pytest.mark.skipif(
    os.environ.get("MEALIE_INTEGRATION") != "1",
    reason="integration tests run via scripts/integration.sh (MEALIE_INTEGRATION=1)",
)


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
    assert "items" in search.data and "count" in search.data

    tags = await client.call_tool("manage_taxonomy", {"resource": "tags", "action": "list"})
    assert "items" in tags.data

    meals = await client.call_tool("get_todays_meals", {})
    assert meals.data["date"] == date.today().isoformat()  # noqa: DTZ011
    assert meals.data["count"] == len(meals.data["items"])

    books = await client.call_tool("list_cookbooks", {})
    assert "items" in books.data


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
    slug = created.data["slug"]
    try:
        assert set(created.data["tags"]) >= {"Integration Test", "Breakfast"}
        assert len(created.data["ingredients"]) == 3

        updated = await client.call_tool("update_recipe", {"slug": slug, "tags": ["Quick"]})
        # Tags must merge with the existing ones, not replace them.
        assert set(updated.data["tags"]) >= {"Integration Test", "Breakfast", "Quick"}

        today = date.today()  # noqa: DTZ011
        entry = await client.call_tool(
            "add_meal_plan_entry",
            {"date": today.isoformat(), "recipe_slug": slug, "entry_type": "breakfast"},
        )
        assert entry.data["recipe_slug"] == slug

        plan = await client.call_tool(
            "get_meal_plan",
            {
                "start_date": today.isoformat(),
                "end_date": (today + timedelta(days=1)).isoformat(),
            },
        )
        assert any(e.get("recipe_slug") == slug for e in plan.data["items"])

        deleted_entry = await client.call_tool(
            "delete_meal_plan_entry", {"entry_id": entry.data["entry_id"]}
        )
        assert deleted_entry.data == {"deleted": entry.data["entry_id"]}

        book = await client.call_tool(
            "create_cookbook",
            {
                "name": "Integration Test Book",
                "query_filter": 'tags.name IN ["Integration Test"]',
            },
        )
        try:
            recipes = await client.call_tool(
                "get_cookbook_recipes", {"cookbook": book.data["cookbook_id"]}
            )
            assert any(r.get("slug") == slug for r in recipes.data["items"])
        finally:
            await client.call_tool("delete_cookbook", {"cookbook_id": book.data["cookbook_id"]})
    finally:
        deleted = await client.call_tool("delete_recipe", {"slug": slug, "confirm_slug": slug})
        assert deleted.data == {"deleted": slug}


async def test_parse_ingredients(client):
    parsed = await client.call_tool("parse_ingredients", {"lines": ["3 cups oats"]})
    items = parsed.data["items"]
    assert len(items) == 1
    assert items[0]["input"] == "3 cups oats"
