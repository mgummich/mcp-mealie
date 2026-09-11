# Architecture

Technical truth for this repository. The README says what the product is; this
says how it is built and which rules a change must not break. Where any other
document disagrees with this one about code behavior, this one is right — and
the disagreement is a bug to fix.

## Scope

An MCP server that lets an agent run a self-hosted [Mealie](https://mealie.io)
instance: recipes, meal plans, shopping lists, cookbooks, cooking history, and
library cleanup. Forty-three curated tools, not one per endpoint — most of
Mealie's API stays unexposed on purpose.

**Non-goals.** Full Mealie API coverage. No admin, user, group, household,
backup, or migration management. No agent loop, no ACP, no prompts or resources: this server exposes
tools and nothing else. No caching layer beyond the short in-process one below.
No PyPI package (see [RELEASING.md](RELEASING.md)).

## Module boundaries

```
src/mealie_mcp/
  __init__.py   __version__ — the single source of version truth
  config.py     MEALIE_* env vars -> frozen Config; validates at startup
  client.py     HTTP to Mealie: retries, path safety, concurrency, caches
  shape.py      pure Mealie JSON -> trimmed dicts; no I/O
  server.py     FastMCP wiring: lifespan, startup probe, tool registration
  tools/        one module per domain; each exposes register(mcp, get_client, read_only)
    recipes.py    search/get/suggest/create/update/import/image/bulk-tag/delete
    mealplan.py   meal plan reads and entry writes, random_meal_plan
    shopping.py   shopping lists and items
    cookbooks.py  cookbooks and their recipes
    feedback.py   cooking history: made, timeline, ratings, comments
    library.py    rollup reports: library_stats, duplicates, link check
    admin.py      parse_ingredients, manage_taxonomy
```

Dependency direction is one-way: `tools/*` → `client` + `shape`; `server` →
`tools`; `config` → `client` (for one default). `shape.py` imports nothing from
this package. Nothing imports `server` except the console script and tests.

Tests mirror that layout: `tests/test_shape.py`, `tests/test_client.py`,
`tests/test_tools.py`, `tests/test_config.py`.

## Request flow

```
MCP client --stdio--> FastMCP --> tools/<domain>.<tool>()
                                    |
                                    +-- client.request(method, path, ...)  -> Mealie HTTP API
                                    +-- client.all_recipes (paged sweep)
                                    +-- client.resolve_taxonomy/recipe_details (cached helpers)
                                    |
                                    +-- shape.<shaper>(raw json) -> trimmed dict
                                    |
                    SendResultsOnce middleware strips structuredContent
MCP client <--------- one JSON text block
```

A tool function is the whole contract: its signature is the input schema, its
docstring is what the model reads, and its return value is the response. Adding
a tool means adding one decorated function in the right `tools/` module.

## Configuration

`Config.from_env()` reads `MEALIE_*`, loading the nearest `.env` by walking up
from the working directory; real environment variables always win. Unrecognized
boolean values are a startup error, never a silent `false`.

| Variable | Required | Default | Field |
| --- | :---: | :---: | --- |
| `MEALIE_URL` | yes | — | `url` (trailing slashes stripped; must be http/https) |
| `MEALIE_API_TOKEN` | yes | — | `token` |
| `MEALIE_READ_ONLY` | no | `false` | `read_only` |
| `MEALIE_VERIFY_SSL` | no | `true` | `verify_ssl` |
| `MEALIE_LOG_LEVEL` | no | `INFO` | `log_level` |
| `MEALIE_MAX_CONCURRENCY` | no | `4` | `max_concurrency` |

`main()` validates config, then probes `/api/app/about` (reachability and
version) and `/api/users/self` (token) **before** the stdio transport starts, so
a misconfiguration is one message on stderr and exit code 2 rather than every
tool call failing separately.

Logging goes to stderr only. Under stdio transport stdout *is* the protocol.
Credentials are never logged: the token lives in an httpx header and no log
statement formats a header, a request body, or a `Config`.

## Read-only mode

`MEALIE_READ_ONLY=true` means write tools are **never registered** — not
registered-then-refused. Each `tools/*.register()` returns early after its read
tools, so the write ones are not in `list_tools()` at all. `manage_taxonomy` is
the one tool on both sides of the line: in read-only mode it registers with only
the `list` action allowed, and a different description.

The authoritative lists are `READ_TOOLS` and `WRITE_TOOLS` in
`tests/test_tools.py`; `test_read_only_mode_hides_writes` fails if a new tool
lands on the wrong side.

## Errors, retries, idempotency

- Every failure reaching a caller is a `ToolError` (`MealieError` subclasses it
  and carries the status). Messages name the thing that failed and what to do.
- **GETs** retry twice on transport errors and 5xx, with linear backoff
  (`GET_RETRIES`, `RETRY_BACKOFF_SECONDS`).
- **Writes are never retried.** Mealie has no idempotency key, so a retried
  create duplicates the recipe. Do not add retry to a non-GET path.
- Irreversible whole-object deletes require the identifier twice:
  `delete_recipe(slug, confirm_slug)`, `delete_shopping_list(id, confirm_id)`.
  Single-row deletes — a shopping item, a comment, a meal plan entry — take it
  once.
- Partial batch writes report per-item failures instead of stopping at the
  first one, and a failure after a create still hands back the created slug.
- A timeout is reported as a timeout, not as unreachability — different cause,
  different fix for the person reading it.

## Path safety and outbound requests

Slugs and IDs are interpolated into request paths, so `check_path()` rejects
dot segments, backslashes, `?`/`#`, and their percent-encodings before any
request is built (`UNSAFE_PATH` in `client.py`). Without it,
`get_recipe("../users/self")` would reach an endpoint no tool exposes.

`check_recipe_links` is the only code that fetches a URL chosen by stored data.
It uses a separate httpx client carrying **no** Mealie credentials, follows
redirects itself one hop at a time, and resolves each hop's address, refusing
anything that is not globally routable. Loopback, private, and link-local
destinations are never requested.

## Caching and concurrency

- One `asyncio.Semaphore` per client caps outbound Mealie requests at
  `max_concurrency` (default 4), **shared** across every concurrent tool call,
  so a fan-out inside one tool and ten parallel tool calls add up to the same
  ceiling.
- `FANOUT` (8) bounds how many of a sweep's own calls run at once; the
  semaphore bounds Mealie requests in flight across everything.
- Recipe bodies and taxonomy snapshots are cached in-process for
  `CACHE_TTL_SECONDS` (300). Writes through this client clear the cached recipe bodies;
  the TTL is what covers edits made in Mealie's UI or by another client. The
  detail cache is dropped rather than grown past `MAX_CACHED_DETAILS`.
- `POST /api/parser/ingredients` mutates nothing and so does not invalidate the
  cache (`NON_MUTATING_POSTS`).
- Library sweeps page at `LIBRARY_PAGE_SIZE` (100) up to `MAX_LIBRARY_RECIPES`
  (2000); taxonomy sweeps at `TAXONOMY_PAGE_SIZE` (500). A sweep that hits the
  cap says so rather than presenting partial evidence as complete.
- The client is a module-global created by the server lifespan. One server per
  process is the supported deployment; `get_client` is passed to tool modules as
  a function precisely because the client only exists while the lifespan runs.

## Token efficiency

This is a product requirement, not a preference. A full Mealie recipe is 2-3k
tokens of nutrition blocks, assets, settings, and nested IDs an agent never
reads.

- `shape.py` is where that gets cut. Raw Mealie JSON must not reach a caller:
  per-entity responses go through a shaper, and the rollups in `library.py`
  build their own projections.
- `_clean()` drops keys that are `None`, `""`, `[]`, or `{}`.
- The `SendResultsOnce` middleware strips `structuredContent` and the output
  schemas that oblige MCP to send it, so each result crosses the wire once
  instead of twice. Do not declare output schemas.
- Reads take a `fields` projection where a sweep would otherwise need a call
  per recipe (`search_recipes(fields=[...])` projects the way `get_recipe`
  does).
- Paginated results carry the total and a next-page hint computed from
  offset and page size — never a hint past the last page.
- Tool docstrings are prompt text and are paid for on every request. Worth
  spending words on a syntax a model gets wrong otherwise (cookbook filters);
  not worth restating a parameter the signature already shows.

## Test layers

| Layer | File | What it uses |
| --- | --- | --- |
| Shapers | `tests/test_shape.py` | captured fixtures in `tests/fixtures/`, no I/O |
| Client | `tests/test_client.py` | `respx`-mocked HTTP |
| Tools | `tests/test_tools.py` | a real in-process `fastmcp.Client` over `build_server()`, `respx`-mocked Mealie |
| Config | `tests/test_config.py` | env vars via `monkeypatch` |
| Docs | `tests/test_docs.py` | the repo's own files and the tool registry; no network |
| Live | `tests/integration/test_live.py` | a real Mealie in Docker; skipped unless `MEALIE_INTEGRATION=1` |

Unit tests are fully offline. `tests/conftest.py` chdirs every test into an
empty directory so a developer's real `.env` cannot supply what a test just
removed from the environment.

## Compatibility

Mealie **2.0 or newer**. The startup probe refuses 1.x, which has no
`/api/households` endpoints. CI runs the integration suite against **2.8.0**
(oldest supported) and **3.25.1** (newest stable) on every push; those two
versions are the whole basis of the compatibility claim, and
`tests/test_docs.py` checks that the README's claim and the CI matrix still
name the same versions.

Mealie 3.x only: `import_recipe_from_images`, and the `snack`, `drink`, and
`dessert` meal plan entry types. On 2.x, Mealie itself rejects those with its
own message; this server does not gate them.

Python 3.11-3.13, all three in CI.

## Version and release

`__version__` in `src/mealie_mcp/__init__.py` is the only place a version is
declared. `pyproject.toml` reads it through hatch. Two copies are written by
hand — the `@vX.Y.Z` install pin in `README.md` and `docs/HOWTO.md` — and
`tests/test_docs.py` pins both to `__version__`, so a half-bumped release fails
the gates instead of shipping.

A release is a git tag. The `Release` workflow re-runs the gates, checks the tag
against `__version__`, builds wheel and sdist, reads the matching `CHANGELOG.md`
section as release notes, and cuts a GitHub Release. There is no package index
in the loop. Full procedure: [RELEASING.md](RELEASING.md).

## Source-of-truth map

| Fact | Lives in | Checked by |
| --- | --- | --- |
| Version | `src/mealie_mcp/__init__.py` | `tests/test_docs.py`, `release.yml` |
| Which tools exist | `@mcp.tool` functions in `src/mealie_mcp/tools/` | `tests/test_tools.py`, `tests/test_docs.py` |
| Which tools are reads | `READ_TOOLS` in `tests/test_tools.py` | `test_read_only_mode_hides_writes` |
| Tool list and counts in prose | `README.md` | `tests/test_docs.py` |
| Config variables | `Config` in `src/mealie_mcp/config.py` | `tests/test_docs.py` (README table, `.env.example`) |
| Supported Mealie versions | `.github/workflows/ci.yml` matrix | `tests/test_docs.py` |
| Supported Python versions | `.github/workflows/ci.yml` matrix | — |
| Response shapes | `src/mealie_mcp/shape.py` | `tests/test_shape.py` |
| Release history | `CHANGELOG.md` | `release.yml` |
| Product / install / usage | `README.md` | — |
| Developer workflow | `CONTRIBUTING.md` | — |
| AI operational context | `AGENTS.md` | — |
| End-user tutorial | `docs/HOWTO.md` | — |
| Technical truth | this file | — |
| Anything under `docs/archive/` | nothing — historical only | — |

`docs/REPO_AUDIT.md` is the dated record of the 2026-09-12 cleanup: how this
map was arrived at, not a source of truth for anything in it.
