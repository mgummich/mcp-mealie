# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `bulk_tag_recipes(slugs, tags, categories)` — adds tags and categories to
  many recipes through Mealie's bulk-action endpoints, creating names that do
  not exist yet. Adds only; removal stays with `update_recipe`.
- `upload_recipe_image(slug, path)` — sends a local image file as a recipe's
  image, including the `extension` form field Mealie's multipart handler
  requires. `set_recipe_image` still covers images already on the web.
- `library_stats(resource)` — usage rollup for tags, categories, tools, foods,
  or units in one call: every item with its recipe count, unused ones included.
  Mealie has no endpoint for this, so it sweeps the recipe list server-side
  instead of costing one search per name.
- `find_duplicate_recipes()` — groups recipes whose names match once case and
  punctuation are ignored.
- `check_recipe_links()` — probes source URLs from outside Mealie (never
  sending the API token to a third party) and lists recipes with no image.
- `update_cookbook(cookbook_id, ...)` — rename or re-filter a cookbook in
  place, instead of delete-and-recreate losing the id.
- `create_cookbook` and `update_cookbook` accept `tags`, `categories`, `tools`,
  and `require_all`, building the `queryFilterString` server-side with Mealie's
  stored name casing. `query_filter` remains for filters names can't express.
- `manage_taxonomy` accepts `items=[...]` to batch create, update, merge, or
  delete in a single call, reporting per-item failures rather than stopping at
  the first bad id.
- `search_recipes` accepts `fields` — the same projection `get_recipe` has,
  minus ingredients, instructions, and notes, which Mealie's search payload
  does not carry.
- `set_recipe_image(slug, url)` — set or repair a recipe's image from an
  image URL.
- `manage_taxonomy` gains `update` and `merge` actions. `update` patches onto
  the current row, so food descriptions, plural names, aliases, label
  assignments, unit abbreviations, and organizer renames are all reachable.
  `merge` uses Mealie's own `/api/foods/merge` and `/api/units/merge`, which
  repoint every recipe that used the duplicate.
- `manage_taxonomy` supports the `labels` resource (`/api/groups/labels`), so
  food labels can be created, renamed, and assigned.
- `update_recipe` accepts `notes`, `tools`, `rating`, and `replace_tools`.
- `get_recipe` returns `tools` in the default view.

### Removed

- The bundled skill (`skills/`). Its workflows moved to
  [mealie-skill](https://github.com/mgummich/mealie-skill), which already
  covers the same instance and now drives this server directly. Two skills for
  one server meant two descriptions in every prompt and two copies of the same
  guidance to keep in sync.

### Changed

- Tool results are sent once, as JSON text. MCP returns a tool's value both as
  a text block and as `structuredContent`, and both cross the wire; the
  duplicate is pure token cost for a client that reads the text. Output
  schemas — which said no more than "an object", and are what oblige the
  server to send the structured copy — go with it. A 300-tag
  `library_stats` went from roughly 12k tokens on the wire to 5k.
- `library_stats` takes `top` (default 50): it lists that many used items and
  reports how many there are in total. Every row carries a UUID, and the tail
  of the used list is what nobody reads. Unused items are still listed in
  full, since those are the ones worth acting on.

### Fixed

- `manage_taxonomy` `list` silently truncated at 200 rows. It now takes a
  `page` argument and reports the total plus the next page to request.
- `update_recipe` reported `recipe 'x' not found` after successfully renaming
  one. Mealie derives the slug from the name, so the read-back used a slug
  that no longer existed. The update response is now read from the PATCH
  itself and carries the new slug, plus `renamed_from` so the caller can see
  the slug moved rather than infer it.
- `import_recipe_from_url` returned a recipe reading "Could not detect
  ingredients" as though the import had worked. Pages that render their recipe
  in the browser now come back with a note saying the scrape found nothing.
- A 500 from Mealie was always reported as "the server is unhealthy",
  discarding the response body. Whatever Mealie sent back is now included.

## [0.1.0] - 2026-08-09

### Added

- Initial release: 18 curated MCP tools over Mealie recipes, meal plans,
  cookbooks, and taxonomy.
- Read-only mode (`MEALIE_READ_ONLY`) that hides all write tools.
- Ingredient parsing, tag resolution by name, tag-preserving updates, and
  multi-day random meal planning built into the tools.
- Bundled agent skill at `skills/mealie/SKILL.md`.

[0.1.0]: https://github.com/mgummich/mcp-mealie/releases/tag/v0.1.0
