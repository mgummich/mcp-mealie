# Releasing

Version lives in one place: `src/mealie_mcp/__init__.py` (`__version__`).
`pyproject.toml` reads it via hatch. `server.json` duplicates it for the MCP
registry and must be bumped by hand.

1. Bump `__version__` and the two `version` fields in `server.json`.
2. Add a section to `CHANGELOG.md`.
3. Run the gates: `uv run pytest` and `./scripts/integration.sh` (needs Docker).
4. Commit, then tag and push:

   ```bash
   git tag v0.X.Y
   git push origin main v0.X.Y
   ```

   The `Release` workflow builds and publishes to PyPI via trusted publishing
   (configured on pypi.org under the project's Publishing settings: repo
   `mgummich/mcp-mealie`, workflow `release.yml`, environment `pypi`).

5. Publish the version to the MCP registry:

   ```bash
   mcp-publisher login github
   mcp-publisher publish
   ```
