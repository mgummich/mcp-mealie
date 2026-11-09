"""MkDocs hooks: publish the root-level docs and keep repo-relative links working.

README, CHANGELOG, CONTRIBUTING and SECURITY stay at the repository root, where
GitHub renders them; this adds them to the site without copying them. Their
links are written for GitHub (`docs/HOWTO.md`, `../CONTRIBUTING.md`), so each
link is resolved against the file's repo path and pointed at the site page, or
at GitHub for files that are not pages (`pyproject.toml`, `LICENSE`).
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import Any

from mkdocs.structure.files import File, Files

ROOT = Path(__file__).parents[1]
REPO_BLOB = "https://github.com/mgummich/mcp-mealie/blob/main/"

#: Root file -> page it becomes.
ROOT_PAGES = {
    "README.md": "index.md",
    "CHANGELOG.md": "changelog.md",
    "CONTRIBUTING.md": "contributing.md",
    "SECURITY.md": "security.md",
}
SOURCES = {page: repo for repo, page in ROOT_PAGES.items()}

#: Inline `](path#anchor)` and reference-style `[label]: path#anchor`; group 2
#: is the path, groups 1 and 3 are kept as they are.
LINK = re.compile(r"(\]\(|^\[[^\]]+\]:\s*)([^)\s#:]+)(?=[)\s#]|$)(#[^)\s]*)?", re.MULTILINE)


def on_files(files: Files, config: Any) -> Files:
    for repo_path, page in ROOT_PAGES.items():
        files.append(File.generated(config, page, abs_src_path=str(ROOT / repo_path)))
    # Lowercase URLs, so docs/HOWTO.md keeps the /howto/ address it had.
    for file in files.documentation_pages():
        file.dest_uri = file.dest_uri.lower()
    return files


def on_page_markdown(markdown: str, page: Any, config: Any, files: Files) -> str:
    src = page.file.src_uri
    repo_dir = posixpath.dirname(SOURCES.get(src, f"docs/{src}"))

    def relink(match: re.Match[str]) -> str:
        lead, target, anchor = match.group(1), match.group(2), match.group(3) or ""
        repo = posixpath.normpath(posixpath.join(repo_dir, target))
        site = ROOT_PAGES.get(repo) or repo.removeprefix("docs/")
        if (repo.startswith("docs/") or repo in ROOT_PAGES) and files.get_file_from_path(site):
            # Every page sits at the top level, so its path is the link.
            return f"{lead}{site}{anchor}"
        return f"{lead}{REPO_BLOB}{repo}{anchor}"

    return LINK.sub(relink, markdown)
