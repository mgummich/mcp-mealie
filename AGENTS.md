# AGENTS.md

Operational context for AI coding agents working on this repository. Read this
first; load the files it names, not the repository.

## Purpose

An MCP server for [Mealie](https://mealie.io): 43 curated tools over recipes,
meal plans, shopping lists, cookbooks, cooking history, and library cleanup.
Curated, not generated — most of Mealie's API is deliberately unexposed.
Token efficiency is a product requirement, not a preference.

Non-goals: full API coverage, admin/user/household management, an agent loop,
MCP prompts or resources, a PyPI package.

## Repo map

```
src/mealie_mcp/
  __init__.py   __version__ — the only place a version is declared
  config.py     MEALIE_* env vars -> frozen Config, validated at startup
  client.py     HTTP to Mealie: retries, path safety, semaphore, caches
  shape.py      pure Mealie JSON -> trimmed dicts; no I/O; heavily tested
  server.py     FastMCP wiring: lifespan, startup probe, tool registration
  tools/        recipes mealplan shopping cookbooks feedback library admin
tests/          test_shape test_client test_tools test_config test_docs
tests/fixtures/         captured Mealie responses for the shaper tests
tests/integration/      live Mealie in Docker; skipped without MEALIE_INTEGRATION=1
scripts/integration.sh  throwaway Mealie + integration tests + teardown
scripts/smoke.py        hit a live instance on demand
docs/archive/           historical; authoritative about nothing
```

Dependency direction: `tools/*` → `client` + `shape`; `server` → `tools`.
`shape.py` imports nothing from this package.

**Where things are.** A tool lives in the `tools/` module matching its domain,
inside `register()`. Its response shaper lives in `shape.py`. Its test lives in
`tests/test_tools.py`; the shaper's in `tests/test_shape.py`. For most tasks
that is three files, and `rg '<tool_name>'` finds all of them.

Inside `register()`, reads go above the `if read_only: return` line and writes
below it. `library.py` has no such line (all reads); `admin.py` gates
`manage_taxonomy` per action instead.

## Authoritative docs

| Question | File |
| --- | --- |
| How is it built, what must not break | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| How do I set up, verify, add a tool | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| What is it, how do users install it | [`README.md`](README.md) |
| What changed | [`CHANGELOG.md`](CHANGELOG.md) |
| How does a user get started | [`docs/HOWTO.md`](docs/HOWTO.md) |
| How is it released | [`docs/RELEASING.md`](docs/RELEASING.md) |

Do not treat anything under `docs/archive/` as current.

## Commands

```bash
uv sync --extra dev                          # setup
uv run --extra dev pytest                    # fastest useful signal
```

**Fast gate** (offline, seconds — run before claiming anything works):

```bash
uv run --extra dev ruff check . && \
uv run --extra dev ruff format --check . && \
uv run --extra dev mypy && \
uv run --extra dev pytest
```

**Full gate** (adds Docker; the same checks CI runs):

```bash
uv run --extra dev pre-commit run --all-files   # lint, format, mypy, file hygiene
uv run --extra dev pytest
./scripts/integration.sh
MEALIE_TEST_VERSION=v2.8.0 ./scripts/integration.sh
```

Never report a check as passing without running it.

## Invariants

Breaking one of these is a bug even if the tests are green.

1. **Writes are never retried.** Mealie has no idempotency key; a retried
   create duplicates the recipe. GETs retry twice; nothing else does.
2. **Read-only mode does not register write tools**, rather than registering
   and refusing them. `MEALIE_READ_ONLY=true` must leave exactly the tools in
   `READ_TOOLS` (`tests/test_tools.py`) and no others.
3. **Raw Mealie JSON never reaches a caller.** Per-entity responses go through
   a `shape.py` shaper; the rollups in `library.py` build their own
   projections.
4. **No output schemas.** A declared schema obliges MCP to send
   `structuredContent`, which doubles every response on the wire; the
   `SendResultsOnce` middleware strips both. Do not reintroduce them.
5. **Interpolated slugs and IDs go through `check_path()`.** Without it a slug
   like `../users/self` reaches an endpoint no tool exposes.
6. **Irreversible whole-object deletes take the identifier twice**
   (`delete_recipe`, `delete_shopping_list`). Single-row deletes
   (`delete_shopping_item`, `delete_recipe_comment`, …) do not.
7. **Nothing but stderr.** Under stdio, stdout is the protocol. Credentials are
   never logged — no log line formats a header, a body, or a `Config`.
8. **One shared semaphore** caps outbound Mealie requests. Do not create a
   second httpx client for Mealie, and do not bypass `client.request()`.
9. **`check_recipe_links` refuses non-global addresses** on every redirect hop,
   and carries no Mealie credentials.
10. **`__version__` is the only version.** Two hand-written copies exist (the
    install pin in `README.md` and `docs/HOWTO.md`) and `tests/test_docs.py`
    pins them.

Details and rationale: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Token efficiency

- A full Mealie recipe is 2-3k tokens; a shaped one is a few hundred. Shapers
  drop `None`, `""`, `[]`, `{}` via `_clean()`.
- Prefer a projection (`fields=[...]`) or a rollup over making the caller loop.
- Tool docstrings are prompt text, paid for on every request. Spend words on
  syntax a model otherwise gets wrong (cookbook filters); do not restate what
  the signature already shows.
- Paginated results carry the total and a next-page hint computed from offset
  and page size — never a hint past the last page.

## Testing

- New logic gets a test at the lowest layer that can hold it: shaper →
  `tests/test_shape.py` with a fixture; HTTP behavior → `tests/test_client.py`
  with `respx`; tool contract → `tests/test_tools.py` through a real in-process
  `fastmcp.Client`.
- A new tool must be added to `READ_TOOLS` or `WRITE_TOOLS` in
  `tests/test_tools.py`, and to the README tool table.
- Unit tests are offline. No test may require a network or a real Mealie
  outside `tests/integration/`.
- Do not weaken an assertion to make a change pass.

## Doc sync

`tests/test_docs.py` mechanically checks version pins, the README tool table
and its spelled-out counts, config variable names and defaults, the Mealie
compatibility claim against the CI matrix, and internal links. If it fails, the
prose is wrong or the code is — fix the disagreement, do not relax the check.

Which document to update for which change: the table in
[`CONTRIBUTING.md`](CONTRIBUTING.md#which-docs-to-update).

## Compatibility

Mealie 2.0+; the startup probe refuses 1.x. CI runs the integration suite
against 2.8.0 and 3.25.1, and those two versions are the entire basis of the
claim — do not widen it in prose without widening the matrix. Mealie 3.x only:
`import_recipe_from_images` and the `snack`/`drink`/`dessert` entry types.
Python 3.11-3.13.

## Definition of done

- The fast gate passes, run — not assumed.
- The full gate passes for anything touching `client.py`, an endpoint, or
  compatibility.
- New behavior has a test at the right layer; no assertion was weakened.
- The invariants above still hold.
- `CHANGELOG.md` has an `## [Unreleased]` entry if a user would notice.
- Docs the change touches are updated, and `tests/test_docs.py` agrees.
- No new dependency without a reason that survives "could stdlib do it".
