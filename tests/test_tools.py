"""Tool registration, the read-only guard, and the payload builders."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from conftest import data
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
    "library_stats",
    "find_duplicate_recipes",
    "check_recipe_links",
}
WRITE_TOOLS = {
    "create_recipe",
    "update_recipe",
    "set_recipe_image",
    "upload_recipe_image",
    "bulk_tag_recipes",
    "delete_recipe",
    "import_recipe_from_url",
    "add_meal_plan_entry",
    "delete_meal_plan_entry",
    "random_meal_plan",
    "create_cookbook",
    "update_cookbook",
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
            await client.call_tool("delete_recipe", {"slug": "roast", "confirm_slug": "roats"})


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

    assert data(result)["count"] == 0
    assert "Unicorn Meat" in data(result)["note"]


@respx.mock
async def test_search_by_food_filters_by_id():
    respx.get(f"{BASE}/api/foods").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "f9", "name": "Chicken"}]})
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

    assert data(result)["count"] == 1
    assert "stopped at 2026-09-02" in data(result)["failed"]


@respx.mock
async def test_random_meal_plan_raises_when_nothing_landed():
    respx.post(f"{BASE}/api/households/mealplans/random").mock(return_value=httpx.Response(500))

    async with Client(build_server(config())) as client:
        with pytest.raises(ToolError, match="stopped at 2026-09-01"):
            await client.call_tool(
                "random_meal_plan", {"start_date": "2026-09-01", "end_date": "2026-09-01"}
            )


@respx.mock
async def test_taxonomy_list_paginates_and_reports_the_total():
    # 400 foods behind a 200-row page must not read as "that is all of them".
    route = respx.get(f"{BASE}/api/foods").mock(
        return_value=httpx.Response(
            200, json={"items": [{"id": "f1", "name": "Flour"}], "total": 400}
        )
    )

    async with Client(build_server(config())) as client:
        result = await client.call_tool(
            "manage_taxonomy", {"resource": "foods", "action": "list", "page": 2}
        )

    assert route.calls.last.request.url.params["page"] == "2"
    assert data(result)["total"] == 400
    assert "page=3" in data(result)["note"]


@respx.mock
async def test_taxonomy_update_patches_onto_the_current_row():
    # Mealie's PUT replaces the row; a bare {description} would blank the name.
    respx.get(f"{BASE}/api/foods/f1").mock(
        return_value=httpx.Response(200, json={"id": "f1", "name": "Flour"})
    )
    put = respx.put(f"{BASE}/api/foods/f1").mock(
        return_value=httpx.Response(
            200, json={"id": "f1", "name": "Flour", "description": "Plain white."}
        )
    )

    async with Client(build_server(config())) as client:
        result = await client.call_tool(
            "manage_taxonomy",
            {
                "resource": "foods",
                "action": "update",
                "item_id": "f1",
                "data": {"description": "Plain white."},
            },
        )

    assert put.calls.last.request.read() == (
        b'{"id":"f1","name":"Flour","description":"Plain white."}'
    )
    assert data(result)["description"] == "Plain white."


@respx.mock
async def test_taxonomy_merge_sends_the_resource_specific_keys():
    merge = respx.put(f"{BASE}/api/units/merge").mock(
        return_value=httpx.Response(200, json={"id": "u2", "name": "gram"})
    )

    async with Client(build_server(config())) as client:
        result = await client.call_tool(
            "manage_taxonomy",
            {"resource": "units", "action": "merge", "item_id": "u1", "merge_into": "u2"},
        )

    assert merge.calls.last.request.read() == b'{"fromUnit":"u1","toUnit":"u2"}'
    assert data(result)["merged"] == "u1"


async def test_taxonomy_merge_is_refused_where_mealie_has_no_endpoint():
    async with Client(build_server(config())) as client:
        with pytest.raises(ToolError, match="only supported for foods, units"):
            await client.call_tool(
                "manage_taxonomy",
                {"resource": "tags", "action": "merge", "item_id": "t1", "merge_into": "t2"},
            )


@respx.mock
async def test_update_recipe_writes_notes_and_rating():
    respx.get(f"{BASE}/api/recipes/roast").mock(
        return_value=httpx.Response(200, json={"id": "r1", "slug": "roast", "name": "Roast"})
    )
    patch = respx.patch(f"{BASE}/api/recipes/roast").mock(return_value=httpx.Response(200, json={}))

    async with Client(build_server(config())) as client:
        await client.call_tool(
            "update_recipe",
            {"slug": "roast", "notes": ["Rest 10 min."], "rating": 4},
        )

    assert patch.calls.last.request.read() == (
        b'{"notes":[{"title":"","text":"Rest 10 min."}],"rating":4.0}'
    )


@respx.mock
async def test_update_recipe_returns_the_new_slug_after_a_rename():
    # Mealie derives the slug from the name, so a re-read of "roast" 404s.
    respx.get(f"{BASE}/api/recipes/roast").mock(
        return_value=httpx.Response(200, json={"id": "r1", "slug": "roast", "name": "Roast"})
    )
    respx.patch(f"{BASE}/api/recipes/roast").mock(
        return_value=httpx.Response(
            200, json={"id": "r1", "slug": "sunday-roast", "name": "Sunday Roast"}
        )
    )

    async with Client(build_server(config())) as client:
        result = await client.call_tool("update_recipe", {"slug": "roast", "name": "Sunday Roast"})

    assert data(result)["slug"] == "sunday-roast"
    assert data(result)["renamed_from"] == "roast"


@respx.mock
async def test_update_recipe_says_nothing_about_a_rename_that_did_not_happen():
    respx.get(f"{BASE}/api/recipes/roast").mock(
        return_value=httpx.Response(200, json={"id": "r1", "slug": "roast", "name": "Roast"})
    )
    respx.patch(f"{BASE}/api/recipes/roast").mock(
        return_value=httpx.Response(200, json={"id": "r1", "slug": "roast", "name": "Roast"})
    )

    async with Client(build_server(config())) as client:
        result = await client.call_tool("update_recipe", {"slug": "roast", "rating": 5})

    assert "renamed_from" not in data(result)


@respx.mock
async def test_import_flags_a_scrape_that_found_nothing():
    respx.post(f"{BASE}/api/recipes/create/url").mock(
        return_value=httpx.Response(201, json="tomatoes")
    )
    respx.get(f"{BASE}/api/recipes/tomatoes").mock(
        return_value=httpx.Response(
            200,
            json={
                "slug": "tomatoes",
                "name": "Tomatoes",
                "recipeIngredient": [{"note": "Could not detect ingredients"}],
                "recipeInstructions": [{"text": "Could not detect instructions"}],
            },
        )
    )

    async with Client(build_server(config())) as client:
        result = await client.call_tool("import_recipe_from_url", {"url": "https://js.test/r"})

    assert "no ingredients or instructions" in data(result)["note"]


@respx.mock
async def test_bulk_tag_sends_resolved_objects_and_creates_missing_names():
    respx.get(f"{BASE}/api/organizers/tags").mock(
        return_value=httpx.Response(
            200, json={"items": [{"id": "t1", "name": "Vegan", "slug": "vegan"}]}
        )
    )
    created = respx.post(f"{BASE}/api/organizers/tags").mock(
        return_value=httpx.Response(201, json={"id": "t2", "name": "Quick", "slug": "quick"})
    )
    bulk = respx.post(f"{BASE}/api/recipes/bulk-actions/tag").mock(
        return_value=httpx.Response(200, json=None)
    )

    async with Client(build_server(config())) as client:
        result = await client.call_tool(
            "bulk_tag_recipes", {"slugs": ["roast", "stew"], "tags": ["vegan", "Quick"]}
        )

    assert created.called
    body = json.loads(bulk.calls.last.request.read())
    assert body["recipes"] == ["roast", "stew"]
    # TagBase needs all three fields; a bare name is a 422.
    assert body["tags"][0] == {"id": "t1", "name": "Vegan", "slug": "vegan"}
    assert data(result)["recipes"] == 2
    assert data(result)["created"]["tags"] == ["Quick"]


async def test_bulk_tag_refuses_a_call_that_would_do_nothing():
    async with Client(build_server(config())) as client:
        with pytest.raises(ToolError, match="pass tags, categories, or both"):
            await client.call_tool("bulk_tag_recipes", {"slugs": ["roast"]})


@respx.mock
async def test_upload_recipe_image_sends_the_extension_field(tmp_path):
    photo = tmp_path / "roast.JPG"
    photo.write_bytes(b"\xff\xd8\xff")
    upload = respx.put(f"{BASE}/api/recipes/roast/image").mock(
        return_value=httpx.Response(200, json={})
    )

    async with Client(build_server(config())) as client:
        result = await client.call_tool(
            "upload_recipe_image", {"slug": "roast", "path": str(photo)}
        )

    # Without the extension part Mealie answers 422 Field required.
    body = upload.calls.last.request.read()
    assert b'name="extension"' in body and b".jpg" in body
    assert b"\xff\xd8\xff" in body
    assert data(result)["bytes"] == 3


async def test_upload_recipe_image_rejects_a_non_image(tmp_path):
    doc = tmp_path / "notes.txt"
    doc.write_text("not a photo")

    async with Client(build_server(config())) as client:
        with pytest.raises(ToolError, match="not an image"):
            await client.call_tool("upload_recipe_image", {"slug": "roast", "path": str(doc)})


async def test_get_recipe_rejects_an_unknown_field():
    async with Client(build_server(config())) as client:
        with pytest.raises(ToolError, match="unknown fields"):
            await client.call_tool("get_recipe", {"slug": "roast", "fields": ["ingredents"]})


@respx.mock
async def test_library_stats_rolls_up_tag_usage_in_one_call():
    # The whole point: one call answers "which tags are unused".
    respx.get(f"{BASE}/api/recipes").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"slug": "stew", "name": "Stew", "tags": [{"id": "t1", "name": "Dinner"}]},
                    {"slug": "soup", "name": "Soup", "tags": [{"id": "t1", "name": "Dinner"}]},
                ],
                "total": 2,
            },
        )
    )
    respx.get(f"{BASE}/api/organizers/tags").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"id": "t1", "name": "Dinner"}, {"id": "t2", "name": "Brunch"}]},
        )
    )

    async with Client(build_server(config())) as client:
        result = await client.call_tool("library_stats", {"resource": "tags"})

    assert data(result)["items"] == [
        {"id": "t1", "name": "Dinner", "recipe_count": 2},
        {"id": "t2", "name": "Brunch", "recipe_count": 0},
    ]
    assert data(result)["unused"] == 1


@respx.mock
async def test_library_stats_caps_both_lists_at_top():
    # Each row costs a UUID. Used and unused each get their own budget of
    # `top` rows, so neither list can run away with the reply.
    respx.get(f"{BASE}/api/recipes").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"slug": f"r{i}", "name": f"R{i}", "tags": [{"id": f"t{i}", "name": f"T{i}"}]}
                    for i in range(10)
                ],
                "total": 10,
            },
        )
    )
    respx.get(f"{BASE}/api/organizers/tags").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [{"id": f"t{i}", "name": f"T{i}"} for i in range(10)]
                + [{"id": f"u{i}", "name": f"U{i}"} for i in range(5)]
            },
        )
    )

    async with Client(build_server(config())) as client:
        result = await client.call_tool("library_stats", {"resource": "tags", "top": 3})

    counts = [row["recipe_count"] for row in data(result)["items"]]
    assert counts == [1, 1, 1, 0, 0, 0]
    assert data(result)["used"] == 10
    assert data(result)["unused"] == 5
    assert "3 most-used of 10" in data(result)["note"]
    assert "3 of 5 unused" in data(result)["note"]


@respx.mock
async def test_tool_results_are_not_sent_twice():
    # MCP ships the return value as text and as structuredContent both; the
    # duplicate is pure token cost for a client that reads the text.
    respx.get(f"{BASE}/api/recipes").mock(
        return_value=httpx.Response(200, json={"items": [], "total": 0})
    )

    async with Client(build_server(config())) as client:
        result = await client.call_tool("search_recipes", {"query": "x"})
        schemas = [t.outputSchema for t in await client.list_tools()]

    assert result.structured_content is None
    # A declared output schema is what obliges the server to send one.
    assert not any(schemas)


@respx.mock
async def test_library_stats_counts_a_repeated_food_once_per_recipe():
    respx.get(f"{BASE}/api/recipes").mock(
        return_value=httpx.Response(
            200, json={"items": [{"slug": "cake", "name": "Cake"}], "total": 1}
        )
    )
    respx.get(f"{BASE}/api/recipes/cake").mock(
        return_value=httpx.Response(
            200,
            json={
                "recipeIngredient": [
                    {"food": {"id": "f1", "name": "Flour"}},
                    {"food": {"id": "f1", "name": "Flour"}},
                ]
            },
        )
    )
    respx.get(f"{BASE}/api/foods").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "f1", "name": "Flour"}]})
    )

    async with Client(build_server(config())) as client:
        result = await client.call_tool("library_stats", {"resource": "foods"})

    assert data(result)["items"] == [{"id": "f1", "name": "Flour", "recipe_count": 1}]


@respx.mock
async def test_find_duplicate_recipes_ignores_punctuation_and_case():
    respx.get(f"{BASE}/api/recipes").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"slug": "chili", "name": "Grandma's Chili!"},
                    {"slug": "chili-2", "name": "grandma s chili"},
                    {"slug": "toast", "name": "Toast"},
                ],
                "total": 3,
            },
        )
    )

    async with Client(build_server(config())) as client:
        result = await client.call_tool("find_duplicate_recipes", {})

    assert data(result)["count"] == 1
    assert {r["slug"] for r in data(result)["groups"][0]["recipes"]} == {"chili", "chili-2"}


@respx.mock
async def test_check_recipe_links_reports_dead_sources_and_blank_images():
    respx.get(f"{BASE}/api/recipes").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"slug": "gone", "name": "Gone", "orgURL": "https://dead.test/r", "image": "1"},
                    {"slug": "blank", "name": "Blank"},
                ],
                "total": 2,
            },
        )
    )
    respx.head("https://dead.test/r").mock(return_value=httpx.Response(404))

    async with Client(build_server(config())) as client:
        result = await client.call_tool("check_recipe_links", {})

    assert [b["slug"] for b in data(result)["broken_sources"]] == ["gone"]
    assert [m["slug"] for m in data(result)["missing_images"]] == ["blank"]


@respx.mock
async def test_search_fields_projects_without_a_get_recipe_per_hit():
    respx.get(f"{BASE}/api/recipes").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "slug": "stew",
                        "name": "Stew",
                        "description": "warm",
                        "tags": [{"name": "Dinner"}],
                    }
                ],
                "total": 1,
            },
        )
    )

    async with Client(build_server(config())) as client:
        result = await client.call_tool("search_recipes", {"fields": ["slug", "tags"]})

    assert data(result)["items"] == [{"slug": "stew", "tags": ["Dinner"]}]


async def test_search_rejects_fields_that_only_get_recipe_can_serve():
    async with Client(build_server(config())) as client:
        with pytest.raises(ToolError, match="ingredients"):
            await client.call_tool("search_recipes", {"fields": ["ingredients"]})


@respx.mock
async def test_batch_taxonomy_update_reports_per_item_failures():
    # One bad id must not strand the other writes.
    respx.get(f"{BASE}/api/foods/f1").mock(
        return_value=httpx.Response(200, json={"id": "f1", "name": "Scallions"})
    )
    respx.put(f"{BASE}/api/foods/f1").mock(
        return_value=httpx.Response(200, json={"id": "f1", "name": "Scallion"})
    )
    respx.get(f"{BASE}/api/foods/nope").mock(return_value=httpx.Response(404))

    async with Client(build_server(config())) as client:
        result = await client.call_tool(
            "manage_taxonomy",
            {
                "resource": "foods",
                "action": "update",
                "items": [
                    {"item_id": "f1", "name": "Scallion"},
                    {"item_id": "nope", "name": "Ghost"},
                ],
            },
        )

    assert data(result)["count"] == 1
    assert data(result)["results"][0]["name"] == "Scallion"
    assert data(result)["failed"] == 1
    assert data(result)["errors"][0]["index"] == 1


async def test_batch_taxonomy_survives_a_malformed_item():
    # A non-object `data` raises TypeError deep in the payload builder, not
    # ToolError; it must still be reported per item rather than killing the call.
    async with Client(build_server(config())) as client:
        result = await client.call_tool(
            "manage_taxonomy",
            {
                "resource": "foods",
                "action": "update",
                "items": [{"item_id": "f1", "data": "not an object"}],
            },
        )

    assert data(result)["failed"] == 1
    assert "object" in data(result)["errors"][0]["error"]


@respx.mock
async def test_search_caps_the_page_size():
    route = respx.get(f"{BASE}/api/recipes").mock(
        return_value=httpx.Response(200, json={"items": [], "total": 0})
    )

    async with Client(build_server(config())) as client:
        await client.call_tool("search_recipes", {"limit": 5000})

    assert route.calls.last.request.url.params["perPage"] == "100"


@respx.mock
async def test_create_recipe_names_a_response_it_cannot_read():
    # An unreadable create response used to become PATCH /api/recipes/None.
    respx.post(f"{BASE}/api/recipes").mock(return_value=httpx.Response(201, json={}))

    async with Client(build_server(config())) as client:
        with pytest.raises(ToolError, match="no slug"):
            await client.call_tool("create_recipe", {"name": "Stew"})


@respx.mock
async def test_create_recipe_rejects_a_short_parser_response():
    respx.post(f"{BASE}/api/recipes").mock(return_value=httpx.Response(201, json="stew"))
    respx.post(f"{BASE}/api/parser/ingredients").mock(
        return_value=httpx.Response(200, json=[{"ingredient": {"note": "flour"}}])
    )

    async with Client(build_server(config())) as client:
        with pytest.raises(ToolError, match="unexpected response"):
            await client.call_tool(
                "create_recipe",
                {"name": "Stew", "ingredients": ["2 cups flour", "1 tsp salt"]},
            )


@respx.mock
async def test_create_cookbook_builds_the_filter_with_stored_casing():
    respx.get(f"{BASE}/api/organizers/tags").mock(
        return_value=httpx.Response(
            200, json={"items": [{"id": "t1", "name": "Vegan", "slug": "vegan"}]}
        )
    )
    created = respx.post(f"{BASE}/api/households/cookbooks").mock(
        return_value=httpx.Response(201, json={"id": "c1", "name": "Greens"})
    )

    async with Client(build_server(config())) as client:
        await client.call_tool(
            "create_cookbook", {"name": "Greens", "tags": ["vegan"], "require_all": True}
        )

    import json as _json

    body = _json.loads(created.calls.last.request.content)
    assert body["queryFilterString"] == 'tags.name CONTAINS ALL ["Vegan"]'


async def test_cookbook_refuses_a_hand_written_filter_plus_name_lists():
    async with Client(build_server(config())) as client:
        with pytest.raises(ToolError, match="not both"):
            await client.call_tool(
                "create_cookbook",
                {"name": "X", "tags": ["Vegan"], "query_filter": "rating > 3"},
            )


@respx.mock
async def test_update_cookbook_keeps_the_id_and_patches_onto_the_current_row():
    respx.get(f"{BASE}/api/households/cookbooks/c1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "c1",
                "name": "Old",
                "description": "keep me",
                "queryFilterString": "rating > 1",
                "public": False,
            },
        )
    )
    put = respx.put(f"{BASE}/api/households/cookbooks/c1").mock(
        return_value=httpx.Response(200, json={"id": "c1", "name": "New"})
    )

    async with Client(build_server(config())) as client:
        result = await client.call_tool(
            "update_cookbook", {"cookbook_id": "c1", "name": "New", "query_filter": "rating > 4"}
        )

    import json as _json

    body = _json.loads(put.calls.last.request.content)
    assert body == {
        "id": "c1",
        "name": "New",
        "description": "keep me",
        "queryFilterString": "rating > 4",
        "public": False,
    }
    assert data(result)["cookbook_id"] == "c1"


@respx.mock
async def test_suggest_recipes_resolves_names_to_ids_and_lists_what_is_missing():
    respx.get(f"{BASE}/api/foods").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "f1", "name": "Rice"}]})
    )
    respx.get(f"{BASE}/api/organizers/tools").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "t1", "name": "Wok"}]})
    )
    suggestions = respx.get(f"{BASE}/api/recipes/suggestions").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "recipe": {"slug": "fried-rice", "name": "Fried Rice"},
                        "missingFoods": [{"name": "Egg"}],
                        "missingTools": [],
                    }
                ]
            },
        )
    )

    async with Client(build_server(config())) as client:
        result = await client.call_tool("suggest_recipes", {"foods": ["rice"], "tools": ["wok"]})

    params = suggestions.calls.last.request.url.params
    assert params["foods"] == "f1"
    assert params["tools"] == "t1"
    item = data(result)["items"][0]
    assert item["slug"] == "fried-rice"
    assert item["missing_foods"] == ["Egg"]
    assert "missing_tools" not in item or item["missing_tools"] == []


@respx.mock
async def test_set_recipe_image_sends_the_url_without_importing_tags():
    route = respx.post(f"{BASE}/api/recipes/stew/image").mock(
        return_value=httpx.Response(200, json={})
    )

    async with Client(build_server(config())) as client:
        result = await client.call_tool(
            "set_recipe_image", {"slug": "stew", "url": "https://example.com/stew.jpg"}
        )

    body = json.loads(route.calls.last.request.content)
    assert body == {"url": "https://example.com/stew.jpg", "includeTags": False}
    assert data(result) == {"slug": "stew", "image_url": "https://example.com/stew.jpg"}


@respx.mock
async def test_set_recipe_image_reports_an_unknown_slug():
    respx.post(f"{BASE}/api/recipes/ghost/image").mock(return_value=httpx.Response(404))

    async with Client(build_server(config())) as client:
        with pytest.raises(ToolError, match="'ghost' not found"):
            await client.call_tool(
                "set_recipe_image", {"slug": "ghost", "url": "https://example.com/x.jpg"}
            )


async def test_read_only_taxonomy_description_does_not_advertise_writes():
    async with Client(build_server(config(read_only=True))) as client:
        tool = next(t for t in await client.list_tools() if t.name == "manage_taxonomy")

    assert "read-only" in tool.description
    # The write actions may be named as refused, but never as callable.
    for how_to_write in ("needs name", "needs item_id", "data may carry", "items batches"):
        assert how_to_write not in tool.description


async def test_taxonomy_description_documents_writes_when_writable():
    async with Client(build_server(config())) as client:
        tool = next(t for t in await client.list_tools() if t.name == "manage_taxonomy")

    assert "merge" in tool.description and "read-only" not in tool.description
