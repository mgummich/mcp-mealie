# Contributing

One path, top to bottom. Everything here is copy-pasteable.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) — the only thing you install globally; it
  manages Python and the virtualenv.
- Python 3.11, 3.12, or 3.13 (uv will fetch one if you have none).
- Docker, for the integration suite only. Unit tests need nothing.

## Setup

```bash
git clone https://github.com/mgummich/mcp-mealie
cd mcp-mealie
uv sync --extra dev                      # .venv from the committed uv.lock
uv run --extra dev pre-commit install    # lint gates on every commit
```

## `.env`

```bash
cp .env.example .env     # then fill in MEALIE_URL and MEALIE_API_TOKEN
```

Create the token in Mealie under **Profile → API Tokens**. Real environment
variables always win over the file. Unit tests never read it — `conftest.py`
runs every test from an empty directory precisely so your real credentials
cannot leak into a test.

Point a client at your checkout and every edit lands on the next restart:

```bash
claude mcp add mealie -- uv run --directory "$PWD" mcp-mealie
```

## Run the server

```bash
uv run mcp-mealie      # stdio; it will sit there waiting for a client
```

It probes Mealie before the transport starts, so a bad URL or token is one
message on stderr and exit code 2.

## Verify

**Fast gate** — what you run constantly, fully offline, seconds:

```bash
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run --extra dev mypy
uv run --extra dev pytest
```

`uv run --extra dev pre-commit run --all-files` runs the first three plus file
hygiene (YAML, TOML, trailing whitespace, stray private keys), and is what the
commit hook runs on the files you touched. It does **not** run the tests.

**Full gate** — before opening a PR or tagging a release; adds Docker:

```bash
uv run --extra dev pre-commit run --all-files
uv run --extra dev pytest
./scripts/integration.sh                            # newest stable Mealie
MEALIE_TEST_VERSION=v2.8.0 ./scripts/integration.sh # oldest supported
```

CI runs the same checks: the fast gate is `ci.yml`'s `unit` job, across Python
3.11-3.13; the two integration commands are its `integration` matrix.

`ruff format .` (no `--check`) is the one that rewrites files.

### Integration tests

`scripts/integration.sh` starts a throwaway Mealie in Docker on port 19925,
mints a token from its default admin, runs `tests/integration/`, and tears
everything down — including on failure. It never touches your `.env`.

`scripts/smoke.py` is the other one: it hits a live instance of your choosing,
on demand, for when you want to see a tool answer real data.

## Adding a tool

1. Pick the module by domain: `src/mealie_mcp/tools/{recipes,mealplan,shopping,cookbooks,feedback,library,admin}.py`.
2. Add one `@mcp.tool` function inside that module's `register()`. Five of the
   modules split at an `if read_only: return` line — reads above it, writes
   below. `library.py` is reads throughout and has no such line; `admin.py`
   gates `manage_taxonomy` per action instead, via `allowed_actions` and
   `READ_ONLY_DOC`.
3. The signature is the input schema and the docstring is what the model reads
   — write both for a reader who has never seen Mealie. Do not declare an
   output schema; the `SendResultsOnce` middleware strips them on purpose.
4. Return a shaped dict. Add a shaper to `src/mealie_mcp/shape.py` rather than
   returning raw Mealie JSON; a full recipe is 2-3k tokens of fields no agent
   reads.
5. Never retry a write, and take a second confirming argument for anything
   destructive. See [Errors, retries, idempotency][arch].
6. Tests, in this order:
   - the shaper in `tests/test_shape.py`, against a fixture in
     `tests/fixtures/` (capture a real response, trim it to what you assert on);
   - the tool in `tests/test_tools.py`, through a real in-process
     `fastmcp.Client` with `respx` standing in for Mealie;
   - add the name to `READ_TOOLS` or `WRITE_TOOLS` at the top of
     `tests/test_tools.py` — that pair is the read-only contract.
7. Add the tool to the README's tool table. `tests/test_docs.py` fails if the
   table and the registry disagree, including the counts written in prose.

## Which docs to update

| You changed | Update |
| --- | --- |
| A tool: added, removed, renamed | `README.md` table, `CHANGELOG.md` |
| Behavior a user would notice | `CHANGELOG.md`, and `docs/HOWTO.md` if it changes the order of doing things |
| A config variable | `README.md` table, `.env.example`, `docs/ARCHITECTURE.md` |
| Module layout, caching, retries, invariants | `docs/ARCHITECTURE.md` |
| Anything an AI agent needs to work here | `AGENTS.md` |
| Supported Mealie or Python versions | `.github/workflows/ci.yml` first — the docs quote CI, not the other way round |
| The developer workflow | this file |

Roles, so nothing gets written twice: **README** is product, install, and
usage. **CONTRIBUTING** is development. **AGENTS.md** is AI operational
context. **[docs/ARCHITECTURE.md][arch]** is technical truth. **docs/HOWTO.md**
is the end-user tutorial. **CHANGELOG.md** is history. Everything under
`docs/archive/` is historical and authoritative about nothing.

Several of these are checked, not merely asked for: see `tests/test_docs.py`.

## Pull requests

- Branch off `main`, keep the diff to one concern.
- The full gate passes locally before you open it.
- A user-visible change gets a `## [Unreleased]` entry in `CHANGELOG.md`.
- Commit messages: imperative mood, `type: summary` (`feat:`, `fix:`,
  `docs:`, `refactor:`, `test:`, `chore:`).

## Releasing

A release is a git tag; the `Release` workflow re-runs the fast gate, builds,
and cuts the GitHub Release from the changelog section. The integration suite
is on you before you tag. Nothing is published to a
package index. Full procedure in [`docs/RELEASING.md`](docs/RELEASING.md).

[arch]: docs/ARCHITECTURE.md
