# 🍽️ mcp-mealie

**An MCP server for [Mealie](https://mealie.io), built for agents rather than for API coverage.**

[![Release](https://img.shields.io/github/v/tag/mgummich/mcp-mealie?label=release)](https://github.com/mgummich/mcp-mealie/releases)
[![CI](https://github.com/mgummich/mcp-mealie/actions/workflows/ci.yml/badge.svg)](https://github.com/mgummich/mcp-mealie/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-github.io-blue)](https://mgummich.github.io/mcp-mealie/)

Twenty-five curated tools over recipes, meal plans, cookbooks, and library
cleanup, with responses trimmed hard enough that a recipe costs a few hundred
tokens instead of a few thousand — and sent once, not in the two copies MCP
would otherwise put on the wire.

Works with any MCP client that speaks stdio: Claude Code, Claude Desktop,
Cursor, Windsurf, Zed.

---

## 🚀 Install

No clone or virtualenv needed — `uvx` builds it straight from the tag.

```json
{
  "command": "uvx",
  "args": [
    "--from",
    "git+https://github.com/mgummich/mcp-mealie@v0.2.0",
    "mcp-mealie"
  ],
  "env": {
    "MEALIE_URL": "https://mealie.example.com",
    "MEALIE_API_TOKEN": "your-token"
  }
}
```

Create the token in Mealie under **Settings → API Tokens**.

> [!NOTE]
> Not on PyPI yet, so installs come from git — `uvx mcp-mealie` on its own
> will not resolve. Keep the `@v0.2.0` pin: without a tag you get whatever
> `main` holds the day `uv` resolves it.

New to this? The [howto](https://mgummich.github.io/mcp-mealie/howto)
([`docs/HOWTO.md`](docs/HOWTO.md)) walks the whole path in order — token,
read-only first session, first writes, and the behaviors that surprise people
once. Full docs, including the
[changelog](https://mgummich.github.io/mcp-mealie/changelog), live at
[mgummich.github.io/mcp-mealie](https://mgummich.github.io/mcp-mealie/).

### Claude Code

```bash
claude mcp add mealie \
  --env MEALIE_URL=https://mealie.example.com \
  --env MEALIE_API_TOKEN=your-token \
  -- uvx --from git+https://github.com/mgummich/mcp-mealie@v0.2.0 mcp-mealie
```

### Claude Desktop, Cursor, Windsurf, Zed

Add the JSON block above under `mcpServers` in the client's config file.

### From a local clone

Working on the server itself? Point the client at the checkout and every edit
lands on the next restart, no push and no reinstall:

```bash
claude mcp add mealie -- uv run --directory /path/to/mcp-mealie mcp-mealie
```

`--directory` is what makes `uv` resolve the project from the checkout rather
than from the client's working directory. Credentials can come from the repo's
own `.env` here, so the `--env` flags are optional.

### Updating

`uvx` caches the revision it first resolved, so a plain restart keeps running
the old one. To move to a newer release, change the tag in the command and drop
the cached build:

```bash
uv cache clean mcp-mealie    # then restart the MCP client
```

Restart the client after any config change; it only reads the file at startup.
Released versions are listed in the
[changelog](https://mgummich.github.io/mcp-mealie/changelog).

## ⚙️ Configuration

| Variable | Required | Default | Purpose |
| --- | :---: | :---: | --- |
| `MEALIE_URL` | ✅ | — | Base URL of your Mealie instance |
| `MEALIE_API_TOKEN` | ✅ | — | Long-lived API token |
| `MEALIE_READ_ONLY` | — | `false` | Hide every write tool |
| `MEALIE_VERIFY_SSL` | — | `true` | Set false for self-signed certs (homelab only) |
| `MEALIE_LOG_LEVEL` | — | `INFO` | Log verbosity, to stderr |

Booleans accept `1/true/yes/on` and their negations. An unrecognized value is a
startup error rather than a silent false.

These can also live in a `.env` file in the working directory (or any parent) —
copy `.env.example` to `.env` and fill it in. Real environment variables always
take precedence over the file.

> [!NOTE]
> Requires Mealie **2.0 or newer**. The server checks at startup and refuses to
> run against 1.x, which has no `/api/households` endpoints.

## 🧰 Tools

| Category | Tools |
| --- | --- |
| 🥘 **Recipes** | `search_recipes` · `get_recipe` · `suggest_recipes` · `create_recipe` · `update_recipe` · `set_recipe_image` · `upload_recipe_image` · `bulk_tag_recipes` · `delete_recipe` · `import_recipe_from_url` |
| 📅 **Meal plans** | `get_meal_plan` · `get_todays_meals` · `add_meal_plan_entry` · `delete_meal_plan_entry` · `random_meal_plan` |
| 📚 **Cookbooks** | `list_cookbooks` · `get_cookbook_recipes` · `create_cookbook` · `update_cookbook` · `delete_cookbook` |
| 📊 **Library reports** | `library_stats` · `find_duplicate_recipes` · `check_recipe_links` |
| 🔧 **Other** | `parse_ingredients` · `manage_taxonomy` |

With `MEALIE_READ_ONLY=true`, twelve read tools remain.

Once connected, ask in plain language — the agent picks the tools:

> 💬 *What's for dinner this week?*
>
> 💬 *Import https://example.com/that-curry-recipe and tag it "Weeknight".*
>
> 💬 *Plan a random week of dinners, no repeats from last week.*
>
> 💬 *I have "scallion" and "spring onion" as separate foods — merge them.*

### ✨ Things it does for you

- **Ingredients as plain text.** `create_recipe` and `update_recipe` accept
  `["2 cups flour", "pinch of salt"]` and run them through Mealie's parser.
  Structured objects work too; the two can be mixed. That also makes
  `update_recipe` the repair for an import that came back empty, which the
  import tells you about rather than leaving to be discovered later.
- **Tags by name.** Mealie's API needs tag objects with a name and a slug.
  Pass `["Vegan"]` and the server resolves or creates it, then tells you which
  ones were new.
- **Updates don't wipe tags.** Mealie's PATCH replaces list fields wholesale.
  `update_recipe` merges tags, categories, and tools by default; pass
  `replace_tags` to overwrite. Renaming a recipe changes its slug — Mealie
  derives one from the other — so the result hands back the new one.
- **Taxonomy cleanup without a script.** `manage_taxonomy` lists (paged, with
  the total), creates, renames, updates, and deletes foods, units, labels,
  tags, categories, and tools — and merges duplicate foods or units through
  Mealie's own merge endpoints, so every recipe that used the loser is
  repointed.
- **A random week is one call.** Mealie's random endpoint fills one day per
  request. `random_meal_plan` loops for you, capped at 14 days.
- **Usage rollups in one call.** Mealie has no "how many recipes use this
  food" endpoint. `library_stats("foods")` sweeps the library server-side and
  returns the most-used foods with their counts plus every unused one — the
  answer to "is this safe to delete" without a search per name.
- **Retag a whole shelf at once.** `bulk_tag_recipes(slugs, tags=["Weeknight"])`
  files any number of recipes through Mealie's bulk endpoints in one call,
  creating names that do not exist yet. It only adds; removing still goes
  through `update_recipe` with `replace_tags`.
- **Batch taxonomy writes.** `manage_taxonomy(action="update", items=[...])`
  runs twenty-five renames in one call, and reports per-item failures instead
  of stopping at the first bad id.
- **Cookbook filters without the syntax.** `create_cookbook(tags=["Vegan"])`
  builds the `queryFilterString` for you, matching your names to Mealie's
  stored casing. `update_cookbook` re-filters in place, so the id survives.
- **Sweeps without a call per recipe.** `search_recipes(fields=["slug",
  "tags"])` projects results the way `get_recipe` does, so surveying the
  library is one paged search rather than N follow-up reads.

### 🛡️ Safety

- `MEALIE_READ_ONLY=true` prevents write tools from being registered at all.
- `delete_recipe` requires the slug twice: `delete_recipe(slug, confirm_slug)`.
- Write requests are never retried — Mealie has no idempotency key, and a
  retried create would duplicate the recipe.

## 🎓 Agent skill

The workflows the tool list alone doesn't teach — planning a week without
repeats, filing an imported recipe, writing cookbook filters, cleaning up a
library rollup-first — live in [mealie-skill][skill], which detects this
server and drives it. It builds for Claude Code, Antigravity, Cursor, and
`AGENTS.md`.

This repository no longer ships its own copy: two skills for one server meant
two descriptions in every prompt and two places for the same guidance to drift.

[skill]: https://github.com/mgummich/mealie-skill

## 🛠️ Development

```bash
uv sync --extra dev             # creates .venv from the committed uv.lock
uv run --extra dev pre-commit install   # run the lint gates on every commit
uv run --extra dev pytest       # unit tests, fully offline
uv run --extra dev ruff check .
uv run --extra dev ruff format .
uv run --extra dev mypy         # type-checks src/
```

`pre-commit run --all-files` runs the same gates CI does.

Unit tests run entirely offline: `shape.py` against captured fixtures,
`client.py` against mocked HTTP.

```bash
./scripts/integration.sh     # needs Docker: throwaway Mealie on port 19925
```

The integration suite spins up a real Mealie in Docker, runs
`tests/integration/` against it, and tears everything down.
`scripts/smoke.py` hits a live instance of your choosing on demand.

## 🔗 Related

[Knuckles-Team/mealie-mcp](https://github.com/Knuckles-Team/mealie-mcp) takes
the opposite approach — it generates 247 tools from Mealie's OpenAPI spec, one
per endpoint. Use it if you want complete API coverage. Use this one if you
want a small tool list and short responses.

## 📄 License

MIT

<!-- MCP registry ownership marker -->

mcp-name: io.github.mgummich/mcp-mealie
