"""Shared test helpers."""

from __future__ import annotations

import json
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def outside_the_repo(monkeypatch, tmp_path):
    """Run every test from an empty directory.

    Config.from_env loads the nearest .env by walking up from the working
    directory, so anyone with real credentials in the repo root saw the
    "missing variable" tests fail — the file supplied what the test had just
    deleted from the environment. Fixtures are addressed via __file__, so
    nothing here depends on the working directory.
    """
    monkeypatch.chdir(tmp_path)


def data(result: Any) -> Any:
    """Decode a tool result.

    The server sends results as JSON text only — the duplicate structured copy
    is stripped — so fastmcp's `.data` shortcut has nothing to parse.
    """
    return json.loads(result.content[0].text)
