# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-08-10

### Changed

- Releases are cut as GitHub Releases with the wheel and sdist attached,
  instead of being published to PyPI. Installs read the tag directly, so the
  tag is the distribution; the publish step needed a PyPI account that does
  not exist and failed on 0.2.0 without uploading anything. `docs/RELEASING.md`
  records what putting it on an index would take.
- The release workflow reads its notes from the matching `CHANGELOG.md`
  section and fails when there is none, rather than publishing an empty
  release. Its three jobs collapse into one — they existed to hand artifacts
  between runners for the upload that is now gone.

### Fixed

- The install tag in `README.md` and `docs/HOWTO.md` is pinned to
  `__version__` by a test. Both files carry it since installs come from git,
  and a release that bumped the version without touching them left a command
  that looks current but installs the previous server.

## [0.2.0] - 2026-08-09

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
- `library_stats` takes `top` (default 50): used and unused items each get
  that many rows, and the reply reports how many there are in total. Every row
  carries a UUID, and neither the tail of the used list nor a 193-item unused
  list is what anybody reads — on a real instance the tags rollup went from
  20k to 8k characters.
- `manage_taxonomy` `list` returns 50 rows a page rather than 200. A food
  carries description, plural name, label, and aliases, so a full page was a
  12k-token reply; it is now 3k, and the pagination note already says how to
  ask for the rest.
- The foods and units rollups fetch each recipe body once per process instead
  of once per call. Mealie has no ingredient-usage endpoint, so those two
  reports sweep the whole library; asking for foods and then units paid for
  the same 262 requests twice. Any write drops the cache. Measured on a
  262-recipe instance: 12.3s cold, 2.5s warm.
- `search_recipes` caps `limit` at 100 rows a page. Mealie will return the
  whole library in one reply if asked, and the reply is the expensive part;
  the pagination note already says how to ask for the rest.
- Requests are logged at DEBUG as method, path, and status — set
  `MEALIE_LOG_LEVEL=DEBUG` to see them. The token is never logged.

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
- A malformed item in a `manage_taxonomy` batch failed the whole call. Only
  `ToolError` was caught, so a `data` value that was not an object raised
  `TypeError` past the per-item handler and stranded the other writes. Such an
  item is now reported in `errors` like any other bad one.
- `create_recipe` and `import_recipe_from_url` sent a follow-up request to
  `/api/recipes/None` when Mealie's create response carried no slug, surfacing
  as a confusing 404. They now say the recipe exists but cannot be read back.
- `create_recipe` and `update_recipe` raised an internal error when Mealie's
  ingredient parser returned fewer results than lines sent. That mismatch is
  now a plain tool error, and nothing is written.
- The recipe-body cache is bounded. A whole-library sweep pinned every fetched
  recipe for the life of the process; past `MAX_CACHED_DETAILS` entries the
  cache is dropped instead of grown.
- Parsing ingredients no longer drops that cache. `POST /api/parser/ingredients`
  writes nothing, but counted as a write, so every `create_recipe` made the
  next foods or units rollup sweep the library again.
- Taxonomy snapshots are paged instead of read as one 1000-row request. Past
  that many foods, a name that already existed looked missing, and
  `resolve_taxonomy` created a duplicate of it; `library_stats` reported the
  same items as unnamed. Both now walk every page.
- `manage_taxonomy` no longer documents `create`, `update`, `merge`, and
  `delete` when the server is read-only, where all four are refused.
- `scripts/smoke.py` printed `null` for every tool and crashed on `--write`.
  It read `result.data`, which is empty since results stopped being sent
  twice; it now decodes the JSON text block like the tests do.

## [0.1.0] - 2026-08-09

### Added

- Initial release: 18 curated MCP tools over Mealie recipes, meal plans,
  cookbooks, and taxonomy.
- Read-only mode (`MEALIE_READ_ONLY`) that hides all write tools.
- Ingredient parsing, tag resolution by name, tag-preserving updates, and
  multi-day random meal planning built into the tools.
- Bundled agent skill at `skills/mealie/SKILL.md`.

[0.2.1]: https://github.com/mgummich/mcp-mealie/releases/tag/v0.2.1
[0.2.0]: https://github.com/mgummich/mcp-mealie/releases/tag/v0.2.0
[0.1.0]: https://github.com/mgummich/mcp-mealie/releases/tag/v0.1.0
