# mcp-mealie — Design

**Date:** 2026-08-09
**Status:** Superseded by
[`2026-08-10-mealie-mcp-current-state.md`](2026-08-10-mealie-mcp-current-state.md)
— kept as the record of what was designed, not of what was built. Where the two
disagree, the newer file is right.

## Purpose

An MCP server that gives AI agents and coding IDEs full CRUD access to a
self-hosted [Mealie](https://mealie.io) instance: recipes, meal plans, and
cookbooks. Optimized for agent consumption — a flat, verb-first tool surface
with trimmed, token-efficient responses — and for broad client compatibility
via stdio transport.

### Relationship to existing work

`mealie-mcp` on PyPI (Knuckles-Team/mealie-mcp) already exists. It is generated
from Mealie's OpenAPI specification: 247 one-per-endpoint tools in verbose mode,
or 10 action-routed umbrella tools in condensed mode, with no response trimming
and no workflow guidance.

This project takes the opposite approach. Its value is in what it does *not*
return: a curated tool set, aggressively trimmed responses, and a bundled skill
describing real workflows. It is a distinct product, not a fork, and ships under
the name `mcp-mealie`.

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
only. Distributed on PyPI as `mcp-mealie` and run via `uvx mcp-mealie` — no
clone or virtualenv required by users. The import package is `mealie_mcp` under
a `src/` layout; a dist-name/import-name mismatch is idiomatic in Python and
reads better in code, and `src/` prevents tests from importing the source tree
instead of the installed package.

FastMCP permits Python 3.10, but the floor here is 3.11: `uvx` fetches its own
interpreter, so the floor costs users nothing.

Tools are `async`; the `httpx.AsyncClient` is created in FastMCP's lifespan hook
and torn down at shutdown. This preserves the connection pool across calls and
gives the slug and tag caches an obvious home on the client instance.

Rationale for FastMCP: it reduces each tool to a decorated function, which keeps
the diff small for a surface this wide. Stdio is supported by every MCP client
(Claude Code, Claude Desktop, Cursor, Windsurf, Zed), so it alone satisfies the
broad-compatibility goal. TypeScript was considered and rejected — it wins only
when embedding the server in a Node application, which is not a goal here.

HTTP transport and a Docker image are deferred until someone needs remote
hosting.

## Target Mealie version

Stable Mealie 2.x, verified against a pinned Docker tag. Mealie 1.x is not
supported — it lacks the `/api/households/*` layer entirely.

If the startup probe reports a version below 2.0 the server **refuses to
start**. Continuing would produce 404s spread across a whole session instead of
one clear message at launch.

## Authentication and configuration

API token only, supplied through environment variables:

| Variable | Required | Purpose |
| --- | --- | --- |
| `MEALIE_URL` | yes | Base URL of the Mealie instance |
| `MEALIE_API_TOKEN` | yes | Long-lived API token from Mealie's user settings |
| `MEALIE_READ_ONLY` | no | When true, write tools are not registered |
| `MEALIE_VERIFY_SSL` | no | Set false for self-signed certs; defaults to true |
| `MEALIE_LOG_LEVEL` | no | Defaults to INFO |

Username/password login is not supported. Mealie 2.x scopes API tokens to a
household, so no household parameter is needed on any tool — the token decides.

`MEALIE_VERIFY_SSL=false` exists because self-hosted instances frequently sit
behind an internal CA or self-signed certificate, and the alternative is users
hitting an opaque SSL failure on first connect. It is documented as a
homelab-only escape hatch.

Boolean variables accept `1/true/yes/on` and their negations, case-insensitively.
An unrecognized value is a startup **error**, not a silent false — failing open
on a safety flag is the wrong default.

### Startup validation

The server fails fast: missing or malformed `MEALIE_URL` / `MEALIE_API_TOKEN`
produces a clear stderr message and a non-zero exit.

It then makes two probe requests:

- `GET /api/app/about` — reports the Mealie version. This endpoint requires no
  authentication, so it proves reachability only.
- `GET /api/users/self` — returns 401 for a bad token, and names the
  authenticated user on success.

Both are logged as a single line: `connected to Mealie 2.x as <user>`. A server
that starts cleanly and then fails every call is worse inside an IDE than one
that visibly refuses to start.

## Logging

Under stdio transport, stdout *is* the protocol — a stray `print` corrupts the
stream. All logging goes to stderr via the `logging` module, at the level given
by `MEALIE_LOG_LEVEL` (default INFO). Nothing in this codebase writes to stdout.

## Architecture

```
src/mealie_mcp/
  __init__.py
  config.py        # env parsing and validation
  client.py        # httpx.AsyncClient wrapper: auth, error mapping, retries, caches
  shape.py         # pure functions: Mealie JSON -> trimmed dicts
  server.py        # FastMCP instance, lifespan, startup probe, tool registration, main()
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

### Retries

Idempotent `GET` requests retry twice with backoff, covering the common
"container just woke up" case on a homelab box. `POST`, `PATCH`, and `DELETE`
are never retried — Mealie offers no idempotency key, and a retried recipe
creation produces duplicates.

## Tool surface

Eighteen flat tools, chosen over an action-dispatch design because fine-grained
schemas are clearer to models. The one exception is taxonomy management, which
is rare enough to group.

Tool names are unprefixed (`search_recipes`, not `mealie_search_recipes`). MCP
clients already namespace tools by server, and a prefix taxes every tool
description with tokens that buy nothing.

Tools return Python dicts, which FastMCP serializes. Dicts stay chainable and
let clients that support structured content use it; pre-rendered Markdown would
read marginally better but break any model trying to pull a slug out of one
result to feed the next call.

### Recipes (7)

| Tool | Endpoint |
| --- | --- |
| `search_recipes(query?, tags?, categories?, tools?, foods?, require_all?, page=1, limit=20)` | `GET /api/recipes` |
| `get_recipe(slug, full=false)` | `GET /api/recipes/{slug}` |
| `create_recipe(...)` | `POST /api/recipes` then `PATCH /api/recipes/{slug}` |
| `update_recipe(slug, ...)` | `PATCH /api/recipes/{slug}` |
| `delete_recipe(slug, confirm_slug)` | `DELETE /api/recipes/{slug}` |
| `import_recipe_from_url(url, include_tags=true, include_categories=true)` | `POST /api/recipes/create/url` |
| `suggest_recipes(foods?, tools?, max_missing_foods=2, limit=10)` | `GET /api/recipes/suggestions` |

`suggest_recipes` answers "what can I cook with what's in the fridge" — Mealie
provides this server-side, so it should not be reimplemented as a client-side
filter over search results.

Search filtering happens server-side. Returning fifty recipes for the model to
filter wastes tokens the API can save.

`import_recipe_from_url` returns a bare slug string from Mealie; the tool
follows up with a `get_recipe` shaping pass so the model sees what was imported.

### Meal plan (5)

| Tool | Endpoint |
| --- | --- |
| `get_meal_plan(start_date, end_date)` | `GET /api/households/mealplans` |
| `get_todays_meals()` | `GET /api/households/mealplans/today` |
| `add_meal_plan_entry(date, recipe_slug \| title, entry_type)` | `POST /api/households/mealplans` |
| `delete_meal_plan_entry(entry_id)` | `DELETE /api/households/mealplans/{id}` |
| `random_meal_plan(start_date, end_date, entry_type="dinner")` | `POST /api/households/mealplans/random` per day |

`entry_type` is one of breakfast, lunch, dinner, side.

`CreateRandomEntry` accepts `{date, entryType}` — one entry per request. The
tool keeps a date-range signature and loops internally, because "fill my week
randomly" is the actual use case and making the model issue seven calls wastes
the turns this server exists to save. The range is **capped at 14 days**, so a
hallucinated range cannot fire hundreds of writes. The response lists what
landed on each day.

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

`parse_ingredients` turns free text into structured food/unit/quantity objects.

`manage_taxonomy` takes `resource` ∈ foods | units | tags | categories | tools
and `action` ∈ list | create | delete.

With `MEALIE_READ_ONLY=true`, nine tools remain registered: the three recipe
reads, the two meal plan reads, the two cookbook reads, `parse_ingredients`
(which mutates nothing), and `manage_taxonomy` restricted to `action="list"`.

## Write semantics

Mealie's write API has four sharp edges that the tools hide.

### Recipe creation is two calls

`POST /api/recipes` accepts `{name}` only and returns a bare slug string; all
other content requires a follow-up `PATCH /api/recipes/{slug}`. `create_recipe`
performs both, so the model spends one turn rather than two.

If the PATCH fails after the POST succeeded, a stub recipe exists on the server.
The raised error must name the created slug, so the stub is never silently
orphaned.

### Ingredients accept text or structure

`create_recipe` accepts `recipe_ingredient` items that are either plain strings
or structured objects. Strings are routed through `/api/parser/ingredients`
first; dicts pass through untouched. The check is per item, with no extra
parameter — a model writing from a user's "two cups flour, pinch of salt"
produces strings, while a model that already called `parse_ingredients` produces
dicts.

In the string case, `create_recipe` costs up to three HTTP calls: parse, POST,
PATCH.

### Tags and categories are objects, not strings

`RecipeTag` and `RecipeCategory` both require `name` *and* `slug`, so
`tags=["Vegan"]` cannot be sent through as-is. Tools accept plain names, look
each one up against `/api/organizers/{tags,categories}`, and **create the entry
when it does not exist** — Mealie's tags are free-form and users expect "tag
this vegan" to work.

Resolved names are cached like slugs. Any auto-creation is reported in the
tool's response (`created new tag: Vegan`), so a typo surfaces immediately
rather than three weeks later.

### Updates merge tags, replace bodies

`PATCH` with `Recipe-Input` replaces list fields wholesale — there is no append.
A naive `update_recipe(slug, tags=["Vegan"])` would silently erase every
existing tag.

`update_recipe` therefore reads before writing:

- `tags` and `categories` **merge** with existing values by default.
  `replace_tags=True` / `replace_categories=True` force a wipe.
- `recipeIngredient` and `recipeInstructions` **replace**. Nobody appends half a
  method, and merging steps would produce nonsense.

The extra GET is cheap; silent data loss is the worst failure mode in this
server.

## Response shaping

Mealie recipe payloads include nutrition, assets, comments, settings,
timestamps, and nested IDs — a single full recipe can cost 2–3k tokens.

**Detail** responses return: name, slug, description, yield, prep/cook/total
times, ingredients, instructions, tags, categories, `orgURL` (source link),
`rating`, and `notes`. Excluded: nutrition (bulky, usually empty), image (a URL
the model cannot see), tools, and `lastMade`. Passing `full=true` returns the
raw payload.

**Search and list** responses are thinner still: slug, name, description.

**Pagination:** Mealie returns `{items, page, per_page, total}`. Tools return
`items` plus a single-line hint (`showing 20 of 143 — pass page=2`) rather than
the envelope. Default `perPage` is 20.

## Identifiers

Recipes are addressed by slug at the tool boundary — slugs appear in search
output and read well to a model. Meal plan entries, cookbooks, and taxonomy
items are addressed by UUID.

`CreatePlanEntry` requires a `recipeId` UUID, not a slug. `add_meal_plan_entry`
therefore resolves slug → UUID internally via `GET /api/recipes/{slug}`, and
caches the mapping on the client for the process lifetime. Planning a week
re-resolves the same handful of recipes repeatedly; without the cache a
seven-entry plan costs fourteen requests. Slugs do not change often enough under
a running process to justify expiry.

Parameter names state which identifier is expected (`recipe_slug`, `entry_id`,
`cookbook_id`).

## Dates and timezones

Mealie stores plain dates. The server runs on the user's own machine, so the
host's local date is used to resolve "today". `get_todays_meals` echoes the date
it resolved to in its response, making the rare mismatch visible rather than
silent. `add_meal_plan_entry` takes explicit ISO dates — no inference.

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

Failures raise FastMCP's `ToolError` so clients render them as errors:
authentication problems, unreachable servers, 404s, and read-only violations.
Empty-but-valid results are *not* errors — "no recipes matched" is returned as
ordinary content, because raising there makes models retry pointlessly.

## Testing

- `shape.py` — unit tests against JSON fixtures captured from
  `demo.mealie.io` and committed to the repo, with any user data sanitized.
  Real captures carry the messy cases (nulls, empty nested arrays, `extras`)
  that hand-written fixtures omit, and omitting them is exactly how shapers
  break. If fixture drift becomes a problem, add
  `scripts/capture_fixtures.py` against the pinned 2.x container.
- `client.py` — tests using `respx` to mock httpx, covering the auth header,
  each error-mapping branch, the retry policy, the slug and tag caches, and the
  read-only guard.
- `tools/*` — one smoke test per tool asserting registration and parameter
  schema.
- `scripts/smoke.py` — runs the read tools against a live instance on demand.
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
- **Author a recipe** — free-text ingredients straight into `create_recipe`, or
  `parse_ingredients` first when the model wants to inspect the structure.

MCP prompts were considered and rejected: client support is inconsistent, while
a `SKILL.md` works natively in Claude Code and reads as documentation
elsewhere.

## Packaging and licensing

`pyproject.toml` with hatchling. Distribution name `mcp-mealie`, import package
`mealie_mcp`, entry point `mcp-mealie = mealie_mcp.server:main`.

Runtime dependencies: `fastmcp`, `httpx`. Development: `pytest`,
`pytest-asyncio`, `respx`, `ruff`.

Licensed MIT, public repository. Mealie itself is AGPL, but this is a separate
client communicating over HTTP — no derivation, no license inheritance.

No git remote is configured yet; one will be added when there is something worth
pushing.

The README ships copy-paste configuration for Claude Code, Claude Desktop,
Cursor, Windsurf, and Zed. All use the same stdio shape:

```json
{
  "command": "uvx",
  "args": ["mcp-mealie"],
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
