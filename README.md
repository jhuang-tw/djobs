<!-- mcp-name: io.github.jhuang-tw/djobs -->

# djobs — project memory for coding agents

**Stop explaining the same project every time you open a new AI session.**

Yesterday your coding agent read the repository, tried a fix, found why the tests failed, and
changed several files. Today the chat is gone or the context was compacted, so it starts over.

`djobs` keeps a small, local memory for each Git repository so the next session can recover:

- what you asked for and which constraints matter;
- which tools succeeded or failed;
- what changed in the working tree;
- a compact end-of-session capsule with the goal, progress, failures, and next step.

It searches those memories using the current request instead of blindly replaying the latest
chat history. Memory stays in local SQLite. No account, hosted service, or project upload is
required.

## What continuing work looks like

```text
Session 1
You: Fix the OAuth callback loop. Keep the public auth API and preserve '+' in state.
Agent: pytest failed because normalization removed '+'.
Agent: updated src/auth/callback.py; one integration test remains.
[context compacts or the session closes]

Session 2
You: Continue the OAuth fix.
Agent receives from djobs:
- Goal: fix the OAuth callback loop without changing the public auth API
- Failed: state normalization removed '+'
- Progress: callback parser updated; integration test remains
- Current Git changes
```

The recovered text is data, not an instruction. The coding agent still follows your current
request.

## Install once, then open any repository

The easiest route for VS Code / GitHub Copilot users is the
[VS Code extension](https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs).
It registers the local MCP server without adding a sidebar or background polling UI.

For another MCP host, add this server once:

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

After the MCP is present, normal Vibe Coding needs no per-project command and no setup wizard.
The first `djobs` tool call creates `~/.djobs/global.db`, identifies the current Git repository,
and installs only the detected host's passive lifecycle adapter. Opening another repository
uses a different local memory automatically. Sibling Git worktrees share passive project memory,
while explicit task ownership and leases remain isolated to each checkout.

`djobs setup` and `djobs doctor` remain available only for manual repair or diagnostics in
headless environments.

## What is saved automatically

| Memory | Example | Changes task ownership? |
|---|---|---|
| User intent | “Keep Python 3.10 support; do not replace Zustand.” | No |
| Tool result | “edit completed — src/parser.py — fallback parser updated” | No |
| Tool failure | “pytest failed — state normalization removed plus signs” | No |
| Git observation | Actual tracked, staged, and bounded untracked changes | No |
| Session capsule | Goal, recent progress, failures, and next step | No |

Exact duplicate prompts in the same session are ignored. Before context compaction and at real
session end, djobs creates a deterministic capsule without calling an external model or sending
repository content anywhere.

## Relevant and current memory

Agents should call:

```text
sync_workspace(query="the user's current request", context_tier="resume")
```

The query searches repository-family memory with SQLite FTS5 when available and a portable
bounded fallback otherwise. Older relevant constraints and failed approaches can therefore rank
above newer unrelated activity.

Recovery is layered so ordinary sessions do not pay for audit detail they do not need:

- `resume` returns the smallest continuation capsule: goal, constraints, recent progress,
  failures, next step, current tasks, and Git state;
- `evidence` adds compact supporting observations without IDs and timestamps;
- `audit` adds full memory identifiers and timestamps for diagnosis or lifecycle updates.

The MCP defaults to `resume`. Direct Python integrations keep the prior audit-shaped default for
backward compatibility and may opt into a smaller tier explicitly.

The response includes a `context_hash`. A host that persists it can pass
`known_context_hash="..."` on the next equivalent recovery. When the selected passive memory is
unchanged, djobs returns no repeated observations while still returning current task state.

A remembered fact can be marked `resolved`, `superseded`, `stale`, or `contradicted`. It remains
available for audit, but inactive memory is excluded from normal recall and context injection.

You can ask the agent naturally:

```text
What does djobs remember about the OAuth bug?
Mark the old OAuth failure as resolved by this commit.
Forget the memory about the abandoned Redis experiment.
Clear djobs memory for this repository.
```

The MCP exposes a `memory` tool for `list`, `search`, `status`, `forget`, and confirmed `clear`
actions. Clearing passive memory does not delete explicit tracked tasks.

Terminal equivalents are available when useful:

```bash
djobs memory list
djobs memory search "OAuth callback"
djobs memory status MEMORY_ID resolved --resolved-by-commit COMMIT_SHA
djobs memory forget MEMORY_ID
djobs memory clear --yes
```

## Privacy controls

- State is stored locally in `~/.djobs/global.db` by default.
- Common API keys, bearer tokens, passwords, authorization values, and URL credentials are
  redacted on a best-effort basis before storage.
- Put `[djobs:no-memory]` in a prompt to skip that prompt.
- Set `DJOBS_CAPTURE_USER_INTENT=0` to disable automatic prompt-intent memory.
- Use `memory(action="status", ...)` to deactivate an obsolete fact without deleting its audit
  history.
- Use `memory(action="forget", memory_id="...")` to delete one record.
- Use confirmed `memory(action="clear")` to clear passive memory for the repository family.
- Stored content is always treated as untrusted data, never executable instructions.
- Hook, search, or storage failures are fail-open and never block the coding request.

## Recovery benchmarks

The repository includes a deterministic recovery-payload proxy:

```bash
python scripts/benchmark_project_memory.py
```

With its default synthetic 18-file fixture, the current implementation compares:

| Recovery strategy | Estimated context | Minimum calls |
|---|---:|---:|
| Re-read every synthetic source file | ~7,805 tokens | 18 file reads |
| Query-aware `sync_workspace` resume tier | ~224 tokens | 1 MCP call |

That is a **97.1% recovery-payload proxy reduction** for this fixture. It is intentionally not
presented as provider billing, latency, or model-quality measurement. The script is included so
results can be reproduced and challenged instead of treated as a marketing claim.

A second deterministic benchmark checks recovery quality rather than only payload size:

```bash
python scripts/benchmark_resume_quality.py
```

It verifies cross-worktree recall, checkout ownership isolation, stale-memory exclusion, and
unchanged-context replay suppression.

## Measure verified-task efficiency

Token reduction alone can hide repeated repairs. `djobs gain` now reports both savings and
workflow efficiency:

```bash
djobs gain
djobs gain --history
djobs gain --format json
```

The report includes first-pass verified rate, repair attempts, average attempts per verified task,
cycle-time proxy, and estimated context tokens per verified task. These are local workflow
proxies derived from durable task state, not provider billing or exact model-call telemetry.

## Supported local hosts

| Host | Prompt-aware memory | Lifecycle observations | Local configuration |
|---|---|---|---|
| GitHub Copilot CLI + VS Code Agent | `UserPromptSubmit` | session, tool, compact, end | `~/.copilot/hooks/djobs.json` |
| Claude Code | `UserPromptSubmit` | session, tool, compact, end | `~/.claude/settings.json` |
| Gemini CLI | `BeforeAgent` | session, tool, compress, end | `~/.gemini/settings.json` |
| Kimi Code | `UserPromptSubmit` | session, tool, compact, end | `~/.kimi-code/config.toml` |
| Codex | Query-aware MCP recovery | supported native session/tool hooks | `~/.codex/hooks.json` |

Only djobs-managed entries are replaced or removed. Malformed configuration is never
overwritten automatically. An unidentified host still gets repository-scoped MCP memory; djobs
simply avoids guessing which hook file to modify.

Python 3.10+ is supported on Windows, macOS, and Linux.

## Default MCP tools

The normal server deliberately stays small:

| Tool | Purpose |
|---|---|
| `sync_workspace(query?, context_tier="resume", known_context_hash?, ...)` | Recover a layered continuation capsule, compact evidence, or audit detail under a token budget; suppress unchanged memory replay. |
| `memory(action, ...)` | Inspect, search, deactivate, forget, or explicitly clear passive memory for the current repository family. |
| `checkpoint(summary, ...)` | Explicitly create or resume one checkout-scoped unit of work. |
| `handoff(task_id, ...)` | Explicitly release or complete tracked work with bounded evidence. |
| `resume_delta(correlation_id, ...)` | Backward-compatible revision recovery for integrations already storing IDs. |

Lower-level queue and worker tools remain opt-in through `djobs-mcp-full`.

## Advanced: explicit ownership and handoff

Passive memory never creates or claims tasks. Ownership changes only through explicit
`checkpoint` and `handoff` calls:

```text
checkpoint("Implement parser", path="src/parser.py")
  -> this checkout owns one expiring lease

handoff(task_id, "Parser updated; edge tests remain", completed=false)
  -> releases the work with evidence for a later session
```

Automatic adapters may heartbeat a task already owned by the same session, but they never:

- turn every prompt into a task;
- claim pending work at startup;
- infer completion from natural-language output;
- release work when a model turn merely stops;
- overwrite another client's lease.

This explicit layer is optional for people who only need project memory.

## Repository identity and storage

Repository resolution uses MCP roots, host cwd, the enclosing Git root, and finally process cwd.
Windows paths, WSL mounts, and common Git Bash spellings resolve to compatible identities.

Passive memory uses a repository-family identity derived from the normalized Git remote and root
commit, with the Git common directory as a local fallback. This lets sibling worktrees reuse
project decisions and prior failures. Explicit tasks, leases, and Git snapshots remain scoped to
the individual checkout, so parallel lanes do not acquire each other's work.

Default database:

```text
~/.djobs/global.db
```

Override it with `DJOBS_DB`. A repository-specific database is also supported with
`djobs mcp --db .djobs/state.db`; do not commit the database.

Each repository family retains at most 1,000 recent visible observations by default. Git contents
are hashed for change detection and are not stored as observation text.

## Development

```bash
git clone https://github.com/jhuang-tw/djobs.git
cd djobs
python -m venv .venv
# activate the venv
python -m pip install -e ".[dev,pg]"

ruff check src/ tests/
ruff format --check src/ tests/
mypy
pytest -q
python scripts/benchmark_project_memory.py
python scripts/benchmark_resume_quality.py
python -m build
python -m twine check dist/*

cd vscode-ext
npm ci
npx tsc -p ./ --noEmit
npm run compile
```

See `CONTRIBUTING.md` and `AGENTS.md` before changing public behavior.

## License

MIT
