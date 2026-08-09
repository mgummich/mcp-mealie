"""The version is written out by hand in the docs; keep them in step."""

import re
from pathlib import Path

import pytest

from mealie_mcp import __version__

ROOT = Path(__file__).parents[1]

#: Docs that pin the install to a tag. Installs read
#: `uvx --from git+...@vX.Y.Z`, so a stale pin silently installs an old server
#: rather than failing.
PINNED_DOCS = ("README.md", "docs/HOWTO.md")

#: The pin as the docs write it, e.g. "mcp-mealie@v0.2.0".
PIN = re.compile(r"mcp-mealie@v(\d+\.\d+\.\d+)")


@pytest.mark.parametrize("name", PINNED_DOCS)
def test_docs_pin_the_current_version(name):
    pins = PIN.findall((ROOT / name).read_text())

    assert pins, f"{name} pins no version — did the install command change?"
    assert set(pins) == {__version__}, f"{name} pins {sorted(set(pins))}, not {__version__}"
