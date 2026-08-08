# mealie-mcp — Design

**Date:** 2026-08-09
**Status:** Approved

## Purpose

An MCP server that gives AI agents and coding IDEs full CRUD access to a
self-hosted [Mealie](https://mealie.io) instance: recipes, meal plans, and
cookbooks. Optimized for agent consumption — a flat, verb-first tool surface
with trimmed, token-efficient responses — and for broad client compatibility
via stdio transport.

## Scope

**In scope (v1):** recipe search/read/create/update/delete, URL import, recipe
suggestions, meal plan read/write, cookbooks, ingredient parsing, and taxonomy
management (foods, units, tags, categories, tools).

**Out of scope (v1):** shopping lists, favorites and ratings, comments, recipe
timeline and `last-made`, bulk tag/categorize/delete actions, image/zip/HTML
import, backups, webhooks, and all admin/user/group management. These endpoints
exist in the Mealie API and were reviewed; they were deliberately deferred.

## Runtime and transport

Python ≥3.11 with [FastMCP](https://github.com/jlowin/fastmcp), stdio transport
only. Distributed on PyPI and run via `uvx mealie-mcp` — no clone or virtualenv
required by users.

Rationale: FastMCP reduces each tool to a decorated function, which keeps the
diff small for a surface this wide. Stdio is supported by every MCP client
(Claude Code, Claude Desktop, Cursor, Windsurf, Zed), so it alone satisfies the
broad-compatibility goal. TypeScript was considered and rejected — it wins only
when embedding the server in a Node application, which is not a goal here.

HTTP transport and a Docker image are deferred until someone needs remote
hosting.

## Authentication

API token only, supplied through environment variables:

| Variable | Required | Purpose |
| --- | --- | --- |
| `MEALIE_URL` | yes | Base URL of the Mealie instance |
| `MEALIE_API_TOKEN` | yes | Long-lived API token from Mealie's user settings |
| `MEALIE_READ_ONLY` | no | When true, write tools are not registered |

Username/password login is not supported. Mealie 2.x scopes API tokens to a
household, so no household parameter is needed on any tool — the token decides.

## Architecture

```
mealie_mcp/
  __init__.py
  config.py        # env parsing: MEALIE_URL, MEALIE_API_TOKEN, MEALIE_READ_ONLY
  client.py        # httpx.AsyncClient wrapper: auth header, base URL, error mapping
  shape.py         # pure functions: Mealie JSON -> trimmed dicts
  server.py        # FastMCP instance, tool registration, main()
  tools/
    recipes.py
    mealplan.py
    cookbooks.py
    admin.py       # manage_taxonomy, parse_ingredients
skills/mealie/SKILL.md
tests/
scripts/smoke.py
pyproject.toml
```

Three layers with one direction of dependency:

- `client.py` speaks HTTP and knows nothing about MCP.
- `shape.py` is pure functions from Mealie JSON to trimmed dicts — no network,
  no I/O, fully unit-testable against fixtures. This is where the real logic
  lives.
- `tools/*` are thin FastMCP decorators that call the client, then a shaper.

No generated OpenAPI client. Mealie's spec has 170+ paths; this server touches
roughly fifteen. Hand-written httpx calls are a smaller diff than generated
models plus a codegen step in the build.

## Tool surface

Eighteen flat tools, chosen over an action-dispatch design because
fine-grained schemas are clearer to models. The one exception is taxonomy
management, which is rare enough to group.

### Recipes (7)

| Tool | Endpoint |
| --- | --- |
| `search_recipes(query?, tags?, categories?, tools?, foods?, require_all?, page=1, limit=20)` | `GET /api/recipes` |
| `get_recipe(slug, full=false)` | `GET /api/recipes/{slug}` |
| `create_recipe(...)` | `POST /api/recipes` |
| `update_recipe(slug, ...)` | `PATCH /api/recipes/{slug}` |
| `delete_recipe(slug, confirm_slug)` | `DELETE /api/recipes/{slug}` |
| `import_recipe_from_url(url)` | `POST /api/recipes/create/url` |
| `suggest_recipes(foods?, tools?, max_missing_foods=2, limit=10)` | `GET /api/recipes/suggestions` |

`suggest_recipes` answers "what can I cook with what's in the fridge" — Mealie
provides this server-side, so it should not be reimplemented as a client-side
filter over search results.

Search filtering happens server-side. Returning fifty recipes for the model to
filter wastes tokens the API can save.

### Meal plan (5)

| Tool | Endpoint |
| --- | --- |
| `get_meal_plan(start_date, end_date)` | `GET /api/households/mealplans` |
| `get_todays_meals()` | `GET /api/households/mealplans/today` |
| `add_meal_plan_entry(date, recipe_slug \| title, entry_type)` | `POST /api/households/mealplans` |
| `delete_meal_plan_entry(entry_id)` | `DELETE /api/households/mealplans/{id}` |
| `random_meal_plan(start_date, end_date)` | `POST /api/households/mealplans/random` |

`entry_type` is one of breakfast, lunch, dinner, side.

### Cookbooks (4)

| Tool | Endpoint |
| --- | --- |
| `list_cookbooks()` | `GET /api/households/cookbooks` |
| `get_cookbook_recipes(cookbook, limit=20)` | `GET /api/recipes?cookbook=…` |
| `create_cookbook(name, description?, query_filter?, public=false)` | `POST /api/households/cookbooks` |
| `delete_cookbook(cookbook_id)` | `DELETE /api/households/cookbooks/{id}` |

A cookbook is a saved filter, not a recipe collection: its contents come from
`GET /api/recipes?cookbook={id_or_slug}`, which reuses the search shaper.

`create_cookbook`'s `query_filter` maps to Mealie's `queryFilterString` DSL
(for example `tags.name IN ["Dinner"] AND rating > 3`). **Requirement:** the
tool description must include at least four worked DSL examples. Without them
models invent invalid filter syntax.

`update_cookbook` is omitted — delete plus create covers it.

### Utility and admin (2)

| Tool | Endpoint |
| --- | --- |
| `parse_ingredients(lines[])` | `POST /api/parser/ingredients` |
| `manage_taxonomy(resource, action, ...)` | `/api/foods`, `/api/units`, `/api/organizers/{tags,categories,tools}` |

`parse_ingredients` turns free text into structured food/unit/quantity objects,
so `create_recipe` can accept human-written ingredient lines instead of
requiring the model to guess Mealie's ingredient schema.

`manage_taxonomy` takes `resource` ∈ foods | units | tags | categories | tools
and `action` ∈ list | create | delete.

With `MEALIE_READ_ONLY=true`, nine tools remain registered: the three recipe
reads, the two meal plan reads, the two cookbook reads, `parse_ingredients`
(which mutates nothing), and `manage_taxonomy` restricted to `action="list"`.

## Response shaping

Mealie recipe payloads include nutrition, assets, comments, settings,
timestamps, and nested IDs — a single full recipe can cost 2–3k tokens.

- **Detail** responses return curated fields: name, slug, description, yield,
  prep/cook/total times, ingredients, instructions, tags, categories. Passing
  `full=true` returns the raw payload.
- **Search and list** responses are thinner still: slug, name, description.
- **Pagination:** Mealie returns `{items, page, per_page, total}`. Tools return
  `items` plus a single-line hint (`showing 20 of 143 — pass page=2`) rather
  than the envelope. Default `perPage` is 20.

## Identifiers

Recipes are addressed by slug — slugs appear in search output and read well to
a model. Meal plan entries, cookbooks, and taxonomy items are addressed by
UUID. Parameter names state which is expected (`recipe_slug`, `entry_id`,
`cookbook_id`).

## Safety

Two guardrails, since MCP clients frequently auto-approve tool calls:

1. `MEALIE_READ_ONLY=true` prevents write tools from being registered at all.
   When a write is attempted in this mode the error is explicit: "server is in
   read-only mode".
2. `delete_recipe(slug, confirm_slug)` requires both arguments to match. A
   mismatch fails before any request is sent.

## Error handling

HTTP status codes map to short, actionable messages. Raw tracebacks and raw
Mealie error bodies are never returned to the model.

| Condition | Message |
| --- | --- |
| 401 / 403 | `authentication failed — check MEALIE_API_TOKEN` |
| 404 | `recipe 'x' not found` (resource-specific) |
| 422 | Mealie's validation detail, trimmed to the failing fields |
| 5xx, timeout, connection error | `Mealie unreachable at {url}` |

Request timeout is 15 seconds.

## Testing

- `shape.py` — unit tests against JSON fixtures captured from
  `demo.mealie.io`. Pure functions, no network. This is the primary test
  target, since shaping is where the logic concentrates.
- `client.py` — tests using `respx` to mock httpx, covering the auth header,
  each error-mapping branch, and the read-only guard.
- `tools/*` — one smoke test per tool asserting registration and parameter
  schema.
- `scripts/smoke.py` — runs the read tools against `demo.mealie.io` on demand.
  Not part of the automated suite; no live-Mealie tests in CI.

## Bundled skill

`skills/mealie/SKILL.md` documents workflows that the tool list alone does not
teach:

- **Plan a week** — read the existing plan, find gaps, fill them with
  `suggest_recipes` or `search_recipes`, then one `add_meal_plan_entry` per
  slot. Includes avoiding repeats from the prior week.
- **Import and file a recipe** — `import_recipe_from_url`, then `update_recipe`
  to tag and categorize.
- **Build a cookbook** — `queryFilterString` DSL patterns and examples.
- **Author a recipe** — `parse_ingredients` on free text, then `create_recipe`.

MCP prompts were considered and rejected: client support is inconsistent, while
a `SKILL.md` works natively in Claude Code and reads as documentation
elsewhere.

## Packaging

`pyproject.toml` with hatchling. Entry point:
`mealie-mcp = mealie_mcp.server:main`.

Runtime dependencies: `fastmcp`, `httpx`. Development: `pytest`, `respx`,
`ruff`.

The README ships copy-paste configuration for Claude Code, Claude Desktop,
Cursor, Windsurf, and Zed. All use the same stdio shape:

```json
{
  "command": "uvx",
  "args": ["mealie-mcp"],
  "env": {
    "MEALIE_URL": "https://mealie.example.com",
    "MEALIE_API_TOKEN": "..."
  }
}
```

Deferred: Docker image, HTTP transport, CI pipeline, and release automation.

## API reference

Design was validated against the Mealie OpenAPI specification fetched from
`https://demo.mealie.io/openapi.json` (`info.version: nightly`, 2026-08-09).
