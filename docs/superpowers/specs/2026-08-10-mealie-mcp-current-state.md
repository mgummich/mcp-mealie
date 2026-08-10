# mcp-mealie — Current State

**Date:** 2026-08-10
**Status:** Current
**Supersedes:** [`2026-08-09-mealie-mcp-design.md`](2026-08-09-mealie-mcp-design.md)
**Describes:** `v0.2.1` plus the unreleased write-path fixes on `main` (`0e4e02c`)

This is a description of what the server does today, not a plan for what it
should become. Where it differs from the original design document, this file
is right and that one is history. Deliberate omissions are recorded here too —
a curated server is defined as much by what it refuses to expose.

## Purpose

An MCP server over a self-hosted [Mealie](https://mealie.io) instance, built
for agents rather than for API coverage. Twenty-five curated tools across
recipes, meal plans, cookbooks, library reports, and taxonomy, with responses
trimmed hard enough that a recipe costs a few hundred tokens instead of a few
thousand — and sent once, not in the two copies MCP would otherwise put on the
wire.

The competing `mealie-mcp` generates 247 tools from Mealie's OpenAPI. The value
here is in the subtraction.

## Coverage

Measured against a live Mealie 3.22.0: its OpenAPI reports **175 paths / 259
operations**; the server calls **22 literal paths** (plus the `tag` and
`categorize` bulk actions), roughly **14% of the API**.

**Covered:** recipe search/read/create/update/delete, URL import, suggestions,
images by URL and by file, bulk tag/categorize, meal plans (read, today, add,
delete, random), cookbooks (full CRUD plus contents), ingredient parsing,
taxonomy management including server-side merge, and three library-wide reports
that correspond to no Mealie endpoint at all.

**Deliberately not covered:** shopping lists, recipe timeline and `last-made`,
comments, ratings per user, `duplicate`, assets, exports, zip/HTML/image
import, mealplan rules, webhooks, notifications, recipe actions, AI provider
configuration, seeders, migrations, reports, `explore/*`, `shared/*`,
`media/*`, and the whole of `/api/admin/*` and `/api/users/*` except
`users/self`. Of these, shopping lists and the timeline are the two whose
absence an agent notices; the rest are administrative.

## Runtime and transport

Python ≥3.11, [FastMCP](https://github.com/jlowin/fastmcp) ≥2.10, stdio only.
Import package `mealie_mcp` under `src/`, distribution name `mcp-mealie`, entry
point `mcp-mealie = mealie_mcp.server:main`.

Runtime dependencies: `fastmcp`, `httpx`, `python-dotenv`. Development:
`pytest`, `pytest-asyncio`, `respx`, `ruff`, `mypy`, `pre-commit`.

Tools are `async`; one `httpx.AsyncClient` is created in FastMCP's lifespan
hook and closed at shutdown, which preserves the connection pool and gives the
caches an obvious home.

### Responses are sent once

A `SendResultsOnce` middleware strips `output_schema` from every listed tool
and `structured_content` from every result. MCP otherwise returns each value
twice — as a text block and as `structuredContent` — and both cross the wire.
On a library-wide sweep that duplicate is thousands of tokens. The declared
output schemas said no more than "an object", so dropping them costs nothing,
and it is the declared schema that obliges the server to send the structured
copy at all.

## Target Mealie version

The startup probe refuses anything below 2.0 and starts on everything above.
CI's integration job runs against a pinned `ghcr.io/mealie-recipes/mealie:v2.8.0`.
Mealie 3.22.0 is verified by hand and works, but is not covered by any
automated test. Mealie 1.x is unsupported — it has no `/api/households/*`
layer.

## Configuration

Environment variables, loaded from the process environment first and a `.env`
found upward from the working directory second.

| Variable | Required | Purpose |
| --- | --- | --- |
| `MEALIE_URL` | yes | Base URL, trailing slashes stripped; must be http(s) |
| `MEALIE_API_TOKEN` | yes | Long-lived API token from Mealie's user settings |
| `MEALIE_READ_ONLY` | no | When true, write tools are not registered |
| `MEALIE_VERIFY_SSL` | no | Set false for self-signed certs; defaults true |
| `MEALIE_LOG_LEVEL` | no | Defaults to INFO |

Booleans accept `1/true/yes/on` and `0/false/no/off`, case-insensitively. An
unrecognized value is a startup error — failing open on a safety flag is the
wrong default.

Token auth only. Mealie scopes API tokens to a household, so no tool takes a
household parameter.

### Startup

`main()` validates the environment, then probes `GET /api/app/about` (version,
unauthenticated) and `GET /api/users/self` (token validity, username), logging
one line: `connected to Mealie <version> as <user>`. Any failure exits 2 with a
single stderr message, before the transport starts. Under stdio, stdout *is*
the protocol — all logging goes to stderr and nothing writes to stdout.

## Architecture

```
src/mealie_mcp/
  __init__.py      # __version__, the single source hatchling reads
  config.py        # env parsing and validation
  client.py        # httpx wrapper: auth, error mapping, retries, caches, sweeps
  shape.py         # pure functions: Mealie JSON -> trimmed dicts
  server.py        # FastMCP instance, middleware, lifespan, probe, main()
  tools/
    recipes.py     # 10 tools
    mealplan.py    # 5
    cookbooks.py   # 5
    library.py     # 3 — whole-library reports
    admin.py       # 2 — parse_ingredients, manage_taxonomy
tests/
  test_client.py test_shape.py test_tools.py test_config.py test_version.py
  fixtures/        # captured Mealie payloads
  integration/     # opt-in, live instance, compose.yml pins Mealie 2.8.0
scripts/
  integration.sh   # spins up Mealie in Docker, mints a token, runs the suite
  smoke.py         # read tools against a live instance, on demand
```

One direction of dependency: `client.py` speaks HTTP and knows nothing about
MCP; `shape.py` is pure functions with no I/O; `tools/*` are thin FastMCP
decorators that call the client and then a shaper.

No generated OpenAPI client. Hand-written httpx calls over ~22 paths are a
smaller diff than generated models plus a codegen step.

**The bundled skill lives elsewhere.** The original design placed
`skills/mealie/SKILL.md` in this repo; it is now a separate project
(`mealie-skill`). This repo ships tools only.

### Caches

Three, all process-lifetime, all on the client instance:

- **slug → UUID**, because `CreatePlanEntry` needs a `recipeId` and planning a
  week re-resolves the same handful of recipes.
- **taxonomy name → object**, one paged sweep per resource, because a name
  missing from a truncated snapshot would make `resolve_taxonomy` create a
  second copy of a food that already exists.
- **slug → full recipe body**, so the foods and units reports do not each pay
  for their own library sweep. Any write clears it wholesale; growing past
  `MAX_CACHED_DETAILS` (2000) drops it rather than evicting entry by entry.

`forget_recipe` and `forget_taxonomy` invalidate after a rename, delete, or
external change.

### Retries

`GET` retries twice with linear backoff on transport errors and 5xx — the
homelab "container just woke up" case. `POST`/`PATCH`/`PUT`/`DELETE` are never
retried: Mealie has no idempotency key and a retried create duplicates the
recipe. Timeout is 15 seconds.

### Sweeps

`all_recipes` pages the recipe list at 100/request up to `MAX_LIBRARY_RECIPES`
(2000); `taxonomy_items` pages at 500/request. `recipe_details` fans out
per-recipe GETs at `FANOUT` = 8 concurrent. These exist because Mealie has no
"how many recipes use this food" endpoint, and the alternative is the model
issuing one search per name.

## Tool surface

Twenty-five flat, unprefixed tools. Fine-grained schemas read better to a model
than an action-dispatch design; the one exception is taxonomy management, which
is rare enough to group. Tools return dicts, which stay chainable — a model
pulls a slug out of one result and feeds the next call.

### Recipes (10)

| Tool | Endpoint |
| --- | --- |
| `search_recipes(query?, tags?, categories?, tools?, foods?, require_all=false, fields?, page=1, limit=20)` | `GET /api/recipes` |
| `get_recipe(slug, full=false, fields?)` | `GET /api/recipes/{slug}` |
| `suggest_recipes(foods?, tools?, max_missing_foods=2, limit=10)` | `GET /api/recipes/suggestions` |
| `create_recipe(name, description?, ingredients?, instructions?, recipe_yield?, prep_time?, cook_time?, tags?, categories?, source_url?)` | `POST /api/recipes` then `PATCH` |
| `update_recipe(slug, …, replace_tags=false, replace_categories=false, replace_tools=false)` | `GET` then `PATCH /api/recipes/{slug}` |
| `set_recipe_image(slug, url)` | `POST /api/recipes/{slug}/image` |
| `upload_recipe_image(slug, path)` | `PUT /api/recipes/{slug}/image` |
| `bulk_tag_recipes(slugs, tags?, categories?)` | `POST /api/recipes/bulk-actions/{tag,categorize}` |
| `delete_recipe(slug, confirm_slug)` | `DELETE /api/recipes/{slug}`, bulk fallback |
| `import_recipe_from_url(url, include_tags=true, include_categories=true)` | `POST /api/recipes/create/url` |

`limit` is capped at 100 per page. `fields` narrows what each result carries;
`search_recipes` rejects `ingredients`, `instructions` and `notes` because
Mealie's list payload has no such thing — `get_recipe` is the only way there.

`upload_recipe_image` reads the file where the *server* runs, and sends the
extension as its own multipart field because Mealie's handler names the stored
file from it. Accepted: `.jpg .jpeg .png .webp .gif`.

`import_recipe_from_url` flags a failed scrape. A page that renders its recipe
in the browser still imports as a 201 with placeholder text, which reads like
content unless someone looks.

### Meal plan (5)

| Tool | Endpoint |
| --- | --- |
| `get_meal_plan(start_date, end_date)` | `GET /api/households/mealplans` |
| `get_todays_meals()` | `GET /api/households/mealplans/today` |
| `add_meal_plan_entry(date, entry_type="dinner", recipe_slug? \| title?, note?)` | `POST /api/households/mealplans` |
| `delete_meal_plan_entry(entry_id)` | `DELETE /api/households/mealplans/{id}` |
| `random_meal_plan(start_date, end_date, entry_type="dinner")` | `POST /api/households/mealplans/random` per day |

`entry_type` ∈ breakfast, lunch, dinner, side. Dates are explicit ISO strings;
a non-ISO date is rejected before any request. `random_meal_plan` loops
internally because "fill my week" is the real use case, and the range is capped
at 14 days so a hallucinated range cannot fire hundreds of writes.

Mealie stores plain dates and the server runs on the user's machine, so "today"
is the host's local date. `get_todays_meals` echoes the date it resolved to,
making the rare mismatch visible.

### Cookbooks (5)

| Tool | Endpoint |
| --- | --- |
| `list_cookbooks()` | `GET /api/households/cookbooks` |
| `get_cookbook_recipes(cookbook, page=1, limit=20)` | `GET /api/recipes?cookbook=…` |
| `create_cookbook(name, description?, tags?, categories?, tools?, require_all=false, query_filter?, public=false)` | `POST /api/households/cookbooks` |
| `update_cookbook(cookbook_id, …)` | `PATCH /api/households/cookbooks/{id}` |
| `delete_cookbook(cookbook_id)` | `DELETE /api/households/cookbooks/{id}` |

A cookbook is a saved filter, not a collection: its contents come from the
recipe search endpoint and reuse the search shaper.

`query_filter` maps to Mealie's `queryFilterString` DSL. The tool description
carries worked examples, pinned by a test — without them models invent invalid
filter syntax. Tags, categories and tools can also be given as plain names,
which are resolved to Mealie's canonical casing before they enter the filter,
because that parser is case-sensitive.

`update_cookbook` exists, contrary to the original design's "delete plus create
covers it" — it does not, since deleting loses the cookbook's id.

### Library reports (3)

Not in the original design. Each sweeps the library server-side instead of
having the model issue one search per name.

| Tool | What it answers |
| --- | --- |
| `library_stats(resource, include_unused=true, top=50, max_recipes=2000)` | how many recipes use each tag/category/tool/food/unit; what is unused and safe to delete |
| `find_duplicate_recipes(max_recipes=2000)` | recipes whose names match once punctuation and case are ignored |
| `check_recipe_links(check_sources=true, check_images=true, max_recipes=200)` | dead source URLs and recipes with no image |

Tags, categories and tools are read off the recipe list — a handful of
requests. Foods and units are not in that payload and need one request per
recipe; that sweep honors `max_recipes` and is the reason the detail cache
exists.

`check_recipe_links` probes source URLs with HEAD and a streamed-GET fallback,
**unauthenticated** — the Mealie token is never sent to a third party. Hosts
answering 401/403/405/429/501 are reported as unverified, not broken. Images
are not probed: Mealie stores them itself, so what is reported is recipes that
have no image at all.

Every report names what it truncated (`scanned 200 of 260 — raise max_recipes`).
A silent cap reads as "covered everything".

### Utility and taxonomy (2)

| Tool | Endpoint |
| --- | --- |
| `parse_ingredients(lines[])` | `POST /api/parser/ingredients` |
| `manage_taxonomy(resource, action="list", name?, item_id?, search?, page=1, data?, merge_into?, items?)` | `/api/foods`, `/api/units`, `/api/groups/labels`, `/api/organizers/{tags,categories,tools}` |

`resource` ∈ foods, units, labels, tags, categories, tools.
`action` ∈ list, create, update, merge, delete.

- `merge` is foods and units only — those are the resources with a server-side
  merge endpoint that repoints every recipe. For tags and categories the tool
  says so rather than faking it.
- `items` batches every action except list: twenty-five renames are one call,
  and one bad item lands in `errors` without stranding the other twenty-four.
- `update` reads the current row first, because Mealie's `PUT` replaces it
  wholesale.
- Listing returns 50 per page — a food carries description, plural name, label
  and aliases, so 200 of them is a 12k-token reply to a question the first few
  rows answer.

`parse_ingredients` currently always uses Mealie's default `nlp` parser; the
API also offers `brute` and `openai`, which the tool does not expose.

### Read-only mode

With `MEALIE_READ_ONLY=true`, **twelve** tools remain registered: the three
recipe reads, the two meal plan reads, the two cookbook reads, the three
library reports, `parse_ingredients`, and `manage_taxonomy` restricted to
`action="list"`. Its docstring is swapped for a read-only variant rather than
describing writes that will be refused.

## Write semantics

Mealie's write API has sharp edges the tools hide.

### Recipe creation is two calls

`POST /api/recipes` accepts `{name}` only and returns a bare slug string;
everything else needs a follow-up `PATCH`. If the PATCH fails, a stub exists on
the server, and the raised error names the slug so it is never silently
orphaned.

### Ingredients accept text or structure, per item

Strings route through `/api/parser/ingredients`; dicts pass through. The check
is per item with no extra parameter — a model writing from "two cups flour"
produces strings, one that already called `parse_ingredients` produces dicts.

`IngredientFood`/`IngredientUnit` require an id, and the parser happily returns
foods it never matched. Rather than silently creating taxonomy for every
ingredient, unresolved names fold back into the note. When *nothing* resolved,
the whole source line becomes the note and **the parsed quantity is dropped** —
otherwise the amount is stored twice and Mealie renders "500 500 g flour".

### Write payloads carry only writable keys

Taxonomy objects come off a read with `createdAt`, `updatedAt`,
`householdsWithIngredientFood`, `label`, `aliases`, `extras`. `client.writable()`
trims every one that enters a write to `id`, `name`, `slug`, `groupId` — in
`resolve_taxonomy`, in the ingredient normalizer, and on the existing side of a
tag merge. Mealie 3.22 tolerates the fat payload; other versions have been
reported to answer 500, and Mealie hydrates the row from the id regardless.

### Tags and categories are objects, not strings

`RecipeTag`/`RecipeCategory` need name *and* slug, so `tags=["Vegan"]` cannot
be sent through. Tools take plain names, resolve them case-insensitively
against a cached snapshot, and **create what does not exist** — Mealie's tags
are free-form and users expect "tag this vegan" to work. Every auto-creation is
reported in the response (`created new tags: Vegan`), so a typo surfaces at
once.

Filtering is the opposite: `taxonomy_slugs` never creates and never errors,
because filtering on a tag that does not exist should return nothing rather
than raise.

### Updates merge tags, replace bodies

`PATCH` replaces list fields wholesale — there is no append, and a naive
`update_recipe(slug, tags=["Vegan"])` would erase every existing tag. So
`update_recipe` reads before writing: `tags`, `categories` and `tools` merge by
default, with `replace_*` flags to force a wipe; ingredients, instructions and
notes always replace. The extra GET is cheap; silent data loss is the worst
failure mode here.

### Renaming changes the slug

Mealie re-derives the slug from the name, so after a rename the slug the caller
passed is already a 404. The PATCH response body carries the new slug, which
avoids a second read, and the result carries `renamed_from` — a model reading
only `slug` would otherwise notice the move when its next call fails.

## Response shaping

A full Mealie recipe costs 2–3k tokens: nutrition, assets, comments, settings,
timestamps, nested ids.

- **Detail** returns name, slug, description, yield, prep/cook/total times,
  ingredients, instructions, tags, categories, tools, source_url, rating,
  notes. `full=true` returns the raw payload.
- **Search and list** return slug, name, description — or exactly the `fields`
  asked for.
- **Pagination**: tools return `items` plus a one-line hint
  (`showing 20 of 143 — pass page=2`), not Mealie's envelope.
- Empty values are dropped rather than serialized as nulls.

## Identifiers

Recipes are addressed by slug at the tool boundary — slugs appear in search
output and read well to a model. Meal plan entries, cookbooks and taxonomy
items are addressed by UUID. Parameter names state which is expected
(`recipe_slug`, `entry_id`, `cookbook_id`, `item_id`).

## Safety

MCP clients frequently auto-approve tool calls, so:

1. `MEALIE_READ_ONLY=true` prevents write tools from being registered at all;
   attempting one in that mode gives an explicit "server is in read-only mode".
2. `delete_recipe(slug, confirm_slug)` requires both to match, and fails before
   any request is sent.
3. `random_meal_plan` caps its range at 14 days.
4. `check_recipe_links` never sends the Mealie token off-instance.

## Error handling

Failures raise FastMCP's `ToolError`, or `MealieError` — a subclass carrying
the HTTP status, so a tool can branch on the status instead of on message text.
Raw tracebacks and raw Mealie bodies never reach the model.

| Condition | Message |
| --- | --- |
| 401 / 403 | `authentication failed — check MEALIE_API_TOKEN` |
| 404 | resource-specific, e.g. `recipe 'x' not found` |
| 422 | FastAPI's `detail`, trimmed to the failing fields |
| 5xx (after GET retries) | `Mealie returned 500: <detail>`, or `— the server is unhealthy` when the body is empty |
| timeout / connection error | `Mealie unreachable at {url}: …` |

`delete_recipe` uses the status: a 5xx falls back to
`POST /api/recipes/bulk-actions/delete` with the recipe's UUID, which gets
through when the ORM cannot cascade the row-level delete. The result says the
fallback ran. A 404 still fails, so a typo is not answered by a second attempt.

Empty-but-valid results are **not** errors — "no recipes matched" is ordinary
content, because raising there makes models retry pointlessly.

## Testing

- `pytest` with `asyncio_mode = "auto"`; 124 unit tests plus 5 integration.
- `shape.py` against committed JSON fixtures — real captures carry the messy
  cases (nulls, empty nested arrays, `extras`) that hand-written ones omit.
- `client.py` with `respx`: auth header, every error branch, retry policy,
  caches, taxonomy paging, metadata trimming.
- `tools/*`: registration and the read-only guard, the payload builders, and
  the failure paths that matter (delete fallback, a 404 that must *not* fall
  back, unknown-food search).
- `tests/integration/` is opt-in via `MEALIE_INTEGRATION=1`; `scripts/integration.sh`
  spins up a throwaway Mealie in Docker, mints a token, runs it, tears it down.
  **It runs in CI**, contrary to the original design.
- A test pins the install tag in `README.md` and `docs/HOWTO.md` to
  `__version__`, so a release cannot leave a command that looks current and
  installs the previous server.

CI runs ruff (check and format), mypy, and pytest on 3.11/3.12/3.13, plus the
integration job.

## Packaging and distribution

**Not on PyPI.** Releases are GitHub Releases with the wheel and sdist
attached; installs read the git tag:

```json
{
  "command": "uvx",
  "args": ["--from", "git+https://github.com/mgummich/mcp-mealie@v0.2.1", "mcp-mealie"],
  "env": { "MEALIE_URL": "https://mealie.example.com", "MEALIE_API_TOKEN": "…" }
}
```

The publish step needed a PyPI account that does not exist and failed on 0.2.0
without uploading anything; `docs/RELEASING.md` records what putting it on an
index would take. The tag is therefore the distribution, and the pin matters —
without one, `uv` resolves whatever `main` holds that day.

Hatchling builds; `__version__` in `src/mealie_mcp/__init__.py` is the single
source. The release workflow reads its notes from the matching `CHANGELOG.md`
section and fails when there is none. Docs publish to GitHub Pages.

MIT, public repo. Mealie is AGPL, but this is a separate client over HTTP — no
derivation.

## Still deferred

Docker image, HTTP transport, PyPI publishing. Feature-wise, in the order they
would be worth adding: shopping lists, recipe timeline and `last-made`,
`duplicate`, and Mealie 3.x's LLM hooks (`parser="openai"`,
`test-scrape-url?useOpenAI`, recipe-from-photo).
