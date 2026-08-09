"""Shared test helpers."""

from __future__ import annotations

import json
from typing import Any


def data(result: Any) -> Any:
    """Decode a tool result.

    The server sends results as JSON text only — the duplicate structured copy
    is stripped — so fastmcp's `.data` shortcut has nothing to parse.
    """
    return json.loads(result.content[0].text)
