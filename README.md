# mcp-mealie

An MCP server for [Mealie](https://mealie.io), built for agents rather than for
API coverage. Eighteen curated tools over recipes, meal plans, and cookbooks,
with responses trimmed hard enough that a recipe costs a few hundred tokens
instead of a few thousand.

Works with any MCP client that speaks stdio: Claude Code, Claude Desktop,
Cursor, Windsurf, Zed.

## Install

No clone or virtualenv needed — `uvx` fetches it on demand.

```json
{
  "command": "uvx",
  "args": ["mcp-mealie"],
  "env": {
    "MEALIE_URL": "https://mealie.example.com",
    "MEALIE_API_TOKEN": "your-token"
  }
}
```

Create the token in Mealie under **Settings → API Tokens**.

### Claude Code

```bash
claude mcp add mealie \
  --env MEALIE_URL=https://mealie.example.com \
  --env MEALIE_API_TOKEN=your-token \
  -- uvx mcp-mealie
```

### Claude Desktop, Cursor, Windsurf, Zed

Add the JSON block above under `mcpServers` in the client's config file.

## Configuration

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `MEALIE_URL` | yes | — | Base URL of your Mealie instance |
| `MEALIE_API_TOKEN` | yes | — | Long-lived API token |
| `MEALIE_READ_ONLY` | no | `false` | Hide every write tool |
| `MEALIE_VERIFY_SSL` | no | `true` | Set false for self-signed certs (homelab only) |
| `MEALIE_LOG_LEVEL` | no | `INFO` | Log verbosity, to stderr |

Booleans accept `1/true/yes/on` and their negations. An unrecognized value is a
startup error rather than a silent false.

Requires Mealie 2.0 or newer. The server checks at startup and refuses to run
against 1.x, which has no `/api/households` endpoints.

## Tools

**Recipes** — `search_recipes`, `get_recipe`, `suggest_recipes`,
`create_recipe`, `update_recipe`, `delete_recipe`, `import_recipe_from_url`

**Meal plans** — `get_meal_plan`, `get_todays_meals`, `add_meal_plan_entry`,
`delete_meal_plan_entry`, `random_meal_plan`

**Cookbooks** — `list_cookbooks`, `get_cookbook_recipes`, `create_cookbook`,
`delete_cookbook`

**Other** — `parse_ingredients`, `manage_taxonomy`

With `MEALIE_READ_ONLY=true`, nine read tools remain.

### Things it does for you

- **Ingredients as plain text.** `create_recipe` accepts `["2 cups flour",
  "pinch of salt"]` and runs them through Mealie's parser. Structured objects
  work too; the two can be mixed.
- **Tags by name.** Mealie's API needs tag objects with a name and a slug.
  Pass `["Vegan"]` and the server resolves or creates it, then tells you which
  ones were new.
- **Updates don't wipe tags.** Mealie's PATCH replaces list fields wholesale.
  `update_recipe` merges tags and categories by default; pass `replace_tags`
  to overwrite.
- **A random week is one call.** Mealie's random endpoint fills one day per
  request. `random_meal_plan` loops for you, capped at 14 days.

### Safety

- `MEALIE_READ_ONLY=true` prevents write tools from being registered at all.
- `delete_recipe` requires the slug twice: `delete_recipe(slug, confirm_slug)`.
- Write requests are never retried — Mealie has no idempotency key, and a
  retried create would duplicate the recipe.

## Bundled skill

`skills/mealie/SKILL.md` covers the workflows the tool list alone doesn't
teach: planning a week without repeats, filing an imported recipe, and writing
cookbook filters. Copy it into your agent's skills directory, or read it as
documentation.

## Development

```bash
uv venv --python 3.11
uv pip install -e ".[dev]"
uv run pytest
```

Tests run entirely offline: `shape.py` against captured fixtures, `client.py`
against mocked HTTP. `scripts/smoke.py` hits a live instance on demand.

## Related

[Knuckles-Team/mealie-mcp](https://github.com/Knuckles-Team/mealie-mcp) takes
the opposite approach — it generates 247 tools from Mealie's OpenAPI spec, one
per endpoint. Use it if you want complete API coverage. Use this one if you
want a small tool list and short responses.

## License

MIT
