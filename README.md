<!-- mcp-name: io.github.jhuang-tw/djobs -->

<p align="center">
  <img src="https://raw.githubusercontent.com/jhuang-tw/djobs/main/vscode-ext/media/icon-128.png" width="88" alt="djobs logo">
</p>

<h1 align="center">djobs</h1>

<p align="center">
  <strong>Shared local project memory for AI coding agents.</strong><br>
  Switch agents or sessions without re-explaining the repository — and coordinate ownership when multiple agents work at once.
</p>

<p align="center">
  <a href="https://github.com/jhuang-tw/djobs/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/jhuang-tw/djobs/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pypi.org/project/djobs/"><img alt="PyPI" src="https://img.shields.io/pypi/v/djobs.svg"></a>
  <a href="https://pypi.org/project/djobs/"><img alt="Python 3.10–3.14" src="https://img.shields.io/badge/Python-3.10%E2%80%933.14-3776AB?logo=python&logoColor=white"></a>
  <a href="https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs"><img alt="VS Code Marketplace" src="https://img.shields.io/visual-studio-marketplace/v/jhuang-tw.djobs?label=VS%20Code"></a>
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/License-MIT-blue.svg"></a>
</p>

## Why djobs

Coding agents are good at the current turn and bad at carrying project state across boundaries.
A new chat, a different model, a different IDE host, or a second agent often means repeating the same
constraints, rediscovering the same failure, and re-reading the same files.

`djobs` gives the **repository** the memory instead of giving that memory to one chat window.

```text
Copilot ─┐
Claude  ─┤
Codex   ─┼──> djobs local repository memory ──> same project context
Gemini  ─┤
Kimi    ─┘
```

When parallel work genuinely needs ownership, the same local layer also provides explicit
checkpoint / lease / handoff state so two agents do not silently duplicate the same bounded task.

**djobs is not an orchestrator.** It does not spawn agents, choose a planner, or take authority away
from the current model. It remembers repository state and exposes explicit coordination primitives;
the current user request and current agent remain in control.

## See what it knows before trusting it

Install once:

```bash
pipx install djobs
# or: uv tool install djobs

djobs setup          # defaults to Copilot; add codex/claude/gemini/kimi/all as needed
djobs doctor
```

Then preview exactly what djobs can recover for the next coding session:

```bash
djobs context "Continue the OAuth callback fix"
```

Example:

```text
djobs context — oauth-service
Goal: Fix the OAuth callback loop without changing the public API.
Constraints:
  - Keep Python 3.10 support.
Progress:
  - Parser updated; focused unit tests pass.
Failures to avoid repeating:
  - Normalization removed '+' from the state parameter.
Next: Run the callback integration test.
Explicit work ownership:
  - callback integration test [running; codex]
```

Use `--json` when another tool should consume the preview, and `--tier evidence` or `--tier audit`
when you need progressively deeper provenance.

A healthy first run does **not** require a project-local `.vscode/mcp.json`. `djobs setup`
configures user-level host adapters. The VS Code extension can register MCP natively and,
on the first MCP call, may create `~/.djobs/global.db` and configure the detected Copilot adapter.
Those are local user-level changes rather than a read-only probe; use `djobs doctor` to inspect them
and `djobs remove copilot` to remove the managed adapter.

## What gets remembered

A normal coding session can record bounded local observations such as:

| Memory type | Example |
|---|---|
| User intent | “Keep Python 3.10 support.” |
| Tool result | “Updated `src/parser.py`; focused tests passed.” |
| Tool failure | “State normalization removed plus signs.” |
| Repository change | Tracked, staged, and bounded untracked Git changes |
| Session capsule | Goal, constraints, progress, failures, and next step |

The next session can call:

```text
sync_workspace(query="Continue the OAuth callback fix")
```

and receive a compact continuation capsule containing the goal, constraints, progress, failures,
next step, current Git state, and explicit task ownership. Stored text is returned as untrusted data;
the current user request always remains authoritative.

## Five MCP tools, with clear roles

| Tool | Call it when |
|---|---|
| `sync_workspace` | Starting or continuing repository work. This is the default recovery entry point. |
| `memory` | Inspecting, searching, retiring, forgetting, or clearing passive memory. |
| `checkpoint` | Deliberately claiming one bounded unit so another agent does not duplicate it. |
| `handoff` | Releasing or completing a claimed unit with bounded evidence. |
| `resume_delta` | Maintaining an older integration that already stores correlation IDs and revisions. |

### Choosing a recovery detail level

Start with `sync_workspace(..., context_tier="resume")`:

| Tier | Use it for | Returned detail |
|---|---|---|
| `resume` | Normal continuation | Goal, constraints, progress, failures, next step, tasks, and Git state |
| `evidence` | Checking why a conclusion was selected | Resume capsule plus compact supporting observations |
| `audit` | Lifecycle changes or diagnosis | Full memory IDs, timestamps, scores, and audit fields |

Persist the returned `context_hash` and pass it back as `known_context_hash` on an equivalent later
recovery. If passive memory did not change, djobs suppresses repeated observations while still
returning current task state.

## Explicit ownership only when needed

Passive memory never silently claims a task. Use explicit ownership only when coordinated work would
otherwise collide:

```text
checkpoint("Implement parser", path="src/parser.py")
  -> this checkout owns one expiring lease

handoff(task_id, "Parser updated; edge tests remain", completed=false)
  -> releases the task with bounded evidence
```

Sibling Git worktrees share passive repository memory; explicit leases remain checkout-scoped.
Do not create a checkpoint for every prompt. Passive observations are enough for ordinary continuation.

## CLI

The normal CLI is intentionally focused on inspectability and setup:

| Command | Use it for |
|---|---|
| `djobs setup [host]` | Configure MCP and passive lifecycle capture for one supported host |
| `djobs doctor` | Check local storage and integrations, with a concrete next action |
| `djobs context [query]` | Preview the bounded context a coding agent can recover |
| `djobs memory list` | See what the current repository remembers |
| `djobs memory search "query"` | Find a prior goal, failure, decision, or result |
| `djobs memory status ID stale` | Retire outdated memory without erasing its audit trail |
| `djobs memory forget ID` | Delete one memory item |
| `djobs memory clear --yes` | Clear passive memory for this repository family |
| `djobs gain` | Inspect heuristic recovery and verified-task efficiency estimates |
| `djobs pause` / `djobs unpause` | Temporarily disable or resume automatic behavior |
| `djobs receipt` | Show an evidence-backed work summary |

The original durable job queue engine is retained for compatibility, but it no longer dominates the
first-run experience. Its operational commands are available through `djobs legacy --help`.
Direct historical invocations still work for scripts and hooks, with a compatibility notice.

## Install and host support

### VS Code / GitHub Copilot

Install the **[djobs VS Code extension](https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs)**.
It registers the compact MCP server and exposes setup, diagnostics, pause, and resume without a
permanent sidebar, hosted account, polling service, or cloud database.

### CLI-managed hosts

```bash
djobs setup copilot
djobs setup codex
djobs setup claude
djobs setup gemini
djobs setup kimi
# or configure all detected hosts:
djobs setup all
```

Repair and removal use the same vocabulary:

```bash
djobs repair codex
djobs remove claude
```

### Generic MCP host

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

## Storage, privacy, and deletion

- State defaults to `~/.djobs/global.db` and stays on the local machine.
- No hosted account, vector database, remote queue, or repository upload is required.
- Sibling Git worktrees share passive repository memory; explicit leases remain checkout-scoped.
- Common API keys, bearer tokens, passwords, authorization values, and URL credentials are redacted
  on a best-effort basis before storage.
- Add `[djobs:no-memory]` to skip one prompt.
- Set `DJOBS_CAPTURE_USER_INTENT=0` to disable automatic prompt-intent capture.
- Run `djobs pause` to stop automatic prompt/tool capture, session capsules, repository snapshots,
  first-call bootstrap, and `sync_workspace` recovery. Manual memory inspection and deletion remain
  available; pausing deletes nothing.
- Mark memory `resolved`, `superseded`, `stale`, or `contradicted` when it should stop influencing
  normal recovery. Inactive observations remain locally inspectable only while retained by bounded
  storage; djobs is not a permanent audit archive.
- Use `forget` for one item or `clear --yes` for passive memory in the repository family. Explicit
  checkpoint tasks are preserved by memory clear.
- Hook, search, and storage failures are fail-open and must not block the coding request.

See [docs/USER_GUIDE.md](docs/USER_GUIDE.md) for troubleshooting, field meanings, and cleanup examples.

## Benchmarks

```bash
python scripts/benchmark_project_memory.py
python scripts/benchmark_resume_quality.py
```

| Fixture path | Simple serialized-text estimate | Minimum calls |
|---|---:|---:|
| Re-read every bundled synthetic source file | ~7,805 tokens | 18 file reads |
| Query-aware `sync_workspace` resume tier | ~224 tokens | 1 MCP call |

This is a payload-size regression fixture, not an end-to-end savings claim. Its reread baseline
assumes every bundled source file is sent again and does not model modern agents that summarize,
cache, or selectively read files. Do not interpret the comparison as provider-token savings,
billing reduction, latency improvement, or model-quality improvement. Use it to detect changes in
djobs payload shape and inspect the benchmark methodology yourself. The companion quality fixture
checks cross-worktree recall, checkout ownership isolation, stale-memory exclusion, and
unchanged-context replay suppression.

`djobs gain` complements the fixture with local workflow heuristics:

```bash
djobs gain
djobs gain --history
djobs gain --format json
```

It reports first-pass verified rate, repair attempts, average attempts per verified task, cycle-time
proxy, and simple context-size estimates. These are explainable local estimates, not observed model
usage or guaranteed savings.

## Requirements

| Component | Requirement |
|---|---|
| Python runtime | Python 3.10+; Python 3.10–3.14 tested in CI |
| VS Code extension | VS Code 1.101 or newer |
| Storage | Local SQLite by default |
| Operating systems | Windows, macOS, Linux |

Node.js is only required to develop or package the extension.

## Development

```bash
git clone https://github.com/jhuang-tw/djobs.git
cd djobs
python -m venv .venv
# activate the environment
python -m pip install -e ".[dev,pg]"
pre-commit install

python scripts/preflight.py --profile quick --fix --base-ref origin/main
python scripts/preflight.py --profile full --check --base-ref origin/main
```

The top-level product is shared local project memory and explicit agent coordination. Queue, worker,
scheduler, and dashboard modules are compatibility subsystems; new examples and user documentation
should not present them as the default experience. See `CONTRIBUTING.md`, `AGENTS.md`,
`examples/README.md`, and `docs/RELEASE.md` before changing public behavior.

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
