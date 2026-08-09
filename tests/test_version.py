"""server.json duplicates the version by hand; this keeps it from drifting."""

import json
from pathlib import Path

from mealie_mcp import __version__


def test_server_json_versions_match():
    data = json.loads((Path(__file__).parents[1] / "server.json").read_text())
    assert data["version"] == __version__
    assert data["packages"][0]["version"] == __version__
