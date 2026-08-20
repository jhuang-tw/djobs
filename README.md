<!-- mcp-name: io.github.jhuang-tw/djobs -->

<p align="center">
  <img src="https://raw.githubusercontent.com/jhuang-tw/djobs/main/vscode-ext/media/icon-128.png" width="88" alt="djobs logo">
</p>

<h1 align="center">djobs</h1>

<p align="center">
  <strong>Remember the project. Coordinate the work. Finish with evidence.</strong><br>
  Local project memory and cross-agent coordination for AI coding agents, with optional ARUN Project Mode for durable goals and verified completion.
</p>

<p align="center">
  <a href="https://github.com/jhuang-tw/djobs/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/jhuang-tw/djobs/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pypi.org/project/djobs/"><img alt="PyPI" src="https://img.shields.io/pypi/v/djobs.svg"></a>
  <a href="https://pypi.org/project/djobs/"><img alt="Python 3.10–3.14" src="https://img.shields.io/badge/Python-3.10%E2%80%933.14-3776AB?logo=python&logoColor=white"></a>
  <a href="https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs"><img alt="VS Code Marketplace" src="https://img.shields.io/visual-studio-marketplace/v/jhuang-tw.djobs?label=VS%20Code"></a>
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/License-MIT-blue.svg"></a>
</p>

## Why djobs

AI coding agents usually lose the most useful state at session boundaries: the user's real constraint,
the failed approach that should not be repeated, the files already changed, the remaining test, and
who currently owns a piece of work.

`djobs` keeps that state attached to the repository instead of one chat window.

| Layer | What it answers | djobs capability |
|---|---|---|
| Memory | **What already happened?** | Local SQLite memory, query-aware recall, session capsules, Git state |
| Coordination | **Who owns this work?** | Explicit checkpoint/handoff, leases, duplicate-work avoidance |
| Completion | **Is the project actually done?** | Optional ARUN Project Mode for durable goals, recovery, scope and evidence-gated completion |

The default product stays lightweight and local. ARUN is optional and is never required for normal
memory or coordination.

## Start in 60 seconds

Install the VS Code extension, or install the CLI once:

```bash
pipx install djobs
# or: uv tool install djobs

djobs setup          # defaults to Copilot; codex/claude/gemini/kimi/all are supported
djobs doctor
djobs memory list
```

A healthy first run does **not** require a project-local `.vscode/mcp.json`. `djobs setup` configures
user-level host adapters. The VS Code extension can register the MCP server natively.

After a configured coding session, memory can contain bounded observations such as:

```json
{
  "event": "tool_failure",
  "summary": "Normalization removed '+' from the OAuth state parameter.",
  "status": "active"
}
```

The next session calls:

```text
sync_workspace(query="Continue the OAuth callback fix")
```

and receives a compact continuation capsule containing the goal, constraints, progress, failures,
next action, task ownership, and current Git state. Stored text is always returned as untrusted data;
the current user request remains authoritative.

## Five focused MCP tools

The normal MCP surface stays small:

| Tool | Use it for |
|---|---|
| `sync_workspace` | Start or continue repository work with query-aware recovery |
| `memory` | Inspect, search, retire, forget, or clear passive memory |
| `checkpoint` | Explicitly claim one bounded unit of work |
| `handoff` | Release or complete claimed work with evidence |
| `resume_delta` | Compatibility recovery for integrations that already persist queue revisions |

Passive capture never silently becomes task ownership. Use checkpoint/handoff only when coordination
actually needs an explicit lease.

### Query-aware local recall

Memory search uses SQLite FTS5/BM25 ranking. No hosted account, remote vector database, or repository
upload is required.

Start with the smallest recovery tier:

```text
sync_workspace(query=current_request, context_tier="resume")
```

| Tier | Use it for | Returned detail |
|---|---|---|
| `resume` | Normal continuation | Goal, constraints, progress, failures, next action, tasks, Git state |
| `evidence` | Inspect why context was selected | Resume capsule plus supporting observations |
| `audit` | Lifecycle/debugging work | IDs, timestamps, scores, and audit fields |

Persist the returned `context_hash` and pass it back as `known_context_hash` to suppress unchanged
context replay.

## Cross-agent coordination

When two agents could duplicate work, claim only the bounded unit that needs ownership:

```text
checkpoint("Implement OAuth callback parser", path="src/oauth/callback.py")
  -> this checkout owns one expiring lease

handoff(task_id, "Parser fixed; integration test remains", completed=false)
  -> releases the unit with bounded continuation evidence
```

Sibling Git worktrees share passive repository memory while explicit leases remain checkout-scoped.
Another agent can see that work is occupied without silently stealing it.

## Optional ARUN Project Mode

Memory and handoff are useful, but long engineering tasks also need durable completion semantics:
what is the active goal, what failed, what evidence is required, whether scope drifted, and whether
acceptance is actually satisfied.

If ARUN is installed, djobs exposes it as an **optional higher-level project mode** without copying
ARUN's state machine into djobs:

```bash
djobs project doctor

djobs project init \
  --objective "Fix the OAuth callback loop" \
  --constraint "Do not change the public API" \
  --acceptance "Focused callback tests pass" \
  --acceptance "No unrelated files change"

djobs project status
djobs project next
```

The boundary is deliberate:

```text
current reasoning model
        |
        +-- djobs: memory, recall, checkpoint, handoff, leases
        |
        +-- ARUN: durable goal, failure/recovery, scope, evidence, verification
```

`djobs project next` only creates/resumes the next ARUN external-control packet. It does **not** start
another LLM or executor and does not auto-run tools. ARUN remains the authoritative project state
engine; the current agent/executor performs real work and supplies evidence through ARUN's control
contract.

If ARUN is absent, normal djobs behavior is unchanged. See
[`docs/PROJECT_MODE.md`](docs/PROJECT_MODE.md) for the exact authority and safety boundary.

## CLI

| Command | Use it for |
|---|---|
| `djobs setup [host]` | Configure MCP and passive lifecycle capture |
| `djobs doctor` | Check local storage and host integrations |
| `djobs memory list` | See what this repository remembers |
| `djobs memory search "query"` | Find an earlier goal, failure, decision, or result |
| `djobs memory status ID stale` | Retire outdated memory without deleting its audit trail |
| `djobs gain` | Inspect local recovery and verified-task efficiency heuristics |
| `djobs pause` / `djobs unpause` | Disable or resume automatic capture/recovery |
| `djobs receipt` | Show an evidence-backed work summary |
| `djobs project ...` | Optional ARUN durable project mode |
| `djobs legacy ...` | Compatibility CLI for the original durable queue engine |

The original queue, worker, scheduler, dashboard, and audit machinery remains available for existing
integrations, but it no longer dominates first-run onboarding.

## Host support

### VS Code / GitHub Copilot

Install the **[djobs VS Code extension](https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs)**.
It registers the compact MCP server and exposes setup, diagnostics, pause, and resume without a hosted
account or permanent cloud service.

### CLI-managed hosts

```bash
djobs setup copilot
djobs setup codex
djobs setup claude
djobs setup gemini
djobs setup kimi
# configure every detected host:
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
- Common API keys, bearer tokens, passwords, authorization values, and URL credentials are redacted
  on a best-effort basis before storage.
- Add `[djobs:no-memory]` to skip one prompt.
- Set `DJOBS_CAPTURE_USER_INTENT=0` to disable automatic prompt-intent capture.
- `djobs pause` stops automatic prompt/tool capture, session capsules, repository snapshots, first-call
  bootstrap, and `sync_workspace` recovery without deleting stored data.
- Mark memory `resolved`, `superseded`, `stale`, or `contradicted` when it should stop influencing
  normal recovery.
- Use `forget` for one item or `clear --yes` for passive memory in the repository family. Explicit
  checkpoint tasks are preserved by memory clear.
- Hook, search, and storage failures are fail-open and must not block the coding request.

See [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) for troubleshooting and memory lifecycle details.

## Benchmarks

```bash
python scripts/benchmark_project_memory.py
python scripts/benchmark_resume_quality.py
```

| Fixture path | Simple serialized-text estimate | Minimum calls |
|---|---:|---:|
| Re-read every bundled synthetic source file | ~7,805 tokens | 18 file reads |
| Query-aware `sync_workspace` resume tier | ~224 tokens | 1 MCP call |

This is a payload-size regression fixture, not an end-to-end provider-token or billing claim. Modern
agents can summarize, cache, or selectively read files. The quality fixture separately checks
cross-worktree recall, ownership isolation, stale-memory exclusion, and unchanged-context suppression.

`djobs gain` reports explainable local heuristics such as first-pass verified rate, repair attempts,
average attempts per verified task, cycle-time proxy, and context-size estimates. They are not
provider telemetry or guaranteed savings.

## Requirements

| Component | Requirement |
|---|---|
| Python runtime | Python 3.10+; Python 3.10–3.14 tested in CI |
| VS Code extension | VS Code 1.101 or newer |
| Storage | Local SQLite by default |
| Operating systems | Windows, macOS, Linux |
| ARUN Project Mode | Optional `arun` executable; normal djobs does not require it |

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

New product work should preserve the layer boundary: djobs owns memory/coordination; ARUN Project Mode
is optional and ARUN remains authoritative for durable completion state. Do not duplicate either
state machine into the other project.

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
