# Releasing

Version lives in one place: `src/mealie_mcp/__init__.py` (`__version__`).
`pyproject.toml` reads it via hatch. `server.json` duplicates it for the MCP
registry and must be bumped by hand.

1. Bump `__version__` and the two `version` fields in `server.json`.
2. Add a section to `CHANGELOG.md`.
3. Run the gates: `uv run --extra dev pre-commit run --all-files`,
   `uv run --extra dev pytest`, and `./scripts/integration.sh` (needs Docker).
4. Commit, then tag and push:

   ```bash
   git tag v0.X.Y
   git push origin main v0.X.Y
   ```

   The `Release` workflow builds and publishes to PyPI via trusted publishing.

   > **PyPI is not configured yet.** The `publish` job fails on v0.2.0 with
   > `invalid-publisher`: PyPI has no publisher record matching the claims
   > GitHub sends. Nothing is uploaded when that happens, so the version
   > number stays free — fix the configuration and re-run the failed job
   > rather than burning a patch release.
   >
   > To configure it, add a *pending* publisher on pypi.org (Your account →
   > Publishing) — pending, because the project has never been published:
   > project `mcp-mealie`, owner `mgummich`, repository `mcp-mealie`,
   > workflow `release.yml`, environment `pypi`. All five must match, and
   > the environment is the one usually left blank.
   >
   > Until then, installs come from git: the README's `uvx --from
   > git+https://github.com/mgummich/mcp-mealie@vX.Y.Z` form needs only the
   > tag, which step 4 already pushed. Bump the tag in the README and
   > `docs/HOWTO.md` as part of the release.

5. Publish the version to the MCP registry:

   ```bash
   mcp-publisher login github
   mcp-publisher publish
   ```
