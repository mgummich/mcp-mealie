"""Tool registration, the read-only guard, and the payload builders."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError

from mealie_mcp.config import Config
from mealie_mcp.server import build_server, probe
from mealie_mcp.tools.recipes import _instruction_payload, _normalize_ingredient

BASE = "https://mealie.test"

READ_TOOLS = {
    "search_recipes",
    "get_recipe",
    "suggest_recipes",
    "get_meal_plan",
    "get_todays_meals",
    "list_cookbooks",
    "get_cookbook_recipes",
    "parse_ingredients",
    "manage_taxonomy",
}
WRITE_TOOLS = {
    "create_recipe",
    "update_recipe",
    "delete_recipe",
    "import_recipe_from_url",
    "add_meal_plan_entry",
    "delete_meal_plan_entry",
    "random_meal_plan",
    "create_cookbook",
    "delete_cookbook",
}


def config(**extra) -> Config:
    return Config(url=BASE, token="tok", **extra)


async def tool_names(cfg: Config) -> set[str]:
    async with Client(build_server(cfg)) as client:
        return {tool.name for tool in await client.list_tools()}


async def test_every_tool_is_registered():
    assert await tool_names(config()) == READ_TOOLS | WRITE_TOOLS


async def test_read_only_mode_hides_writes():
    names = await tool_names(config(read_only=True))

    assert names == READ_TOOLS
    assert not names & WRITE_TOOLS


async def test_every_tool_has_a_description():
    async with Client(build_server(config())) as client:
        for tool in await client.list_tools():
            assert tool.description, f"{tool.name} has no description"


async def test_cookbook_filter_examples_reach_the_model():
    # Without worked examples models invent invalid filter syntax.
    async with Client(build_server(config())) as client:
        tool = next(t for t in await client.list_tools() if t.name == "create_cookbook")

    assert tool.description.count("\n") >= 4
    assert "tags.name IN" in tool.description


async def test_delete_recipe_requires_a_matching_confirmation():
    async with Client(build_server(config())) as client:
        with pytest.raises(ToolError, match="does not match"):
            await client.call_tool(
                "delete_recipe", {"slug": "roast", "confirm_slug": "roats"}
            )


async def test_taxonomy_writes_are_refused_in_read_only_mode():
    async with Client(build_server(config(read_only=True))) as client:
        with pytest.raises(ToolError, match="read-only mode"):
            await client.call_tool(
                "manage_taxonomy", {"resource": "tags", "action": "create", "name": "Nope"}
            )


async def test_meal_plan_rejects_a_non_iso_date():
    async with Client(build_server(config())) as client:
        with pytest.raises(ToolError, match="ISO date"):
            await client.call_tool(
                "get_meal_plan", {"start_date": "next tuesday", "end_date": "2026-08-10"}
            )


async def test_random_meal_plan_caps_the_range():
    async with Client(build_server(config())) as client:
        with pytest.raises(ToolError, match="cap is 14"):
            await client.call_tool(
                "random_meal_plan", {"start_date": "2026-01-01", "end_date": "2026-03-01"}
            )


@respx.mock
async def test_probe_refuses_mealie_1x():
    from mealie_mcp.client import MealieClient
    from mealie_mcp.config import ConfigError

    respx.get(f"{BASE}/api/app/about").mock(
        return_value=httpx.Response(200, json={"version": "v1.12.0"})
    )
    client = MealieClient(BASE, "tok")
    try:
        with pytest.raises(ConfigError, match="not supported"):
            await probe(client)
    finally:
        await client.aclose()


@respx.mock
async def test_probe_reports_version_and_user():
    from mealie_mcp.client import MealieClient

    respx.get(f"{BASE}/api/app/about").mock(
        return_value=httpx.Response(200, json={"version": "v2.8.0"})
    )
    respx.get(f"{BASE}/api/users/self").mock(
        return_value=httpx.Response(200, json={"username": "chef"})
    )
    client = MealieClient(BASE, "tok")
    try:
        assert await probe(client) == ("v2.8.0", "chef")
    finally:
        await client.aclose()


def test_instructions_carry_the_fields_the_orm_requires():
    # A bare {"text": ...} makes Mealie 500 on RecipeInstruction.__init__.
    assert _instruction_payload(["Chop."]) == [
        {"title": "", "text": "Chop.", "ingredientReferences": []}
    ]


def test_ingredient_keeps_resolved_food_objects():
    payload = _normalize_ingredient(
        {"quantity": 2, "food": {"id": "f1", "name": "flour"}, "note": ""}, original="2 cups flour"
    )

    assert payload["food"] == {"id": "f1", "name": "flour"}
    assert payload["referenceId"]


def test_ingredient_folds_unresolved_names_into_the_note():
    # IngredientFood requires an id; an unmatched food would otherwise 500.
    # When nothing resolved, the source line reads better than "pinch saffron".
    payload = _normalize_ingredient(
        {"quantity": 1, "unit": {"name": "pinch"}, "food": {"name": "saffron"}},
        original="a pinch of saffron",
    )

    assert "food" not in payload
    assert "unit" not in payload
    assert payload["note"] == "a pinch of saffron"
    assert payload["originalText"] == "a pinch of saffron"


def test_ingredient_keeps_fragment_note_when_the_unit_resolved():
    # Partially resolved: only the leftover name folds into the note.
    payload = _normalize_ingredient(
        {"quantity": 1, "unit": {"id": "u1", "name": "pinch"}, "food": {"name": "saffron"}},
        original="a pinch of saffron",
    )

    assert payload["unit"] == {"id": "u1", "name": "pinch"}
    assert "food" not in payload
    assert payload["note"] == "saffron"


@respx.mock
async def test_search_by_unknown_food_returns_empty_not_error():
    # A food Mealie has never heard of provably matches no recipes.
    respx.get(f"{BASE}/api/foods").mock(return_value=httpx.Response(200, json={"items": []}))

    async with Client(build_server(config())) as client:
        result = await client.call_tool("search_recipes", {"foods": ["Unicorn Meat"]})

    assert result.data["count"] == 0
    assert "Unicorn Meat" in result.data["note"]


@respx.mock
async def test_search_by_food_filters_by_id():
    respx.get(f"{BASE}/api/foods").mock(
        return_value=httpx.Response(
            200, json={"items": [{"id": "f9", "name": "Chicken"}]}
        )
    )
    recipes = respx.get(f"{BASE}/api/recipes").mock(
        return_value=httpx.Response(200, json={"items": [], "total": 0})
    )

    async with Client(build_server(config())) as client:
        await client.call_tool("search_recipes", {"foods": ["chicken"]})

    assert recipes.calls.last.request.url.params["foods"] == "f9"


@respx.mock
async def test_random_meal_plan_reports_partial_success():
    # Day two failing must not hide that day one was written.
    entry = {"id": "e1", "date": "2026-09-01", "entryType": "dinner", "title": "Stew"}
    respx.post(f"{BASE}/api/households/mealplans/random").mock(
        side_effect=[httpx.Response(200, json=entry), httpx.Response(500)]
    )

    async with Client(build_server(config())) as client:
        result = await client.call_tool(
            "random_meal_plan", {"start_date": "2026-09-01", "end_date": "2026-09-02"}
        )

    assert result.data["count"] == 1
    assert "stopped at 2026-09-02" in result.data["failed"]


@respx.mock
async def test_random_meal_plan_raises_when_nothing_landed():
    respx.post(f"{BASE}/api/households/mealplans/random").mock(
        return_value=httpx.Response(500)
    )

    async with Client(build_server(config())) as client:
        with pytest.raises(ToolError, match="stopped at 2026-09-01"):
            await client.call_tool(
                "random_meal_plan", {"start_date": "2026-09-01", "end_date": "2026-09-01"}
            )
