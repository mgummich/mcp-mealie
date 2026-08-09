---
name: mealie-mcp
description: Use when planning meals, saving or editing recipes, or organizing a Mealie recipe library — covers weekly meal planning, importing and filing recipes, writing cookbook filters, authoring recipes from free text, and cleaning up duplicate foods, units, and tags.
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
3. `update_recipe(slug, tags=[...], categories=[...], tools=[...])` to file it.
   All three merge with whatever is already there, so this is safe to call
   repeatedly. Names that don't exist yet are created, and the response says
   which — read that line back to the user so a typo doesn't become a
   permanent tag.
4. If the scraper missed the photo, `set_recipe_image(slug, url)` fetches one
   from an image URL. It replaces whatever is there.

To overwrite rather than merge, pass `replace_tags=True` (or
`replace_categories` / `replace_tools`).

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

Ingredients, instructions, and notes **replace** on update — read the recipe
first and pass the whole list back, not just the new items.

`update_recipe` also writes `notes` and `rating`. Notes are
`{"title": ..., "text": ...}` objects; a plain string becomes an untitled note.
To append a note, `get_recipe(slug, fields=["notes"])` first and pass the old
notes plus the new one.

## See what the library actually looks like

Start a cleanup with the rollup, not with a scan. `library_stats(resource)`
sweeps every recipe once, server-side, and returns each tag, category, tool,
food, or unit with its `recipe_count`, sorted highest first, unused entries
included.

| Question | Call |
| --- | --- |
| Which tags are unused? | `library_stats("tags")` — read the zero-count rows |
| Is this food safe to delete? | `library_stats("foods")` — `recipe_count: 0` |
| Same recipe imported twice? | `find_duplicate_recipes()` |
| Which source links are dead? | `check_recipe_links()` |

Do not build this index yourself with one `search_recipes` per name — that is
the pass `library_stats` replaces. `foods` and `units` are the slow ones: they
need each recipe's ingredients, so that sweep is one request per recipe and
honors `max_recipes`. Tags, categories, and tools come off the recipe list in a
handful of requests.

`find_duplicate_recipes` groups by name with punctuation and case ignored, so
two different takes on "Pancakes" land in the same group — read the group
before deleting anything.

`check_recipe_links` probes source URLs from outside Mealie and reports dead
ones; hosts that refuse the probe come back under `unverified_sources`, not
`broken_sources`. Images are not probed — Mealie stores them itself — so what
it reports is recipes with no image at all, repairable with
`set_recipe_image(slug, url)`.

To sweep the library for something these three don't cover, use
`search_recipes(fields=[...])` rather than a `get_recipe` per hit:
`search_recipes(fields=["slug", "tags", "rating"], limit=100)`. Ingredients,
instructions, and notes are not in the search payload; those still need
`get_recipe`.

## Tidy up the library

`manage_taxonomy(resource, action, ...)` covers foods, units, labels, tags,
categories, and tools.

| Goal | Call |
| --- | --- |
| See what exists | `manage_taxonomy("foods", "list", search="onion")` |
| Rename | `manage_taxonomy("tags", "update", item_id=..., name="Weeknight")` |
| Describe a food | `manage_taxonomy("foods", "update", item_id=..., data={"description": ..., "pluralName": ...})` |
| Label a food | `manage_taxonomy("foods", "update", item_id=..., data={"labelId": ...})` |
| Fold a duplicate away | `manage_taxonomy("foods", "merge", item_id=<loser>, merge_into=<keeper>)` |
| Remove | `manage_taxonomy("units", "delete", item_id=...)` |

Every action except `list` takes `items` instead of the single-item arguments,
and runs the whole batch in one call:

```
manage_taxonomy("foods", "update", items=[
  {"item_id": "...", "name": "Scallion"},
  {"item_id": "...", "data": {"labelId": "..."}},
])
```

The reply splits `results` from `errors`, each error carrying the index and the
item that failed, so one bad id doesn't strand the rest. Use the batch form
whenever you have more than two changes queued — twenty-five renames are one
call, not twenty-five.

Notes:

- **Merge, don't delete-and-retype.** `merge` exists for foods and units only,
  and it repoints every recipe that used the loser. Deleting a duplicate food
  instead strips it from those recipes. Tags and categories have no merge
  endpoint — re-tag the recipes with `update_recipe`, then delete the leftover.
- **`list` is paged at 200.** The reply carries `total` and, when there is
  more, the page to ask for next. A library with 400 foods needs two calls;
  don't conclude anything from page one alone.
- **`update` is a patch.** Fields you don't mention keep their current value.
- Get a `labelId` from `manage_taxonomy("labels", "list")`; create labels with
  `action="create"` on the same resource.

## Build a cookbook

A Mealie cookbook is a saved filter, not a folder. Its contents update
automatically as recipes match.

Pass names and let the server write the filter:

```
create_cookbook(name="Weeknight Dinners", tags=["Quick"], categories=["Dinner"])
```

`require_all=True` switches from any-of (`IN`) to all-of (`CONTAINS ALL`).
Names are matched to Mealie's stored casing on the way in, so `["quick"]` still
produces a filter that matches.

Reach for `query_filter` only for what names can't express — dates, ratings,
mixed operators. It cannot be combined with the name lists:

```
create_cookbook(name="Recent Favorites",
                query_filter='rating > 3 AND createdAt > "2026-01-01"')
```

To change a cookbook, use `update_cookbook(cookbook_id, ...)` — never delete
and recreate, which throws away the id. It takes the same filter arguments, and
only the fields you pass change; `query_filter=""` clears the filter entirely.

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
- `manage_taxonomy` `merge` and `delete` are not reversible. Confirm which side
  wins with the user before merging.
- `random_meal_plan` is capped at 14 days per call.
- If every tool fails with an auth error, the server's `MEALIE_API_TOKEN` is
  wrong — that is a configuration fix, not something to retry.
