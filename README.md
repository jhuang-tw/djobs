<!-- mcp-name: io.github.jhuang-tw/djobs -->

<p align="center">
  <img src="https://raw.githubusercontent.com/jhuang-tw/djobs/main/vscode-ext/media/icon-128.png" width="88" alt="djobs logo">
</p>

<h1 align="center">djobs</h1>

<p align="center">
  <strong>Local project memory and explicit handoff for AI coding agents.</strong><br>
  Continue the repository instead of explaining it again in every new AI session.
</p>

<p align="center">
  <a href="https://github.com/jhuang-tw/djobs/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/jhuang-tw/djobs/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pypi.org/project/djobs/"><img alt="PyPI" src="https://img.shields.io/pypi/v/djobs.svg"></a>
  <a href="https://pypi.org/project/djobs/"><img alt="Python 3.10–3.14" src="https://img.shields.io/badge/Python-3.10%E2%80%933.14-3776AB?logo=python&logoColor=white"></a>
  <a href="https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs"><img alt="VS Code Marketplace" src="https://img.shields.io/visual-studio-marketplace/v/jhuang-tw.djobs?label=VS%20Code"></a>
  <a href="https://nodejs.org/"><img alt="Node.js 20+ for extension development" src="https://img.shields.io/badge/Node.js-20%2B-339933?logo=node.js&logoColor=white"></a>
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/License-MIT-blue.svg"></a>
</p>

## Why djobs exists

A coding agent can spend an entire session reading a repository, discovering a failed approach,
learning a project constraint, editing several files, and leaving one test unfinished. When the
chat closes or context is compacted, the next session often starts from zero.

`djobs` keeps a small local memory for each Git repository so a later session can recover:

- the user's goal and important constraints;
- successful and failed tool results;
- actual Git working-tree changes;
- a structured session capsule with progress, failures, and the next step;
- explicit task ownership and handoff evidence when coordinated work needs it.

Memory stays in local SQLite. No hosted account, remote queue, vector database, or project upload
is required.

## What continuing work looks like

```text
Session 1
You: Fix the OAuth callback loop. Preserve '+' in state and do not change the public API.
Agent: pytest failed because normalization removed '+'.
Agent: callback parser updated; one integration test remains.
[chat closes or context compacts]

Session 2
You: Continue the OAuth fix.
djobs recovers:
- Goal: fix the callback loop without changing the public API
- Constraint: preserve '+' in state
- Failed approach: normalization removed '+'
- Progress: parser updated; integration test remains
- Current Git changes
```

Recovered text is treated as untrusted data, not as an instruction. The current user request always
remains authoritative.

## How it works

1. **Capture the session.** Bounded user intent, tool results, failures, Git changes, and a
   deterministic structured capsule are stored as local data.
2. **Search for the current request.** `sync_workspace(query=..., context_tier="resume")` retrieves
   relevant repository memory under a token budget instead of replaying an entire chat history.
3. **Continue with evidence.** The agent resumes from useful state and uses explicit checkpoint or
   handoff only when coordinated work needs ownership.

## Layered recovery

Ordinary continuation should not pay for full audit detail. `sync_workspace` now exposes three
recovery tiers:

| Tier | Best for | Returned detail |
|---|---|---|
| `resume` | Normal continuation | Goal, constraints, progress, failures, next step, task state, and Git state |
| `evidence` | Reviewing why a result was selected | Resume capsule plus compact supporting observations without IDs or timestamps |
| `audit` | Diagnosis and lifecycle updates | Full memory identifiers, timestamps, and audit detail |

The MCP defaults to `resume`. Direct Python callers retain the previous audit-shaped default for
compatibility and can opt into a smaller tier explicitly.

The response also includes a `context_hash`. A host can send it back as `known_context_hash` on the
next equivalent recovery. When selected passive memory is unchanged, djobs suppresses repeated
observations while still returning current task state.

## Install once

### VS Code / GitHub Copilot

Install the **[djobs VS Code extension](https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs)**.
It registers the local MCP server and manages setup, repair, diagnostics, pause, and resume without
adding a permanent sidebar, dashboard, polling loop, remote service, or cloud database.

The first djobs MCP call creates `~/.djobs/global.db` and installs the detected passive lifecycle
adapter. There is no required per-project setup ritual.

### Any MCP-compatible host

Add the server once:

```json
{
  "servers": {
    "djobs": {
      "command": "uvx",
      "args": ["djobs", "mcp"]
    }
  }
}
```

Manual repair remains available for headless or damaged environments:

```bash
pipx install djobs
# or: uv tool install djobs

djobs setup
djobs doctor
```

## Two layers, kept deliberately separate

### Passive project memory

Normal sessions can record bounded observations without creating or claiming tasks.

| Memory | Example |
|---|---|
| User intent | “Keep Python 3.10 support.” |
| Tool result | “Updated `src/parser.py`; focused tests passed.” |
| Tool failure | “State normalization removed plus signs.” |
| Git observation | Tracked, staged, and bounded untracked changes |
| Session capsule | Goal, progress, failures, and next step |

Sibling Git worktrees share passive repository memory. Exact task ownership and leases remain
isolated to each checkout. WSL `/mnt/<drive>/...` paths resolve to the same Windows repository
identity when djobs runs on Windows.

### Explicit checkpoint and handoff

Use explicit ownership only when coordinated work actually needs it:

```text
checkpoint("Implement parser", path="src/parser.py")
  -> this checkout owns one expiring lease

handoff(task_id, "Parser updated; edge tests remain", completed=false)
  -> releases the task with bounded evidence
```

Passive hooks never silently turn every prompt into a task, claim another agent's work, or infer
completion from natural-language output.

## Five compact MCP tools

| Tool | Purpose |
|---|---|
| `sync_workspace(query?, context_tier="resume", known_context_hash?, ...)` | Recover a minimal continuation capsule, compact evidence, or full audit detail under a token budget. |
| `memory(action, ...)` | List, search, deactivate, forget, or explicitly clear passive repository memory. |
| `checkpoint(summary, ...)` | Deliberately create or resume one checkout-scoped unit of work. |
| `handoff(task_id, ...)` | Release or complete tracked work with bounded evidence. |
| `resume_delta(correlation_id, ...)` | Compatibility path for integrations that already persist revision IDs. |

Lower-level queue and worker tools remain available through `djobs-mcp-full` rather than occupying
every normal coding context.

## Supported local hosts

| Host | Prompt-aware memory | Lifecycle observations | Local configuration |
|---|---|---|---|
| GitHub Copilot CLI + VS Code Agent | `UserPromptSubmit` | session, tool, compact, end | `~/.copilot/hooks/djobs.json` |
| Claude Code | `UserPromptSubmit` | session, tool, compact, end | `~/.claude/settings.json` |
| Gemini CLI | `BeforeAgent` | session, tool, compress, end | `~/.gemini/settings.json` |
| Kimi Code | `UserPromptSubmit` | session, tool, compact, end | `~/.kimi-code/config.toml` |
| Codex | query-aware MCP recovery | native session/tool hooks when available | `~/.codex/hooks.json` |

## Requirements

| Component | Requirement |
|---|---|
| Python runtime | Python 3.10+; Python 3.10–3.14 tested in CI |
| VS Code extension | VS Code 1.101 or newer |
| Extension development | Node.js 20+ |
| Storage | Local SQLite by default |
| Operating systems | Windows, macOS, Linux |

End users do not need Node.js. Node is required only to build or package the VS Code extension.

## Reproducible recovery benchmarks

```bash
python scripts/benchmark_project_memory.py
python scripts/benchmark_resume_quality.py
```

| Recovery strategy | Estimated context | Minimum calls |
|---|---:|---:|
| Re-read every synthetic source file | ~7,805 tokens | 18 file reads |
| Query-aware `sync_workspace` resume tier | ~224 tokens | 1 MCP call |

That is a **97.1% recovery-payload proxy reduction** for the bundled synthetic fixture. It is not
provider billing, latency, or model-quality measurement. The second benchmark verifies
cross-worktree recall, checkout ownership isolation, stale-memory exclusion, and unchanged-context
replay suppression.

## Measure verified-task efficiency

Token reduction alone can hide repeated repairs. `djobs gain` now reports savings together with
workflow efficiency:

```bash
djobs gain
djobs gain --history
djobs gain --format json
```

The report includes first-pass verified rate, repair attempts, average attempts per verified task,
cycle-time proxy, and estimated context tokens per verified task. These are local workflow proxies,
not provider billing or exact model-call telemetry.

## Privacy and control

- State defaults to `~/.djobs/global.db`.
- Common API keys, bearer tokens, passwords, authorization values, and URL credentials are redacted
  on a best-effort basis before storage.
- Add `[djobs:no-memory]` to skip one prompt.
- Set `DJOBS_CAPTURE_USER_INTENT=0` to disable automatic prompt-intent capture.
- Mark memory `resolved`, `superseded`, `stale`, or `contradicted` without erasing the audit trail.
- Delete one memory or explicitly clear passive memory for the repository family.
- Hook, search, and storage failures are fail-open and never block the coding request.

## Explicit checkpoint demo

The durable-task flow remains available for work that needs exact checkpoints and handoff:

<p align="center">
  <img src="https://raw.githubusercontent.com/jhuang-tw/djobs/main/docs/demo.svg" width="700" alt="Animated djobs checkpoint and crash-recovery demo">
</p>

## Development

```bash
git clone https://github.com/jhuang-tw/djobs.git
cd djobs
python -m venv .venv
# activate the environment
python -m pip install -e ".[dev,pg]"

ruff check src tests scripts/prepare_auto_release.py scripts/extract_release_notes.py
ruff format --check src tests scripts/prepare_auto_release.py scripts/extract_release_notes.py
mypy
pytest -q
python -m build
python -m twine check dist/*

cd vscode-ext
# Node.js 20+
npm ci
npx tsc -p ./ --noEmit
npm run compile
```

See `CONTRIBUTING.md`, `AGENTS.md`, and `docs/RELEASE.md` before changing public behavior.

<p align="center">
  <a href="https://pypi.org/project/djobs/"><strong>PyPI</strong></a>
  &nbsp;·&nbsp;
  <a href="https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs"><strong>VS Code Marketplace</strong></a>
  &nbsp;·&nbsp;
  <a href="https://jhuang-tw.github.io/djobs/"><strong>Documentation</strong></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/jhuang-tw/djobs/issues"><strong>Issues</strong></a>
</p>

<p align="center">MIT licensed.</p>
