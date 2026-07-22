"""One-shot cleanup for public docs, metadata, and release surfaces.

This script is executed and removed by the cleanup PR workflow. It intentionally
centralizes the large text rewrite so the final squash contains only maintained
product files, not migration machinery.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.11.0"
DATE = "2026-07-22"


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content.rstrip() + "\n", encoding="utf-8")


def delete(path: str) -> None:
    target = ROOT / path
    if target.exists():
        target.unlink()


README = r'''# djobs

<!-- mcp-name: io.github.jhuang-tw/djobs -->

**Crash-proof checkpoints and resumable task memory for AI coding agents.**

djobs keeps long coding work recoverable when a terminal command fails, an IDE
closes, or a chat loses context. Deterministic hooks cover the common command
path; compact MCP tools track structured multi-file work; `djobs gain` makes the
estimated context savings visible.

[![CI](https://github.com/jhuang-tw/djobs/actions/workflows/ci.yml/badge.svg)](https://github.com/jhuang-tw/djobs/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/djobs.svg)](https://pypi.org/project/djobs/)
[![Website](https://img.shields.io/badge/website-GitHub%20Pages-21835b.svg)](https://jhuang-tw.github.io/djobs/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

<p align="center">
  <img src="docs/demo.svg" alt="djobs crash recovery demo" width="700">
</p>

## Why djobs

A coding agent can finish twelve files, lose its chat, then spend the next
session re-reading the repository and guessing what remains. djobs stores exact
work state in SQLite so completed work stays completed and failed or interrupted
work can be resumed without replaying the whole conversation.

| Layer | What it does |
|---|---|
| `preToolUse` hook | Rewrites meaningful Bash or PowerShell commands through a durable wrapper before execution. |
| `sessionStart` hook | Injects unfinished and failed checkpoints into the next compatible session. |
| MCP tools | Track semantic tasks, evidence, batches, dependencies, revisions, and multi-agent claims. |
| `djobs gain` | Reports estimated savings for 24 hours, 30 days, and all time. |

Everything is local by default: one SQLite file, no Redis, no broker, and no
cloud service.

## Quick start

### VS Code and GitHub Copilot

Install the **djobs — Agent Checkpoints** extension from the Marketplace, then
run **djobs: Set up / Repair djobs** from the Command Palette.

The extension installs or repairs the Python runtime, registers the MCP server,
installs deterministic lifecycle hooks, and adds the task sidebar.

### CLI and MCP-compatible hosts

Install the runtime once, then initialize each repository:

```bash
pipx install djobs
cd your-repository
djobs init
```

`djobs init` writes the MCP configuration, installs the hook configuration,
adds optional agent guidance, runs `djobs doctor`, and prints the resolved queue
location.

Useful commands:

```bash
djobs doctor                 # verify runtime, MCP, database, and hooks
djobs hook install           # install or repair hooks only
djobs pause                  # temporarily disable hooks and recovery
djobs unpause                # re-enable them
djobs receipt                # evidence-backed work summary
djobs gain                   # estimated token/context savings
```

## Automatic command checkpoints

Smart mode checkpoints tests, builds, linters, type checks, and substantial
compound commands. It skips shell-state commands such as `cd` and read-only
commands such as `git status`.

```bash
djobs hook install --mode smart   # recommended
djobs hook install --mode all     # checkpoint almost every terminal command
djobs hook install --mode off     # keep config installed but disable rewriting
djobs hook install --global       # share ~/.djobs/global.db with MCP
djobs hook doctor                 # validate the installed hook file
```

The wrapper preserves the original command output and exit code. Successful
automatic checkpoints are archived after audit evidence is recorded; failed or
interrupted checkpoints remain recoverable. Hook handling is fail-open: a djobs
problem does not block the original coding task.

## Savings analytics

```bash
djobs gain                         # current workspace
djobs gain --graph                 # 30-day ASCII graph
djobs gain --history               # recent checkpoint estimates
djobs gain --daily                 # daily non-empty totals
djobs gain --all --format json     # all workspaces, machine-readable
djobs stats                        # alias
djobs state                        # alias
```

The report separates automatic-hook savings from durable-workflow savings. Its
numbers estimate avoided replay, re-reading, and re-planning using published
assumptions; they are not provider billing data.

## Structured workflows

For semantic multi-step work, the default MCP server exposes compact batch and
revision-aware tools such as:

- `enqueue_batch` and `complete_batch` for many tasks in one tool round trip.
- `resume_capsule` for a bounded recovery view.
- `resume_delta` for changes since a saved revision.
- `work_receipt` for evidence plus Git working-tree checks.
- `claim_task`, dependencies, resource locks, and agent heartbeats for shared queues.

Advanced queue features also include retries, dead-letter preservation, SQLite
and PostgreSQL backends, a local read-only web dashboard, and a full audit log.

## Compatibility

| Host | Current status |
|---|---|
| GitHub Copilot in VS Code | Automatic hooks, MCP registration, setup, and sidebar are implemented and tested. |
| GitHub Copilot CLI/cloud hook format | Supported by the hook adapter and unit tests. |
| Claude Code, Codex, Cursor, Cline, Gemini, other MCP hosts | MCP workflows are available when the host supports MCP. Automatic hook behavior depends on that host's hook protocol and still needs broader real-world validation. |
| Plain browser chat without tools | Not automatic; djobs needs MCP or a compatible installed hook host. |

The compatibility table is intentionally conservative: shared protocol support
is not presented as proof of full end-to-end validation on every agent.

## Safety and privacy

- Queue data stays local unless you intentionally point clients at a shared database.
- Default MCP auto-approval is read-only; write tools remain explicit unless enabled.
- `djobs pause` disables rewriting and recovery without deleting state.
- The local dashboard binds to `127.0.0.1` by default and has no public-auth layer.
- Tool output is treated as data, not as instructions that override the user.

## Maintained documentation

To prevent documentation drift, this repository keeps a small set of canonical
sources:

- `README.md` — product behavior, setup, compatibility, and user commands.
- `CONTRIBUTING.md` — development workflow and architecture map.
- `AGENTS.md` — short rules for AI contributors working on this repository.
- `CHANGELOG.md` — release history and release notes.
- `docs/RELEASE.md` — the release runbook.

The live landing page is generated from the same product claims in
`docs/index.html`; implementation truth remains in code and tests.

## Development

```bash
git clone https://github.com/jhuang-tw/djobs.git
cd djobs
python -m venv .venv
pip install -e ".[dev,pg]"
pre-commit install
pre-commit run --all-files
pytest -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before changing code and
[docs/RELEASE.md](docs/RELEASE.md) before publishing.

## License

[MIT](LICENSE)
'''

CONTRIBUTING = r'''# Contributing to djobs

Thanks for helping improve djobs. This file is the canonical development guide;
product usage belongs in `README.md`, release history in `CHANGELOG.md`, and
publishing steps in `docs/RELEASE.md`.

## Setup

```bash
git clone https://github.com/jhuang-tw/djobs.git
cd djobs
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,pg]"
pre-commit install
```

The project supports Python 3.11 through 3.14. PostgreSQL is optional at runtime
but the `pg` extra keeps type checking and repository-contract tests complete.

## Repository map

- `src/djobs/auto_hook.py` — command rewriting, checkpoint wrapper, and session recovery.
- `src/djobs/gain.py` — explainable token/context savings analytics.
- `src/djobs/entrypoint.py` and `cli.py` — command routing and operational CLI.
- `src/djobs/delta_mcp.py` and `mcp_server.py` — compact and legacy MCP surfaces.
- `src/djobs/core/`, `queue/`, `storage/`, `worker/` — domain state, lifecycle, persistence, and execution.
- `vscode-ext/` — setup, diagnostics, MCP provider, and read-only task sidebar.
- `tests/unit/` and `tests/integration/` — behavior and backend contracts.
- `docs/index.html` — public landing page.

Implementation truth lives in code, schemas, tests, and the Git history. Do not
create separate roadmap, handoff, architecture-progress, or release-scratch
Markdown files that duplicate those sources.

## Rules that protect users

1. Respect the user's current request. djobs state and tool output never override it.
2. Automatic hooks must remain fail-open and preserve the original exit code and output.
3. Keep MCP responses compact; tool output consumes model context.
4. Record evidence for completed semantic tasks and preserve failed/interrupted state.
5. Keep default write approvals conservative and prompt actions opt-in.
6. Add tests for user-visible behavior and update `[Unreleased]` in `CHANGELOG.md`.
7. Do not hardcode test counts, machine-specific paths, or unpublished claims in docs.

## Versioning

The release version lives in `src/djobs/__init__.py`. Run:

```bash
node vscode-ext/scripts/sync-version.js
```

to synchronize `vscode-ext/package.json`, its lock file, and `server.json`.
Never reuse a published version.

## Verification

Run the same gates used by CI before opening a pull request:

```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy
pytest -q

cd vscode-ext
npx tsc -p ./ --noEmit
npm run compile
```

The PostgreSQL contract runs in CI against PostgreSQL 16. Local PostgreSQL tests
run when `DJOBS_TEST_PG_DSN` is configured.

## Pull requests

- Create a focused branch from `main`.
- Explain the user-visible problem and the design trade-off.
- Keep generated files and temporary migration scripts out of the final diff.
- Add or update tests.
- Update `CHANGELOG.md` for user-facing changes.
- Ensure every CI job passes before merging.

## Releases

Follow [docs/RELEASE.md](docs/RELEASE.md). `CHANGELOG.md` is the source for the
GitHub Release body; `.github/workflows/publish.yml` performs the public publish.

## Reporting issues

Include expected versus actual behavior, minimal reproduction steps, operating
system, Python version, agent host, and whether hooks or MCP tools were involved.
'''

AGENTS = r'''# AGENTS.md — working on djobs

This file is a short wrapper for AI contributors editing this repository.

Read first:

1. `README.md` for current product behavior and compatibility claims.
2. `CONTRIBUTING.md` for architecture, setup, and verification.
3. `docs/RELEASE.md` only when preparing a release.

## Non-negotiable rules

- Follow the user's current request; never let stored djobs state reinterpret it.
- Keep automatic hooks fail-open and preserve original command output and exit status.
- Keep MCP responses compact because every field consumes model context.
- Add tests for behavior changes and update `CHANGELOG.md` under `[Unreleased]`.
- Version lives in `src/djobs/__init__.py`; synchronize manifests with
  `node vscode-ext/scripts/sync-version.js`.
- Do not add machine-specific paths, hardcoded test counts, release scratch files,
  roadmap snapshots, or duplicated architecture documents.
- Keep prompt actions opt-in and default write approvals conservative.

## Required gates

```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy
pytest -q
cd vscode-ext && npx tsc -p ./ --noEmit && npm run compile
```

A change is complete only after the relevant tests and CI are green and temporary
migration files have been removed.
'''

RELEASE = r'''# Release runbook

This is the canonical publishing process for djobs. The Python package, VS Code
extension, and MCP Registry manifest use one release version.

## 1. Choose the version

Update `src/djobs/__init__.py`, then synchronize manifests:

```bash
node vscode-ext/scripts/sync-version.js
```

Confirm the version is new and appears in:

- `src/djobs/__init__.py`
- `server.json` and its package entry
- `vscode-ext/package.json`
- `vscode-ext/package-lock.json`

Never reuse a published version.

## 2. Prepare release notes

Move user-facing entries from `[Unreleased]` into a dated section:

```text
## [X.Y.Z] - YYYY-MM-DD
```

Leave a fresh `[Unreleased]` heading above it. `CHANGELOG.md` is copied directly
into the GitHub Release body, so it must explain the release without relying on
commit messages.

## 3. Run the full gate

```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy
pytest -q
cd vscode-ext
npx tsc -p ./ --noEmit
npm run compile
```

Open and merge a pull request, then wait for main-branch CI to pass.

## 4. Publish exactly one commit

The preferred path is to update `.github/release.json` on `main` with the new
version and the exact release commit SHA. The `Release` workflow validates every
manifest, creates the tag and GitHub Release, publishes to PyPI, and publishes the
VS Code extension.

A manual tag is also supported:

```bash
git tag vX.Y.Z <release-commit-sha>
git push origin vX.Y.Z
```

Never use `git push --tags`; publish only the intended tag.

## 5. Verify public surfaces

Confirm:

- the GitHub Release body matches the changelog section;
- PyPI shows the new package version;
- the VS Code Marketplace shows the matching extension version;
- `server.json` matches the release;
- GitHub Pages deployed successfully.

Do not edit already-published artifacts under the same version. Make another
versioned release for follow-up fixes.
'''

EXT_README = r'''# djobs — Agent Checkpoints

![djobs agent checkpoints](https://raw.githubusercontent.com/jhuang-tw/djobs/main/vscode-ext/media/banner.png)

**Crash-proof checkpoints and resumable task memory for AI coding agents.**

This extension is the one-click setup and task view for djobs. It installs or
repairs the runtime, registers the MCP server, installs deterministic lifecycle
hooks, runs diagnostics, and shows recoverable work in the sidebar.

## Setup

1. Install the extension.
2. Run **djobs: Set up / Repair djobs** from the Command Palette.
3. Start a new compatible agent session and work normally.

Meaningful terminal commands are checkpointed before execution. Failed or
interrupted checkpoints can be injected into the next session; successful
checkpoints remain auditable without filling the active task view.

## What the extension provides

- Automatic `preToolUse` command checkpointing and `sessionStart` recovery setup.
- MCP server registration for structured multi-file workflows.
- Current-workspace and global queue views.
- Pause/resume, archive, delete, history, evidence, and setup diagnostics.
- Optional prompt actions, disabled by default.
- Local SQLite storage by default; no hosted service required.

## See the savings

Run in the integrated terminal:

```bash
djobs gain
djobs gain --graph
djobs gain --history
djobs gain --all --format json
```

The report separates automatic checkpoints from structured workflows and labels
its numbers as estimates rather than provider billing data.

## Compatibility

Automatic hooks, setup, and the sidebar are implemented and tested with GitHub
Copilot in VS Code. MCP workflows can be used by other MCP-compatible hosts;
automatic behavior depends on each host's hook protocol and still needs broader
real-world validation.

## Privacy and control

Queue data is local unless you configure a shared database. Default MCP write
actions are conservative. **Pause djobs** disables rewriting and recovery without
deleting state. Prompt actions remain opt-in.

For commands, architecture, and troubleshooting, use the repository
[README](https://github.com/jhuang-tw/djobs). Report issues in the repository's
issue tracker.
'''

INDEX = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>djobs — Crash-proof checkpoints for AI coding agents</title>
  <meta name="description" content="djobs provides crash-proof checkpoints, resumable task memory, automatic command hooks, and visible context-savings analytics for AI coding agents.">
  <meta name="keywords" content="AI coding agent, agent checkpoint, agent memory, crash recovery, context recovery, token savings, MCP, GitHub Copilot, Claude Code, Codex, Cursor, Cline, Gemini">
  <meta name="author" content="jhuang-tw">
  <meta name="robots" content="index, follow, max-image-preview:large">
  <meta name="theme-color" content="#272a3a">
  <link rel="canonical" href="https://jhuang-tw.github.io/djobs/">
  <meta name="google-site-verification" content="ioDXvGBVsrtGQCvsd5pkSm9ZyYfN_GnipBDsYvz76Go">

  <meta property="og:type" content="website">
  <meta property="og:site_name" content="djobs">
  <meta property="og:url" content="https://jhuang-tw.github.io/djobs/">
  <meta property="og:title" content="djobs — Crash-proof checkpoints for AI coding agents">
  <meta property="og:description" content="Resume long coding work after crashes or context loss, and see the estimated context savings with djobs gain.">
  <meta property="og:image" content="https://raw.githubusercontent.com/jhuang-tw/djobs/main/vscode-ext/media/banner.png">
  <meta property="og:image:alt" content="djobs agent checkpoints">

  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="djobs — Crash-proof checkpoints for AI coding agents">
  <meta name="twitter:description" content="Automatic command checkpoints, resumable MCP workflows, and visible context-savings analytics.">
  <meta name="twitter:image" content="https://raw.githubusercontent.com/jhuang-tw/djobs/main/vscode-ext/media/banner.png">

  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    "name": "djobs",
    "description": "Crash-proof checkpoints and resumable task memory for AI coding agents.",
    "applicationCategory": "DeveloperApplication",
    "operatingSystem": "Windows, macOS, Linux",
    "url": "https://jhuang-tw.github.io/djobs/",
    "downloadUrl": "https://pypi.org/project/djobs/",
    "codeRepository": "https://github.com/jhuang-tw/djobs",
    "license": "https://opensource.org/licenses/MIT",
    "programmingLanguage": "Python",
    "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"}
  }
  </script>

  <style>
    :root { color-scheme: light; --ink:#202330; --muted:#5f6675; --panel:#fff; --paper:#f5f3ed; --line:#dfdcd3; --violet:#6265e7; --dark:#272a3a; }
    * { box-sizing: border-box; }
    body { margin:0; font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; color:var(--ink); background:var(--paper); line-height:1.6; }
    a { color:inherit; }
    header, main, footer { width:min(1080px, calc(100% - 32px)); margin:auto; }
    nav { display:flex; align-items:center; justify-content:space-between; padding:22px 0; }
    nav strong { font-size:1.2rem; }
    .links { display:flex; gap:18px; flex-wrap:wrap; }
    .hero { padding:72px 0 50px; display:grid; grid-template-columns:1.2fr .8fr; gap:44px; align-items:center; }
    h1 { font-size:clamp(2.6rem,7vw,5.5rem); line-height:1; letter-spacing:-.055em; margin:0 0 24px; }
    h2 { font-size:clamp(1.7rem,4vw,2.5rem); letter-spacing:-.03em; margin:0 0 18px; }
    .lead { font-size:1.2rem; color:var(--muted); max-width:720px; }
    .actions { display:flex; gap:12px; flex-wrap:wrap; margin-top:28px; }
    .button { display:inline-block; padding:12px 18px; border-radius:10px; text-decoration:none; font-weight:700; border:1px solid var(--dark); }
    .primary { color:#fff; background:var(--dark); }
    .terminal { background:#11150f; color:#e8f5e8; border-radius:16px; padding:24px; font:14px/1.8 ui-monospace,SFMono-Regular,Consolas,monospace; box-shadow:0 24px 70px rgba(32,35,48,.18); overflow:auto; }
    section { padding:54px 0; }
    .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; }
    .card { background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:24px; }
    .card h3 { margin-top:0; }
    .muted { color:var(--muted); }
    code { font-family:ui-monospace,SFMono-Regular,Consolas,monospace; }
    footer { padding:42px 0 60px; color:var(--muted); border-top:1px solid var(--line); }
    @media (max-width:760px) { .hero,.grid { grid-template-columns:1fr; } .hero { padding-top:40px; } }
  </style>
</head>
<body>
<header>
  <nav>
    <strong>djobs</strong>
    <div class="links">
      <a href="https://github.com/jhuang-tw/djobs">GitHub</a>
      <a href="https://pypi.org/project/djobs/">PyPI</a>
      <a href="https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs">VS Code</a>
    </div>
  </nav>
  <div class="hero">
    <div>
      <h1>Finish the work after the context is gone.</h1>
      <p class="lead">djobs gives AI coding agents crash-proof checkpoints, resumable task memory, deterministic command hooks, and an explainable view of estimated context saved.</p>
      <div class="actions">
        <a class="button primary" href="https://github.com/jhuang-tw/djobs">View on GitHub</a>
        <a class="button" href="https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs">Install extension</a>
      </div>
    </div>
    <div class="terminal"><span class="muted">$</span> pipx install djobs<br><span class="muted">$</span> djobs init<br><br><span class="muted">$</span> djobs gain<br>24h / 30d / all-time estimates<br>automatic hooks + durable workflows</div>
  </div>
</header>
<main>
  <section>
    <h2>Two recovery layers, one local state file.</h2>
    <div class="grid">
      <article class="card"><h3>Automatic command checkpoints</h3><p>Compatible hosts rewrite meaningful tests, builds, linters, and compound terminal commands through a fail-open wrapper before execution.</p></article>
      <article class="card"><h3>Structured MCP workflows</h3><p>Batch tasks, evidence, bounded resume capsules, revision deltas, dependencies, and multi-agent claims handle semantic multi-file work.</p></article>
      <article class="card"><h3>Session recovery</h3><p>Failed and interrupted checkpoints stay recoverable. Successful automatic checkpoints remain auditable without filling the active task list.</p></article>
      <article class="card"><h3>Visible savings</h3><p><code>djobs gain</code> separates automatic and workflow estimates across 24 hours, 30 days, and all time, with graph, history, daily, and JSON views.</p></article>
    </div>
  </section>
  <section>
    <h2>Local by default, conservative by design.</h2>
    <p class="lead">SQLite requires no broker or hosted service. Hooks preserve the original exit code and fail open. Write approvals stay conservative, prompt actions are opt-in, and <code>djobs pause</code> disables automation without deleting state.</p>
  </section>
  <section>
    <h2>Compatibility without inflated claims.</h2>
    <p class="lead">Automatic hooks, setup, and the sidebar are implemented and tested with GitHub Copilot in VS Code. Other MCP-capable agents can use durable workflow tools; automatic behavior depends on each host's hook protocol and still needs broader real-world validation.</p>
  </section>
</main>
<footer>MIT licensed. Source, documentation, compatibility details, and issue tracking live in the GitHub repository.</footer>
</body>
</html>
'''

write("README.md", README)
write("CONTRIBUTING.md", CONTRIBUTING)
write("AGENTS.md", AGENTS)
write("docs/RELEASE.md", RELEASE)
write("vscode-ext/README.md", EXT_README)
write("docs/index.html", INDEX)

# Python package metadata and version.
init_path = ROOT / "src/djobs/__init__.py"
init_text = init_path.read_text(encoding="utf-8")
init_text = init_text.replace(
    '"""djobs — durable workflow state and context-efficient orchestration for coding agents.',
    '"""djobs — crash-proof checkpoints and resumable task memory for coding agents.',
    1,
)
init_text = init_text.replace('__version__ = "0.10.0"', f'__version__ = "{VERSION}"', 1)
init_path.write_text(init_text, encoding="utf-8")

pyproject_path = ROOT / "pyproject.toml"
pyproject = pyproject_path.read_text(encoding="utf-8")
pyproject = pyproject.replace(
    'description = "Durable workflow state and context-efficient MCP orchestration for coding agents."',
    'description = "Crash-proof checkpoints and resumable task memory for AI coding agents."',
    1,
)
old_keywords = 'keywords = ["mcp", "mcp-server", "ai-agent", "ai-coding-agent", "codex", "claude-code", "gemini", "copilot", "cursor", "cline", "context-efficiency", "crash-recovery", "workflow-state", "durable", "sqlite", "audit-log"]'
new_keywords = 'keywords = ["ai-agent", "coding-agent", "agent-checkpoint", "agent-memory", "crash-recovery", "context-recovery", "resumable-workflow", "token-savings", "mcp", "model-context-protocol", "copilot", "claude-code", "codex", "cursor", "cline", "gemini", "multi-agent", "workflow-state", "sqlite"]'
if old_keywords not in pyproject:
    raise RuntimeError("pyproject keyword surface changed unexpectedly")
pyproject_path.write_text(pyproject.replace(old_keywords, new_keywords, 1), encoding="utf-8")

# VS Code Marketplace manifest: preserve all contributions, update discoverability.
package_path = ROOT / "vscode-ext/package.json"
package = json.loads(package_path.read_text(encoding="utf-8"))
package["displayName"] = "djobs — Agent Checkpoints"
package["description"] = "Crash-proof checkpoints and resumable task memory for AI coding agents."
package["version"] = VERSION
package["homepage"] = "https://jhuang-tw.github.io/djobs/"
package["bugs"] = {"url": "https://github.com/jhuang-tw/djobs/issues"}
package["categories"] = ["Machine Learning", "Other"]
package["keywords"] = [
    "djobs",
    "ai agent",
    "coding agent",
    "agent checkpoint",
    "agent memory",
    "crash recovery",
    "context recovery",
    "resumable workflow",
    "token savings",
    "MCP",
    "Model Context Protocol",
    "GitHub Copilot",
    "Claude Code",
    "Codex",
    "Cursor",
    "Cline",
    "Gemini",
    "multi-agent",
    "workflow state",
    "task queue",
]
package["galleryBanner"] = {"color": "#272A3A", "theme": "dark"}
package["preview"] = True
package["pricing"] = "Free"
package_path.write_text(json.dumps(package, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

lock_path = ROOT / "vscode-ext/package-lock.json"
lock = json.loads(lock_path.read_text(encoding="utf-8"))
lock["version"] = VERSION
lock["packages"][""]["version"] = VERSION
lock_path.write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

server_path = ROOT / "server.json"
server = json.loads(server_path.read_text(encoding="utf-8"))
server["title"] = "djobs — Agent Checkpoints"
server["description"] = "Crash-proof checkpoints and resumable task memory for AI coding agents."
server["version"] = VERSION
for package_entry in server.get("packages", []):
    package_entry["version"] = VERSION
server_path.write_text(json.dumps(server, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# Preserve historical changelog, but make current policy and release complete.
changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text(encoding="utf-8")
old_policy = '''## Versioning policy

This repository ships two independently versioned artifacts:

- **`djobs` Python package** — the durable queue, MCP server, and CLI. It is
  **pre-1.0**: the public API may still change between minor versions.
- **`djobs` VS Code extension** (under `vscode-ext/`) — a thin, read-only
  sidebar over the CLI. Its UI surface is small and stable, so it carries its
  own version line and may sit at a higher number than the Python package.

Entries below are tagged `[core]` or `[ext]` when a change applies to only one
artifact.
'''
new_policy = '''## Versioning policy

The Python package, VS Code extension, and MCP Registry manifest are released in
lockstep from the version in `src/djobs/__init__.py`. The project is pre-1.0, so
public interfaces may still change between minor versions. Entries below use
`[core]`, `[ext]`, `[docs]`, or `[release]` to identify the affected surface.
'''
if old_policy not in changelog:
    raise RuntimeError("CHANGELOG version policy changed unexpectedly")
changelog = changelog.replace(old_policy, new_policy, 1)
anchor = "## [Unreleased]\n\n## [0.10.0] - 2026-07-21"
release = f'''## [Unreleased]

## [{VERSION}] - {DATE}

### Added
- `[core]` **Deterministic command checkpoints.** `djobs init` now installs compatible `preToolUse` and `sessionStart` hooks that checkpoint meaningful Bash and PowerShell commands before execution, preserve output and exit status, and restore failed or interrupted work without relying on the model to remember an MCP call.
- `[core]` **Explainable savings analytics.** Added `djobs gain` with `stats` and `state` aliases, 24-hour, 30-day, and all-time views, source breakdowns, daily history, an ASCII graph, recent records, and JSON export. Values are explicitly labeled estimates rather than provider billing data.

### Changed
- `[core]` **Cleaner checkpoint lifecycle.** Successful automatic checkpoints are archived after evidence is recorded, while failed and interrupted checkpoints remain visible and recoverable. Hook processing remains fail-open, and custom or global database paths are shared with MCP configuration.
- `[docs]` **One maintained documentation system.** Rewrote the README, contributor guide, AI contributor rules, release runbook, Marketplace page, package metadata, MCP manifest, and public website around one product description and a conservative compatibility matrix.
- `[ext]` **Marketplace discoverability.** Renamed the extension surface to Agent Checkpoints, added supported metadata, categories, and search terms, and aligned the extension README with automatic hooks and `djobs gain`.

### Removed
- `[docs]` **Stale duplicated documentation and scratch tooling.** Removed phase roadmaps, AI handoff snapshots, obsolete architecture and implementation notes, duplicated Durable Coder prompts, machine-specific contributor skills, accidental diff files, and outdated packaging/release scripts that contradicted the current code or release workflow.

## [0.10.0] - 2026-07-21'''
if anchor not in changelog:
    raise RuntimeError("CHANGELOG release anchor changed unexpectedly")
changelog_path.write_text(changelog.replace(anchor, release, 1), encoding="utf-8")

# Remove snapshots, prompt takeovers, and scratch files that cannot stay current.
for stale in [
    "CLAUDE.md",
    ".agent.md",
    ".github/agents/durable-coder.agent.md",
    ".github/skills/djobs-development/SKILL.md",
    "docs/ARCHITECTURE.md",
    "docs/ROADMAP.md",
    "docs/HANDOFF.md",
    "docs/IMPLEMENTATION_NOTES.md",
    "docs/INTERNALS.md",
    "docs/CONTEXT_EFFICIENCY.md",
    "CHANGES.diff",
    "_pack_080.ps1",
    "scripts/release.ps1",
]:
    delete(stale)

# Guard the final public surface against the exact drift this cleanup removes.
for path in [
    "README.md",
    "CONTRIBUTING.md",
    "AGENTS.md",
    "docs/RELEASE.md",
    "vscode-ext/README.md",
    "docs/index.html",
    "pyproject.toml",
    "server.json",
    "vscode-ext/package.json",
]:
    text = (ROOT / path).read_text(encoding="utf-8")
    for forbidden in [
        "docs/INTERNALS.md",
        "docs/ARCHITECTURE.md",
        "At the start of EVERY conversation",
        "C:\\dev\\djobs",
        "c:\\src\\my\\djobs",
        "v0.10.0",
    ]:
        if forbidden in text:
            raise RuntimeError(f"{path} still contains stale surface: {forbidden}")

for stale in [
    "CLAUDE.md",
    ".agent.md",
    ".github/agents/durable-coder.agent.md",
    ".github/skills/djobs-development/SKILL.md",
    "docs/ARCHITECTURE.md",
    "docs/ROADMAP.md",
    "docs/HANDOFF.md",
    "docs/IMPLEMENTATION_NOTES.md",
    "docs/INTERNALS.md",
    "docs/CONTEXT_EFFICIENCY.md",
    "CHANGES.diff",
    "_pack_080.ps1",
    "scripts/release.ps1",
]:
    if (ROOT / stale).exists():
        raise RuntimeError(f"stale file was not removed: {stale}")

print(f"public surface cleanup prepared for {VERSION}")
