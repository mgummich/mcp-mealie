# Repository audit — 2026-09-12

A structure and documentation pass: what every file is for, where the same fact
was written twice, and what was done about it. No runtime behavior was changed;
`src/` is untouched.

The code itself was audited separately on 2026-09-11
([`archive/2026-09-11-audit.md`](archive/2026-09-11-audit.md)); those findings
were fixed in #30 and #31 and are not revisited here.

## Verdicts

| Path | Verdict | Note |
| --- | --- | --- |
| `README.md` | KEEP | Product, install, usage. Development section cut to two commands plus links; the spec link now points at `docs/ARCHITECTURE.md`. |
| `AGENTS.md` | **ADDED** | Was missing. The AI operational context: repo map, gates, invariants, definition of done. |
| `CONTRIBUTING.md` | **ADDED** | Was missing, and `_config.yml` already excluded it from the site — a file that had been intended and never written. |
| `docs/ARCHITECTURE.md` | **ADDED** | Technical truth and the source-of-truth map. Supersedes the 2026-08-10 spec. |
| `docs/REPO_AUDIT.md` | **ADDED** | This file. |
| `CHANGELOG.md` | KEEP | History. Release notes are read from it by `release.yml`. |
| `SECURITY.md` | KEEP | Filled in since the 2026-09-11 audit flagged it as a template. |
| `LICENSE`, `pyproject.toml`, `uv.lock` | KEEP | |
| `.env.example` | KEEP, fixed | Was missing `MEALIE_MAX_CONCURRENCY`. Now checked against `config.py`. |
| `.gitignore` | KEEP, fixed | `*.egg-info/` and `.ruff_cache/` were each listed twice. |
| `_config.yml`, `_layouts/default.html` | KEEP | GitHub Pages. Excludes extended to the new developer-facing docs. |
| `index.md`, `howto.md`, `releases.md` | KEEP | Three-to-nine-line Jekyll wrappers that give `README.md`, `docs/HOWTO.md`, and `CHANGELOG.md` their permalinks via `include_relative`. Root clutter, but load-bearing: without them the site has no `/`, `/howto`, or `/changelog`. Deleting them breaks the published site. |
| `docs/HOWTO.md` | KEEP | End-user tutorial. |
| `docs/RELEASING.md` | KEEP | Release procedure, including why there is no PyPI package. |
| `docs/superpowers/specs/*.md` | **MOVED → ARCHIVE** | Now `docs/archive/2026-08-09-design.md` and `2026-08-10-current-state.md`. The `superpowers/specs/` nesting named the tool that produced them, not what they are. Both are explicitly marked non-authoritative; the "Current State" status line said *Current* while describing v0.2.1. |
| `docs/audits/2026-09-11-project-audit.md` | **MOVED → ARCHIVE** | Now `docs/archive/2026-09-11-audit.md`, with a banner recording that findings 1-6 are fixed. It was untracked; a security audit is worth committing, but not as a standing defect list. |
| `docs/archive/README.md` | **ADDED** | Says plainly that nothing in the directory is authoritative. |
| `tests/test_version.py` | **MERGED** | Into `tests/test_docs.py`, so every drift check is in one place. |
| `tests/test_docs.py` | **ADDED** | The anti-drift checks below. |
| `scripts/integration.sh`, `scripts/smoke.py` | KEEP | Both are referenced and both work. |
| `.github/workflows/*.yml` | KEEP | `codeql.yml` still carries GitHub's template comment block; noisy, but it is the upstream file and diverging from it costs more than it saves. |
| `.claude/settings.local.json` | KEEP | Gitignored, local only. |

Nothing was deleted. Two directories (`docs/superpowers/`, `docs/audits/`)
disappeared as a consequence of their contents moving.

## Duplicated facts, and where they now live

| Fact | Was in | Now |
| --- | --- | --- |
| Tool list and counts | README prose only, hand-maintained | Still README, now checked against the live registry |
| Tool inventory with endpoints | README + the 2026-08-10 spec | `docs/ARCHITECTURE.md` for structure; the spec is archived |
| Developer commands | README | `CONTRIBUTING.md`; README keeps two lines and a link |
| Config variables | README table + `.env.example` | Still both, now checked against `Config` |
| Supported Mealie versions | README, `ci.yml` comment | `ci.yml` matrix is the source; prose is checked against it |
| Version | `__init__.py`, README pin, HOWTO pin | Unchanged; the existing pin test moved into `test_docs.py` |

## Drift found and fixed

1. `.env.example` did not list `MEALIE_MAX_CONCURRENCY`, which the README's
   config table did.
2. `docs/archive/2026-08-10-current-state.md` was labelled **Status: Current**
   while describing v0.2.1 and a tool set that has grown since.
3. README pointed at that spec as the description of the server as built.
4. `_config.yml` excluded a `CONTRIBUTING.md` that did not exist.
5. `.gitignore` listed two patterns twice.
6. The 2026-09-11 audit was an uncommitted file whose findings were all fixed,
   with nothing recording that they were.
7. `docs/HOWTO.md` said **twelve** read tools remain in read-only mode; the
   number is seventeen, and the README already said so. Found by the critic
   pass, not by a check — `test_docs.py` now covers HOWTO's count too.
8. `docs/HOWTO.md` and `README.md` disagreed on where Mealie keeps API tokens
   (**Profile** vs **Settings**); both now say Profile, which is where the
   token page lives.
9. `docs/RELEASING.md` said the release workflow "re-runs the gates" while
   `release.yml` ran only `ruff check` and `pytest`. The workflow now runs the
   whole fast gate, which is what the sentence promised.
10. The README's "86% of Mealie's API" was unverifiable and inconsistent with
    the README's own figure of 247 generated tools for the competing project.
    Softened rather than guessed at.
11. `AGENTS.md` and `docs/ARCHITECTURE.md` first stated "every response goes
    through a `shape.py` shaper" and "destructive tools take the identifier
    twice" more broadly than the code does — `library.py` rollups build their
    own projections, and only whole-object deletes ask twice. Both scoped to
    what is true.

Checked and found **correct**, so left alone: the README's "Forty-three curated
tools" and "seventeen read tools"; the compatibility note against the CI matrix
(2.8.0 and 3.25.1); the `@v0.3.1` install pins; the Python version badge against
the CI matrix; every registered tool appearing in the README table. No
`TODO`/`FIXME`/`HACK` markers exist in the repository.

## Checks added

`tests/test_docs.py`, all offline, none asserting on prose formatting:

- install pins in `README.md` and `docs/HOWTO.md` equal `__version__` (moved
  from `test_version.py`);
- the README tool table equals the set of registered tool names;
- the spelled-out tool counts in `README.md` and `docs/ARCHITECTURE.md`, and
  the numeral in `AGENTS.md`, equal the registered count and the read-only
  count;
- `MEALIE_*` names in `README.md`, `docs/ARCHITECTURE.md`, and `.env.example`
  equal the names `config.py` reads;
- the defaults in the README's config table equal the `Config` defaults;
- the Mealie versions claimed in `README.md` and `docs/ARCHITECTURE.md` equal
  the `ci.yml` integration matrix;
- the read-tool count in `README.md` and `docs/HOWTO.md` equals the count
  registered under `MEALIE_READ_ONLY=true`;
- every relative link in every tracked Markdown file resolves.

## Known limitations

- The Pages site renders `README.md` through `index.md`, so README's relative
  links to `CONTRIBUTING.md` and `docs/ARCHITECTURE.md` do not resolve there.
  They resolve on GitHub and in a clone, which is where developers read them;
  the alternative is publishing developer docs to a user-facing site.
- The tool-table check matches backticked names in table rows. It would not
  notice a tool documented in the right table but the wrong category.
- `uv.lock` pins the development toolchain; the runtime lower bounds in
  `pyproject.toml` (`fastmcp>=2.10`) are not exercised by any job.
