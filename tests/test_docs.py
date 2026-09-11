"""Drift checks: facts written in prose against the code that decides them.

Everything here reads the repository's own files. Nothing formats-checks prose —
these assert on facts (versions, tool names, counts, variable names, links) that
have exactly one source of truth elsewhere, and fail when the copy stops
matching it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastmcp import Client

from mealie_mcp import __version__
from mealie_mcp.config import Config
from mealie_mcp.server import build_server

ROOT = Path(__file__).parents[1]
README = (ROOT / "README.md").read_text()
ARCHITECTURE = (ROOT / "docs/ARCHITECTURE.md").read_text()
CI = (ROOT / ".github/workflows/ci.yml").read_text()


def read(name: str) -> str:
    return (ROOT / name).read_text()


# --------------------------------------------------------------------------
# Version


#: Docs that pin the install to a tag. Installs read
#: `uvx --from git+...@vX.Y.Z`, so a stale pin silently installs an old server
#: rather than failing.
PINNED_DOCS = ("README.md", "docs/HOWTO.md")

#: The pin as the docs write it, e.g. "mcp-mealie@v0.2.0".
PIN = re.compile(r"mcp-mealie@v(\d+\.\d+\.\d+)")


@pytest.mark.parametrize("name", PINNED_DOCS)
def test_docs_pin_the_current_version(name):
    pins = PIN.findall(read(name))

    assert pins, f"{name} pins no version — did the install command change?"
    assert set(pins) == {__version__}, f"{name} pins {sorted(set(pins))}, not {__version__}"


# --------------------------------------------------------------------------
# Tools


async def registered(read_only: bool) -> set[str]:
    server = build_server(Config(url="https://mealie.test", token="tok", read_only=read_only))
    async with Client(server) as client:
        return {tool.name for tool in await client.list_tools()}


def table_rows(text: str) -> list[list[str]]:
    """Cells of every Markdown table row, separators and headers excluded."""
    rows = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if all(set(cell) <= set("-: ") for cell in cells):  # the --- separator
            continue
        rows.append(cells)
    return rows


def readme_tool_table() -> set[str]:
    """Tool names from the README's tool table, whatever its categories are."""
    rows = [cells for cells in table_rows(README) if len(cells) == 2 and "**" in cells[0]]
    assert rows, "README has no tool table rows — did the table format change?"
    return {name for _, tools in rows for name in re.findall(r"`(\w+)`", tools)}


async def test_readme_lists_every_registered_tool():
    assert readme_tool_table() == await registered(read_only=False)


#: Counts in the README are written as words, which is the house style; this is
#: the dictionary that makes them checkable.
ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
TEENS = [
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
TENS = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def spell(n: int) -> str:
    """Spell 0-99 the way the README does, e.g. 43 -> 'forty-three'."""
    if n < 10:
        return ONES[n]
    if n < 20:
        return TEENS[n - 10]
    tens, ones = divmod(n, 10)
    word = TENS[tens - 2]
    return f"{word}-{ONES[ones]}" if ones else word


async def test_readme_counts_the_tools_it_registers():
    total = len(await registered(read_only=False))

    assert re.search(rf"\b{spell(total)} curated tools\b", README, re.IGNORECASE), (
        f"README should say '{spell(total)} curated tools'"
    )


@pytest.mark.parametrize("name", ["README.md", "docs/HOWTO.md"])
async def test_docs_count_the_tools_read_only_mode_leaves(name):
    reads = len(await registered(read_only=True))

    assert re.search(rf"\b{spell(reads)} read tools\b", read(name), re.IGNORECASE), (
        f"{name} should say '{spell(reads)} read tools' remain in read-only mode"
    )


async def test_architecture_counts_the_tools_it_registers():
    total = len(await registered(read_only=False))

    assert re.search(rf"\b{spell(total).capitalize()} curated tools\b", ARCHITECTURE), (
        f"docs/ARCHITECTURE.md should say '{spell(total)} curated tools'"
    )


async def test_agents_md_counts_the_tools_it_registers():
    """AGENTS.md is read by machines, so it writes the count as a numeral."""
    total = len(await registered(read_only=False))

    assert re.search(rf"\b{total} curated tools\b", read("AGENTS.md")), (
        f"AGENTS.md should say '{total} curated tools'"
    )


# --------------------------------------------------------------------------
# Configuration


def config_variables() -> set[str]:
    """The MEALIE_* names config.py actually reads.

    Quoted only: a name in a comment or a docstring is prose, not a lookup.
    """
    return set(re.findall(r'"(MEALIE_[A-Z_]+)"', read("src/mealie_mcp/config.py")))


#: MEALIE_* names the server never reads: they belong to the test harness, and
#: the docs mention them when describing it.
HARNESS_VARIABLES = {"MEALIE_TEST_VERSION", "MEALIE_INTEGRATION"}


@pytest.mark.parametrize("name", ["README.md", "docs/ARCHITECTURE.md", ".env.example"])
def test_documented_config_variables_match_the_code(name):
    documented = set(re.findall(r"MEALIE_[A-Z_]+", read(name))) - HARNESS_VARIABLES

    assert documented == config_variables()


def test_documented_defaults_match_the_code():
    defaults = Config(url="https://mealie.test", token="tok")

    # | Variable | Required | Default | Purpose |
    documented = {
        cells[0].strip("`"): cells[2]
        for cells in table_rows(README)
        if len(cells) == 4 and cells[0].startswith("`MEALIE_")
    }
    for variable, value in (
        ("MEALIE_READ_ONLY", str(defaults.read_only).lower()),
        ("MEALIE_VERIFY_SSL", str(defaults.verify_ssl).lower()),
        ("MEALIE_LOG_LEVEL", defaults.log_level),
        ("MEALIE_MAX_CONCURRENCY", str(defaults.max_concurrency)),
    ):
        assert documented[variable] == f"`{value}`", (
            f"README's Default column says {documented[variable]} for {variable}, "
            f"but the default is {value!r}"
        )


# --------------------------------------------------------------------------
# Compatibility


def ci_mealie_versions() -> set[str]:
    matrix = re.search(r"mealie: \[(.*?)\]", CI)
    assert matrix, "ci.yml has no Mealie version matrix"
    return {v.strip(' "').lstrip("v") for v in matrix.group(1).split(",")}


#: The sentence every doc uses to make the claim. Anchoring on it, rather than
#: on markup, keeps the check from firing on a harmless prose edit and from
#: passing because some unrelated x.y.z appears elsewhere in the file.
CLAIM = "CI runs the integration suite against"


@pytest.mark.parametrize("name", ["README.md", "docs/ARCHITECTURE.md", "AGENTS.md"])
def test_compatibility_claims_match_the_ci_matrix(name):
    """The claim rests on what CI actually runs, so it must name those versions."""
    # Blockquote markers would otherwise land in the middle of the sentence.
    text = " ".join(re.sub(r"^> ?", "", read(name), flags=re.MULTILINE).split())
    start = text.find(CLAIM)
    assert start != -1, f"{name} should say '{CLAIM} <versions>'"
    claim = text[start : start + len(CLAIM) + 140]

    assert set(re.findall(r"\d+\.\d+\.\d+", claim)) == ci_mealie_versions()


# --------------------------------------------------------------------------
# Links

#: Inline `[text](path)` and reference-style `[label]: path`, minus URLs and
#: anchors-only links.
LINK = re.compile(r"\]\(([^)\s]+)\)|^\[[^\]]+\]:\s*(\S+)", re.MULTILINE)


def markdown_files() -> list[Path]:
    return [*ROOT.glob("*.md"), *ROOT.glob("docs/**/*.md")]


@pytest.mark.parametrize("path", markdown_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_internal_links_resolve(path):
    text = path.read_text()
    broken = []
    for inline, reference in LINK.findall(text):
        target = (inline or reference).split("#")[0]
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        if not (path.parent / target).exists():
            broken.append(target)

    assert not broken, f"{path.relative_to(ROOT)} links to missing {broken}"
