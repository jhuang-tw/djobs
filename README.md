<!-- mcp-name: io.github.jhuang-tw/djobs -->

<p align="center">
  <img src="https://raw.githubusercontent.com/jhuang-tw/djobs/main/vscode-ext/media/icon-128.png" width="104" alt="djobs logo">
</p>

<h1 align="center">djobs</h1>

<p align="center">
  <strong>Local project memory and explicit handoff for AI coding agents.</strong><br>
  Continue the repository instead of explaining it again in every new session.
</p>

<p align="center">
  <a href="https://github.com/jhuang-tw/djobs/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/jhuang-tw/djobs/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pypi.org/project/djobs/"><img alt="PyPI" src="https://img.shields.io/pypi/v/djobs.svg"></a>
  <a href="https://pypi.org/project/djobs/"><img alt="Python 3.10–3.14" src="https://img.shields.io/badge/Python-3.10%E2%80%933.14-3776AB?logo=python&logoColor=white"></a>
  <a href="https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs"><img alt="VS Code Marketplace" src="https://img.shields.io/visual-studio-marketplace/v/jhuang-tw.djobs?label=VS%20Code"></a>
  <a href="https://nodejs.org/"><img alt="Node.js 20+ for extension development" src="https://img.shields.io/badge/Node.js-20%2B-339933?logo=node.js&logoColor=white"></a>
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/License-MIT-blue.svg"></a>
</p>

<p align="center">
  <a href="https://jhuang-tw.github.io/djobs/"><img src="https://raw.githubusercontent.com/jhuang-tw/djobs/main/vscode-ext/media/banner.png" width="760" alt="djobs — local project memory for coding agents"></a>
</p>

## Why djobs exists

A coding agent can spend an entire session reading a repository, discovering a failed approach,
learning a project constraint, editing several files, and leaving one test unfinished. When the
chat closes or context is compacted, the next session often starts from zero.

`djobs` keeps a small local memory for each Git repository so a later session can recover:

- the user's goal and important constraints;
- successful and failed tool results;
- actual Git working-tree changes;
- a compact session capsule with progress, failures, and the next step;
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

The recovered text is treated as untrusted data, not as an instruction. The current user request
always remains authoritative.

## Install once

### VS Code / GitHub Copilot

Install the **[djobs VS Code extension](https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs)**.
It registers the local MCP server and manages setup, repair, diagnostics, pause, and resume.

There is no required per-project wizard. The first djobs MCP call creates the local database and
installs only the detected host's passive lifecycle adapter.

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

Manual repair commands remain available for headless or damaged environments:

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
isolated to each checkout.

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

## Query-aware recovery

Agents normally recover with:

```text
sync_workspace(query="the user's current request")
```

The query searches repository-family memory using SQLite FTS5 when available and a bounded
portable fallback otherwise. Relevant older constraints or failed approaches can rank above newer
but unrelated activity.

The response includes a `context_hash`. A host can return it as `known_context_hash` on the next
equivalent recovery; unchanged passive memory is then suppressed instead of replayed again.

Memories can be marked `resolved`, `superseded`, `stale`, or `contradicted`. They remain auditable
but are excluded from ordinary recall.

## Default MCP tools

| Tool | Purpose |
|---|---|
| `sync_workspace(query?, known_context_hash?, ...)` | Recover relevant goals, failures, capsules, task state, and Git observations under a token budget. |
| `memory(action, ...)` | List, search, deactivate, forget, or explicitly clear passive memory. |
| `checkpoint(summary, ...)` | Deliberately create or resume one checkout-scoped unit of work. |
| `handoff(task_id, ...)` | Release or complete tracked work with bounded evidence. |
| `resume_delta(correlation_id, ...)` | Compatibility path for integrations that already persist revision IDs. |

Lower-level queue and worker tools remain available through `djobs-mcp-full` rather than occupying
every normal coding context.

## Memory control

Ask the agent naturally:

```text
What does djobs remember about the login bug?
Mark the old OAuth failure as resolved by this commit.
Forget the abandoned Redis experiment.
Clear djobs memory for this repository.
```

Terminal equivalents:

```bash
djobs memory list
djobs memory search "OAuth callback"
djobs memory status MEMORY_ID resolved --resolved-by-commit COMMIT_SHA
djobs memory forget MEMORY_ID
djobs memory clear --yes
```

Privacy controls:

- database: `~/.djobs/global.db` by default;
- common API keys, bearer tokens, passwords, authorization values, and URL credentials are
  redacted on a best-effort basis before storage;
- `[djobs:no-memory]` skips one prompt;
- `DJOBS_CAPTURE_USER_INTENT=0` disables automatic prompt-intent capture;
- stored content is always treated as untrusted data;
- hook, search, and storage failures are fail-open and never block the coding request.

## Supported local hosts

| Host | Prompt-aware memory | Lifecycle observations | Local configuration |
|---|---|---|---|
| GitHub Copilot CLI + VS Code Agent | `UserPromptSubmit` | session, tool, compact, end | `~/.copilot/hooks/djobs.json` |
| Claude Code | `UserPromptSubmit` | session, tool, compact, end | `~/.claude/settings.json` |
| Gemini CLI | `BeforeAgent` | session, tool, compress, end | `~/.gemini/settings.json` |
| Kimi Code | `UserPromptSubmit` | session, tool, compact, end | `~/.kimi-code/config.toml` |
| Codex | query-aware MCP recovery | native session/tool hooks when available | `~/.codex/hooks.json` |

Python **3.10–3.14** is tested in CI on Windows, macOS, and Linux. Node.js **20+** is used only to
build and publish the VS Code extension; end users do not need Node to run the Python MCP server.

## Reproducible recovery benchmarks

Payload proxy:

```bash
python scripts/benchmark_project_memory.py
```

With the bundled synthetic 18-file fixture:

| Recovery strategy | Estimated context | Minimum calls |
|---|---:|---:|
| Re-read every synthetic source file | ~7,805 tokens | 18 file reads |
| Query-aware `sync_workspace` | ~407 tokens | 1 MCP call |

That is a **94.8% recovery-payload proxy reduction** for this fixture. It is not presented as
provider billing, latency, or model-quality measurement.

Recovery-quality checks:

```bash
python scripts/benchmark_resume_quality.py
```

This benchmark verifies cross-worktree recall, checkout ownership isolation, stale-memory
exclusion, and unchanged-context replay suppression.

## Explicit checkpoint demo

The older durable-task flow remains available for work that needs exact checkpoints and handoff:

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

## Public packages and documentation

- **PyPI:** https://pypi.org/project/djobs/
- **VS Code Marketplace:** https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs
- **GitHub Pages:** https://jhuang-tw.github.io/djobs/
- **Source and issues:** https://github.com/jhuang-tw/djobs

## License

[MIT](LICENSE)
