# Howto: from a fresh Mealie to an agent that maintains it

The README says what the tools are. This is the order to do things in, and the
handful of behaviors that surprise people on the first run.

Twenty minutes end to end. Requires Mealie 2.0 or newer.

## 1. Token and backup

In Mealie: **Profile → API Tokens** for a long-lived token, then **Site
Settings → Backups** before anything writes for the first time. Nothing here
is exotic, but merges and deletes are not undoable and a backup is cheaper
than reconstructing a taxonomy.

```bash
export MEALIE_URL=https://mealie.example.org
export MEALIE_API_TOKEN=<your token>
```

Verify the instance answers before wiring up a client:

```bash
curl -s -H "Authorization: Bearer $MEALIE_API_TOKEN" \
  "$MEALIE_URL/api/users/self" -w '\n%{http_code}\n' | tail -2
curl -s "$MEALIE_URL/api/app/about"
```

Your username and `200` is what you want; `401` means the token. The second
call reports the version — the server reads it at startup and refuses to run
against 1.x, which has no `/api/households` endpoints.

## 2. Connect the server, read-only first

```bash
claude mcp add mealie \
  --env MEALIE_URL=$MEALIE_URL \
  --env MEALIE_API_TOKEN=$MEALIE_API_TOKEN \
  --env MEALIE_READ_ONLY=true \
  -- uvx mcp-mealie
```

For Claude Desktop, Cursor, Windsurf, or Zed, the same three variables go into
the `mcpServers` block from the README.

With `MEALIE_READ_ONLY=true` the write tools are never registered — the model
cannot call them by accident, because it cannot see them. Twelve read tools
remain, which is enough for every question in step 3. Drop the variable once
you like what the assistant proposes.

Self-signed certificate on a homelab instance: add
`MEALIE_VERIFY_SSL=false`. Do not use it against anything reachable from the
internet.

## 3. Look before writing

Ask in plain language; the model picks the tools.

> *What's for dinner this week?*
>
> *Which of my tags are used by nothing?*
>
> *Did I import the same recipe twice?*
>
> *Which recipes link to a page that no longer exists?*

The last three are one call each — `library_stats`, `find_duplicate_recipes`,
`check_recipe_links`. They sweep server-side. If the assistant instead starts
running one search per tag, stop it: that is the pass those tools replace.

`library_stats("foods")` and `library_stats("units")` are the slow ones. They
need every recipe's ingredients, so that sweep is one request per recipe and
honors `max_recipes`. Tags, categories and tools come off the recipe list in a
handful of requests.

## 4. Turn on writes

Remove `MEALIE_READ_ONLY` and restart the client. Useful first jobs, roughly
in order of how much they repay the effort:

**Import and file a recipe.** `import_recipe_from_url` scrapes it;
`update_recipe` files it. Tags, categories and tools **merge** with what is
already there and names that do not exist yet are created — the response says
which were new, so read that line back before a typo becomes a permanent tag.
Pass `replace_tags=True` to overwrite instead. If the scraper missed the
photo, `set_recipe_image(slug, url)`.

**Merge duplicate foods.** `manage_taxonomy("foods", "merge", item_id=<loser>,
merge_into=<keeper>)` uses Mealie's own merge endpoint and repoints every
recipe that used the loser. Deleting the duplicate instead strips it from
those recipes — that is the difference worth confirming out loud before
running it.

**Rename in bulk.** Every action except `list` also takes `items=[…]` and runs
the batch in one call, reporting per-item failures rather than stopping at the
first bad id:

```python
manage_taxonomy("foods", "update", items=[
  {"item_id": "...", "name": "Scallion"},
  {"item_id": "...", "data": {"labelId": "..."}},
])
```

**Build a cookbook.** A Mealie cookbook is a saved filter, not a folder — it
fills itself as recipes match. Pass names and let the server write the filter:
`create_cookbook(name="Weeknight Dinners", tags=["Quick"], require_all=True)`.
Check it with `get_cookbook_recipes` afterwards; an overly narrow filter
matches nothing, which is easy to miss. To change one, `update_cookbook` —
never delete and recreate, which throws away the id.

**Plan a week.** Read the *previous* week with `get_meal_plan` first; that is
what tells you which dinners would repeat. Then one
`add_meal_plan_entry(date, entry_type, recipe_slug)` per slot — there is no
batch endpoint — or `random_meal_plan` for a whole range at once. Random
*adds to* existing entries rather than replacing them, and is capped at 14
days per call.

## 5. Things that bite

- **Ingredients, instructions and notes replace on update.** Read the recipe
  first and pass the whole list back, not just the new items. Tags,
  categories and tools are the exception — those merge.
- **`manage_taxonomy("…", "list")` is paged at 200** and reports the total. A
  library with 400 foods needs two calls; conclude nothing from page one.
  For anything table-wide, `library_stats` is cheaper and carries the counts.
- **`update` is a patch.** Fields you do not mention keep their value.
- **`delete_recipe` needs the slug twice** and is permanent.
- **Write requests are never retried.** Mealie has no idempotency key, and a
  retried create would duplicate the recipe. A failed write is reported, not
  re-attempted.
- **An auth error on every tool is configuration**, not a transient failure.
  Same for "server is in read-only mode".

## 6. Add the workflows

The tools are verbs. Deciding *when* to merge, how large a batch stays
reviewable, and what a cookbook rule should contain is the job of
[mealie-skill](https://github.com/mgummich/mealie-skill), which builds for
Claude Code, Antigravity, Cursor and `AGENTS.md`.

It detects this server and uses it as the primary analysis path: with the
server connected it never builds its own local recipe index, because
`library_stats` and friends answer the same questions in one call. It keeps
its own ordered batch (`actions.json` + `apply`) for plans where execution
order matters or a dry run over the whole set is wanted.

One rule when running both: **one write path per plan**. Either every write is
an MCP call or the whole plan goes through `apply` — never half of each.
