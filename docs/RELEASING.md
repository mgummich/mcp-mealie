# Releasing

A release is a tag. The `Release` workflow does the rest: it re-runs the
gates, builds the wheel and sdist, cuts a GitHub Release, and attaches both.
Installs read the tag directly, so the tag *is* the distribution — there is no
package index in the loop.

Version lives in one place: `src/mealie_mcp/__init__.py` (`__version__`).
`pyproject.toml` reads it via hatch. Two copies are written by hand and pinned
by tests, so a half-bumped release fails the gates instead of shipping: the
install tag in `README.md` and `docs/HOWTO.md`.

1. Bump `__version__` and the `mcp-mealie@vX.Y.Z` pin in `README.md` and
   `docs/HOWTO.md`.
2. Add a `## [X.Y.Z] - YYYY-MM-DD` section to `CHANGELOG.md`, and a link for
   it at the bottom. The workflow reads this section as the release notes and
   fails if it is missing.
3. Run the gates: `uv run --extra dev pre-commit run --all-files`,
   `uv run --extra dev pytest`, and `./scripts/integration.sh` (needs Docker).
4. Commit, then tag and push:

   ```bash
   git tag v0.X.Y
   git push origin main v0.X.Y
   ```

That is the whole release. Watch it with `gh run watch`, and if it fails,
delete the tag (`git push --delete origin v0.X.Y`), fix, and push it again —
nothing is published anywhere until the workflow's last step.

## Not published to a package index

`uvx --from git+https://github.com/mgummich/mcp-mealie@vX.Y.Z mcp-mealie`
needs only the tag, which is why the install docs use it.

Putting this on PyPI would mean creating a pending publisher on pypi.org
(project `mcp-mealie`, owner `mgummich`, repository `mcp-mealie`, workflow
`release.yml`, environment `pypi`) and restoring the `pypa/gh-action-pypi-publish`
step. It was removed because the account does not exist; v0.2.0's publish
attempt failed with `invalid-publisher`, uploading nothing.

The MCP registry waits on the same thing, and its manifest is gone with it:
`server.json` declared a `pypi` package that was never published, so the
registry had nothing to verify ownership of. Publishing there again means
restoring the manifest (`git log -- server.json`) alongside the package it
points at, plus the `mcp-name:` marker the README carried.
