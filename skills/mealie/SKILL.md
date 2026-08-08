---
name: mealie
description: Use when planning meals, saving or editing recipes, or organizing a Mealie recipe library — covers weekly meal planning, importing and filing recipes, writing cookbook filters, and authoring recipes from free text.
---

# Working with Mealie

These workflows assume the `mcp-mealie` MCP server is connected. Tool names
below are its tools.

## Plan a week of meals

1. Read what already exists before adding anything:
   `get_meal_plan(start_date, end_date)` for the target week, and again for the
   week before. The previous week is what tells you which dinners would be
   repeats.
2. Find candidates. `suggest_recipes(foods=[...])` when the user named
   ingredients they have; `search_recipes(tags=["Quick"])` or a cookbook when
   they described a style or constraint.
3. Fill each empty slot with `add_meal_plan_entry(date, entry_type,
   recipe_slug)`. One call per slot — there is no batch endpoint.
4. For a plan the user doesn't want to curate, `random_meal_plan(start_date,
   end_date)` fills the range in one call, honoring any meal plan rules
   configured in Mealie. It adds to existing entries rather than replacing
   them, so check the plan first if the week is partly full.

Free-text entries are allowed: `add_meal_plan_entry(date, title="Leftovers")`
needs no recipe.

Prefer filtering server-side over pulling fifty recipes and sorting them
yourself — `search_recipes` takes tags, categories, and tools, with
`require_all` to switch from any-of to all-of.

## Import a recipe and file it

1. `import_recipe_from_url(url)` — Mealie scrapes it and returns the imported
   recipe.
2. Check what the scraper produced. Sites vary; ingredients and times are often
   incomplete.
3. `update_recipe(slug, tags=[...], categories=[...])` to file it. Tags merge
   with whatever is already there, so this is safe to call repeatedly. Tags
   that don't exist yet are created, and the response says which — read that
   line back to the user so a typo doesn't become a permanent tag.

To overwrite rather than merge, pass `replace_tags=True`.

## Author a recipe from a conversation

`create_recipe` takes free text directly:

```
create_recipe(
  name="Weeknight Dal",
  ingredients=["1 cup red lentils", "2 tbsp ghee", "a thumb of ginger"],
  instructions=["Rinse the lentils.", "Simmer 20 minutes."],
  tags=["Vegetarian"],
)
```

Ingredient lines run through Mealie's parser automatically. Call
`parse_ingredients` first only when you want to show the user how a line will
be interpreted before committing it.

Ingredients and instructions **replace** on update — pass the whole list, not
just the new items.

## Build a cookbook

A Mealie cookbook is a saved filter, not a folder. Its contents update
automatically as recipes match.

```
create_cookbook(name="Weeknight Dinners",
                query_filter='tags.name IN ["Quick"] AND recipeCategory.name IN ["Dinner"]')
```

Filter syntax:

| Pattern | Example |
| --- | --- |
| Match any of | `tags.name IN ["Dinner", "Lunch"]` |
| Match all of | `tags.name CONTAINS ALL ["Vegan", "Quick"]` |
| Combine | `recipeCategory.name IN ["Dessert"] AND rating > 3` |
| By date | `createdAt > "2026-01-01"` |
| By equipment | `tools.name IN ["Air Fryer"]` |

Verify with `get_cookbook_recipes(cookbook_id)` after creating one — an
overly narrow filter matches nothing, which is easy to miss.

## Cautions

- `delete_recipe` is permanent and needs the slug twice:
  `delete_recipe(slug, confirm_slug)`. Confirm with the user first.
- Tag and category names are created on demand. Check spelling before writing.
- `random_meal_plan` is capped at 14 days per call.
- If every tool fails with an auth error, the server's `MEALIE_API_TOKEN` is
  wrong — that is a configuration fix, not something to retry.
