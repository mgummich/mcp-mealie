"""Shaping is where the logic concentrates, so this is where the tests do."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mealie_mcp import shape

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture
def recipe() -> dict:
    return load("recipe_detail")


def test_detail_keeps_the_fields_an_agent_reads(recipe):
    result = shape.recipe_detail(recipe)

    assert result["name"] == "Fixture Roast Chicken"
    assert result["slug"] == "fixture-roast-chicken"
    assert result["yield"] == "4 servings"
    assert result["cook_time"] == "1 hour"
    assert result["source_url"] == "https://example.com/roast-chicken"
    assert sorted(result["tags"]) == ["Dinner", "Roast"]
    assert result["categories"] == ["Main Course"]
    assert len(result["ingredients"]) == 3
    assert result["instructions"] == ["Season the bird.", "Roast at 200C."]
    assert result["notes"] == [{"title": "Tip", "text": "Rest it for 10 minutes."}]


def test_detail_drops_the_bulk(recipe):
    result = shape.recipe_detail(recipe)

    for noisy in ("nutrition", "settings", "assets", "comments", "extras", "id", "image"):
        assert noisy not in result


def test_detail_is_much_smaller_than_the_raw_payload(recipe):
    assert len(json.dumps(shape.recipe_detail(recipe))) < len(json.dumps(recipe)) / 2


def test_empty_values_are_omitted_rather_than_returned_as_null():
    result = shape.recipe_detail({"name": "Bare", "slug": "bare", "description": ""})

    assert result == {"name": "Bare", "slug": "bare"}


def test_rating_appears_only_when_mealie_supplies_one(recipe):
    # Mealie 2.x keeps ratings per user, so a recipe payload often has none.
    assert "rating" not in shape.recipe_detail(recipe)
    assert shape.recipe_detail({**recipe, "rating": 4})["rating"] == 4


def test_summary_is_three_fields(recipe):
    assert set(shape.recipe_summary(recipe)) <= {"slug", "name", "description"}


def test_ingredients_render_as_text(recipe):
    rendered = [shape.ingredient(i) for i in recipe["recipeIngredient"]]

    assert all(isinstance(line, str) and line for line in rendered)
    assert any("chicken" in line for line in rendered)


def test_ingredient_falls_back_when_mealie_has_no_display_string():
    assert shape.ingredient(
        {"quantity": 2.0, "unit": {"name": "cup"}, "food": {"name": "flour"}}
    ) == "2 cup flour"


def test_ingredient_drops_a_zero_quantity():
    assert shape.ingredient({"quantity": 0, "food": {"name": "salt"}, "note": "to taste"}) == (
        "salt to taste"
    )


def test_instruction_prefixes_a_title_when_present():
    assert shape.instruction({"title": "Prep", "text": "Chop."}) == "Prep: Chop."
    assert shape.instruction({"title": "", "text": "Chop."}) == "Chop."


def test_paginated_hints_at_the_next_page():
    page = {"items": [{"name": "a", "slug": "a"}], "total": 143, "perPage": 1}

    result = shape.paginated(page, shape.recipe_summary, page_number=1)

    assert result["count"] == 1
    assert result["total"] == 143
    assert "page=2" in result["note"]


def test_paginated_stays_quiet_when_everything_fits():
    page = {"items": [{"name": "a", "slug": "a"}], "total": 1, "perPage": 20}

    assert "note" not in shape.paginated(page, shape.recipe_summary)


def test_paginated_shapes_a_real_search_page():
    result = shape.paginated(load("recipes_page"), shape.recipe_summary)

    assert result["count"] >= 1
    assert all(set(item) <= {"slug", "name", "description"} for item in result["items"])


def test_meal_plan_entry_flattens_the_linked_recipe():
    page = load("mealplan_page")

    entry = shape.meal_plan_entry(page["items"][0])

    assert entry["date"] == "2026-08-10"
    assert entry["meal"] == "dinner"
    assert entry["recipe_slug"] == "fixture-roast-chicken"
    assert entry["entry_id"]


def test_meal_plan_entry_handles_a_free_text_meal():
    entry = shape.meal_plan_entry(
        {"id": "1", "date": "2026-08-10", "entryType": "lunch", "title": "Leftovers"}
    )

    assert entry["name"] == "Leftovers"
    assert "recipe_slug" not in entry


def test_cookbook_exposes_the_filter():
    book = shape.cookbook(load("cookbook"))

    assert book["name"] == "Fixture Book"
    assert book["query_filter"] == 'tags.name IN ["Dinner"]'
    assert book["cookbook_id"]


def test_parsed_ingredient_flattens_food_and_unit():
    parsed = [shape.parsed_ingredient(p) for p in load("parsed_ingredients")]

    flour = next(p for p in parsed if "flour" in p["input"])
    assert flour["quantity"] == 2.0
    assert flour["unit"] == "cup"
    assert flour["food"] == "flour"


def test_taxonomy_page_shapes_to_id_name_slug():
    result = shape.paginated(load("tags_page"), shape.taxonomy_item)

    assert result["items"]
    assert all(set(item) <= {"id", "name", "slug"} for item in result["items"])
