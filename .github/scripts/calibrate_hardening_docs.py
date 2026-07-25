from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected block missing from {path}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, block: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if marker not in text:
        target.write_text(text.rstrip() + "\n\n" + block.strip() + "\n", encoding="utf-8")


replace_once(
    "README.md",
    """A healthy first run does **not** require a project-local `.vscode/mcp.json`. The extension and
`djobs setup` use user-level registration, while the shared local database is created on first use.
""",
    """A healthy first run does **not** require a project-local `.vscode/mcp.json`. `djobs setup`
explicitly configures user-level host adapters. The VS Code extension can register MCP natively and,
on the first MCP call, may create `~/.djobs/global.db` and configure the detected Copilot adapter.
Those are local user-level changes rather than a read-only probe; use `djobs doctor` to inspect them
and `djobs remove copilot` to remove the managed adapter.
""",
)
replace_once(
    "README.md",
    "| `djobs gain` | Inspect recovery savings and verified-task efficiency |",
    "| `djobs gain` | Inspect heuristic recovery and verified-task efficiency estimates |",
)
replace_once(
    "README.md",
    """- Set `DJOBS_CAPTURE_USER_INTENT=0` to disable automatic prompt-intent capture.
- Mark memory `resolved`, `superseded`, `stale`, or `contradicted` when it should stop influencing
  normal recovery.
""",
    """- Set `DJOBS_CAPTURE_USER_INTENT=0` to disable automatic prompt-intent capture.
- Run `djobs pause` to stop automatic prompt/tool capture, session capsules, repository snapshots,
  first-call bootstrap, and `sync_workspace` recovery. Manual memory inspection and deletion remain
  available; pausing deletes nothing.
- Mark memory `resolved`, `superseded`, `stale`, or `contradicted` when it should stop influencing
  normal recovery. Inactive observations remain locally inspectable only while retained by bounded
  storage; djobs is not a permanent audit archive.
""",
)
replace_once(
    "README.md",
    """| Recovery strategy | Estimated context | Minimum calls |
|---|---:|---:|
| Re-read every bundled synthetic source file | ~7,805 tokens | 18 file reads |
| Query-aware `sync_workspace` resume tier | ~224 tokens | 1 MCP call |

The bundled fixture reports a **97.1% recovery-payload proxy reduction**. It is a reproducible
synthetic comparison, not provider billing, latency, or model-quality measurement. The quality
benchmark also checks cross-worktree recall, checkout ownership isolation, stale-memory exclusion,
and unchanged-context replay suppression.

`djobs gain` complements the synthetic benchmark with local workflow proxies:

```bash
djobs gain
djobs gain --history
djobs gain --format json
```

It reports first-pass verified rate, repair attempts, average attempts per verified task, cycle-time
proxy, and estimated context tokens per verified task.
""",
    """| Fixture path | Simple serialized-text estimate | Minimum calls |
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
""",
)

replace_once(
    "vscode-ext/README.md",
    """The first djobs MCP call creates `~/.djobs/global.db` and installs the passive Copilot lifecycle
adapter. There is no required per-project command.

Run **djobs: Set up / Repair djobs** only when Python is missing, an old launch path needs repair,
or diagnostics report a damaged installation.
""",
    """The first djobs MCP call may create `~/.djobs/global.db` and install the passive Copilot
lifecycle adapter. That is a local user-level configuration change, not a read-only probe. No
per-project command is required, but **djobs: Diagnose Setup** shows what was configured and
**djobs: Set up / Repair djobs** performs the same work explicitly when Python or an old launch path
needs attention.
""",
)
replace_once(
    "vscode-ext/README.md",
    "- **djobs: Pause djobs** — temporarily disable djobs operations without deleting state.",
    "- **djobs: Pause djobs** — stop automatic capture and recovery without deleting state; manual inspection and cleanup remain available.",
)
replace_once(
    "vscode-ext/README.md",
    """## Efficiency metrics

The bundled recovery benchmark estimates **~224 tokens** for one `resume` recovery versus
**~7,805 tokens** for rereading the synthetic 18-file fixture, a **97.1% payload proxy reduction**.
This is not provider billing or model-quality measurement.

Reproduce it from the repository with `python scripts/benchmark_project_memory.py`.

`djobs gain` also reports first-pass verified rate, repair attempts, average attempts per verified
task, cycle-time proxy, and estimated context tokens per verified task.
""",
    """## Efficiency metrics

The bundled fixture produces a simple serialized-text estimate of **~224 tokens** for one `resume`
response and **~7,805 tokens** for rereading all 18 synthetic files.

This is a payload-size regression fixture, not an end-to-end savings claim. The reread baseline does
not model modern agents that summarize, cache, or selectively read files, so the figures are not
provider billing, measured model usage, latency, or quality results. Reproduce the fixture with
`python scripts/benchmark_project_memory.py` and inspect its assumptions.

`djobs gain` reports first-pass verified rate, repair attempts, average attempts per verified task,
cycle-time proxy, and simple context-size estimates. Treat them as local heuristics rather than
guaranteed savings.
""",
)
replace_once(
    "vscode-ext/README.md",
    """- Set `DJOBS_CAPTURE_USER_INTENT=0` to disable automatic prompt-intent capture.
- Mark memory resolved, superseded, stale, or contradicted without erasing its audit trail.
- Hook failures are fail-open, unrelated settings are preserved, and djobs does not upload
  repository memory or task state.
""",
    """- Set `DJOBS_CAPTURE_USER_INTENT=0` to disable automatic prompt-intent capture.
- Use **Pause djobs** to stop automatic capture, session capsules, snapshots, bootstrap, and
  `sync_workspace` recovery without deleting stored state.
- Mark memory resolved, superseded, stale, or contradicted so normal recovery excludes it. Local
  observation retention is bounded; this is not a permanent audit archive.
- Hook failures are fail-open, unrelated settings are preserved, and djobs does not upload
  repository memory or task state.
""",
)

replace_once(
    "docs/USER_GUIDE.md",
    """`djobs setup` defaults to Copilot. Pass `codex`, `claude`, `gemini`, `kimi`, or `all` to configure a
different host. The VS Code extension can register the MCP server natively, so a missing
`.vscode/mcp.json` is not an error.
""",
    """`djobs setup` defaults to Copilot. Pass `codex`, `claude`, `gemini`, `kimi`, or `all` to configure a
different host. The VS Code extension can register the MCP server natively, so a missing
`.vscode/mcp.json` is not an error. On its first MCP call the extension may create the shared local
database and configure the detected Copilot adapter; that is a local user-level side effect. Use
`djobs doctor` to inspect it and `djobs remove HOST` to remove a managed adapter.
""",
)
replace_once(
    "docs/USER_GUIDE.md",
    "Inactive memory remains auditable but is excluded from normal recovery.",
    "Inactive memory is excluded from normal recovery and remains inspectable only while retained by bounded local storage. djobs is not a permanent audit archive.",
)
replace_once(
    "docs/USER_GUIDE.md",
    """`clear --yes` removes passive memory for the repository family. Explicit checkpoint tasks are
preserved.

## Choosing a recovery tool
""",
    """`clear --yes` removes passive memory for the repository family. Explicit checkpoint tasks are
preserved.

## Pause automatic behavior

```bash
djobs pause
djobs unpause
```

While paused, automatic prompt and tool capture, repository snapshots, session capsules, first-call
bootstrap, and `sync_workspace` recovery are skipped. Existing data is not deleted. Manual
`memory list`, `search`, lifecycle updates, `forget`, and `clear` remain available so the user can
inspect or remove stored state.

## Choosing a recovery tool
""",
)
replace_once(
    "docs/USER_GUIDE.md",
    """- `reset_required`: the legacy revision cursor cannot be advanced safely; refresh from scratch.

## Troubleshooting
""",
    """- `reset_required`: the legacy revision cursor cannot be advanced safely; refresh from scratch.

## Interpreting benchmarks and `djobs gain`

The bundled benchmark compares one bounded `sync_workspace` response with a deliberately simple
baseline that rereads every file in a synthetic fixture. This is a payload-size regression fixture,
not an end-to-end savings claim. Modern agents can summarize, cache, or selectively read files, so
the fixture must not be presented as provider-token savings, billing reduction, latency, or quality.

`djobs gain` uses configurable characters-per-token and redo-overhead assumptions. Its output is an
explainable local heuristic, not observed provider usage or a guarantee that a model would otherwise
repeat the same work.

## Troubleshooting
""",
)

replace_once(
    "docs/index.html",
    '<p class="lead">The VS Code extension is the easiest route. Other MCP hosts can launch djobs through <code>uvx</code>. Setup and doctor remain repair tools, not a ritual for every project.</p>',
    '<p class="lead">The VS Code extension is the easiest route. Its first MCP call may create the local database and configure the detected Copilot adapter at user level; Diagnose Setup makes that visible. Other MCP hosts can launch djobs through <code>uvx</code>.</p>',
)
replace_once(
    "docs/index.html",
    """        <h2 id="bench-title">Show the payload, not a vague marketing number.</h2>
        <p>The bundled deterministic fixture compares a full synthetic repository reread with one query-aware resume-tier recovery call.</p>
""",
    """        <h2 id="bench-title">Measure payload shape, not a marketing percentage.</h2>
        <p>The bundled deterministic fixture compares a deliberately simple full-reread baseline with one query-aware resume-tier response.</p>
""",
)
replace_once(
    "docs/index.html",
    """        <article class="metric"><strong>~7,805</strong><span>estimated tokens to reread the synthetic 18-file fixture</span></article>
        <article class="metric"><strong>~224</strong><span>estimated tokens for query-aware <code>sync_workspace</code> resume tier</span></article>
      </div>
      <p style="color:var(--muted);margin-top:18px">A 97.1% recovery-payload proxy reduction for this fixture. This is not provider billing, latency, or model-quality measurement. Run <code>python scripts/benchmark_project_memory.py</code> and inspect it yourself.</p>
      <div class="gain-note"><strong>djobs gain</strong> now also reports first-pass verified rate, repair attempts, average attempts per verified task, cycle-time proxy, and estimated context tokens per verified task.</div>
""",
    """        <article class="metric"><strong>~7,805</strong><span>simple estimate for rereading all 18 synthetic files</span></article>
        <article class="metric"><strong>~224</strong><span>simple estimate for one query-aware <code>sync_workspace</code> resume response</span></article>
      </div>
      <p style="color:var(--muted);margin-top:18px">This is a payload-size regression fixture, not an end-to-end savings claim. The baseline does not model modern agents that summarize, cache, or selectively read files. It is not provider billing, measured model usage, latency, or quality. Run <code>python scripts/benchmark_project_memory.py</code> and inspect the assumptions.</p>
      <div class="gain-note"><strong>djobs gain</strong> reports local task-efficiency and context-size heuristics. Treat them as explainable estimates, not guaranteed savings.</div>
""",
)
replace_once(
    "docs/index.html",
    '<p>State defaults to <code>~/.djobs/global.db</code>. Common credentials are redacted on a best-effort basis, stored text is untrusted data, and failures remain fail-open.</p>',
    '<p>State defaults to <code>~/.djobs/global.db</code>. Pause stops automatic capture and recovery without deleting data. Common credentials are redacted on a best-effort basis, stored text is untrusted data, retention is bounded, and failures remain fail-open.</p>',
)
replace_once(
    "docs/index.html",
    '<article class="card"><span class="card-icon" aria-hidden="true">02</span><h3>Resolve without erasing</h3><p>Mark memory resolved, superseded, stale, or contradicted while keeping its audit trail.</p></article>',
    '<article class="card"><span class="card-icon" aria-hidden="true">02</span><h3>Retire without selecting</h3><p>Mark memory resolved, superseded, stale, or contradicted. It remains inspectable only while retained by bounded local storage.</p></article>',
)

replace_once(
    "CONTRIBUTING.md",
    """Ordinary pull requests run one fast required `lint` check containing the complete
quick preflight. The legacy required check names remain present but their expensive
jobs are skipped for ordinary PRs.

The full compatibility matrix runs for:

- every commit that reaches `main`;
- `workflow_dispatch` verification;
- automated `automation/release-vX.Y.Z` pull requests.

Full CI includes Python 3.10–3.14, PostgreSQL, package and Twine validation, VS Code
compilation, and clean-wheel installation on Windows, macOS, and Linux. New pushes to
the same PR cancel obsolete runs instead of waiting for every stale commit.
""",
    """Every pull request runs the real compatibility gates before merge. The shared `lint` job performs
formatting, lint, and type checks, while separate jobs cover Python 3.10–3.14,
PostgreSQL, package and Twine validation, VS Code compilation, and clean-wheel
installation on Windows, macOS, and Linux. Required compatibility job names must not
be no-op placeholders.

The same matrix also runs on `main`, `workflow_dispatch`, and automated
`automation/release-vX.Y.Z` pull requests. New pushes to the same branch cancel
obsolete runs instead of waiting for every stale commit.
""",
)
replace_once(
    "CONTRIBUTING.md",
    """The release commit changes only generated version surfaces already validated in the
release PR, so it is not followed by a duplicate full main matrix.
""",
    """The release PR validates the generated version surfaces before merge. A normal `main` CI run may
also revalidate the squash result; publishing rebuilds artifacts from the exact merged SHA.
""",
)

replace_once(
    "AGENTS.md",
    """Ordinary PRs run the quick preflight once. Full Python, PostgreSQL, package, extension,
and three-OS installation validation runs on `main` and automated release PRs. A
change is complete only after relevant tests and CI are green, current Markdown and
published metadata match the code, and temporary migration or validation files are
removed.
""",
    """Every pull request runs the real Python, PostgreSQL, package, extension, and three-OS
installation gates before merge; required job names must not be no-op placeholders.
A change is complete only after those checks are green, current Markdown and published
metadata match the code, benchmark copy states its assumptions, and temporary migration
or validation files are removed.
""",
)

replace_once(
    "docs/RELEASE.md",
    """1. the human PR runs the quick shared preflight;
2. after merge, `main` runs the full compatibility matrix;
""",
    """1. the human PR runs the full compatibility matrix before merge;
2. after merge, `main` revalidates the selected source commit;
""",
)
replace_once(
    "docs/RELEASE.md",
    """CI uses the non-mutating `--check` form. Ordinary human PRs run one quick required
preflight. The full matrix is reserved for `main`, manual verification, and automated
release PRs. A newer push cancels an obsolete CI run for the same PR.
""",
    """CI uses the non-mutating `--check` form. Every pull request runs the real compatibility
matrix before merge; required check names must never be satisfied by no-op placeholder
jobs. The same matrix runs for `main`, manual verification, and automated release PRs.
A newer push cancels an obsolete CI run for the same branch.
""",
)
replace_once(
    "docs/RELEASE.md",
    """The merged release commit differs from the checked PR only by the squash commit
identity, so the workflow does not dispatch a duplicate full matrix after merging.
The package and extension publishing jobs still rebuild from the exact merged SHA.
""",
    """The merged release commit differs from the checked PR only by the squash commit identity.
A normal `main` CI run may revalidate that result, while the package and extension
publishing jobs rebuild from the exact merged SHA.
""",
)

replace_once(
    "CHANGELOG.md",
    "## [Unreleased]\n",
    """## [Unreleased]

### Fixed
- `[core]` Pause now suppresses automatic capture, snapshots, session capsules, first-call bootstrap, and workspace recovery without blocking manual inspection or deletion.
- `[core]` Recovery reads prior memory before persisting the current prompt, preventing the current request from ranking as its own history.
- `[core]` Bounded session-capsule storage preserves structured goal, progress, failure, next-step, and source fields.

### Changed
- `[release]` Pull requests run real compatibility, package, extension, database, and installed-wheel gates before merge instead of no-op compatibility placeholders.
- `[docs]` Public benchmark copy now describes a synthetic payload-size regression fixture and its assumptions instead of claiming a fixed percentage of provider-token savings.
""",
)

replace_once(
    "scripts/benchmark_project_memory.py",
    """This benchmark does not claim provider billing or model-quality results. It
compares a conservative "re-read every synthetic source file" recovery payload
with one query-aware ``sync_workspace`` response for the same repository.
""",
    """This benchmark is a serialized payload-size regression fixture. It does not
claim provider billing, end-to-end savings, latency, or model-quality results.
Modern agents may summarize, cache, or selectively read files instead of replaying
every source file, so the full-reread baseline is deliberately simple.
""",
)
replace_once(
    "scripts/benchmark_project_memory.py",
    '                "benchmark": "deterministic recovery-payload proxy",\n                "disclaimer": "Not provider billing, latency, or model-quality measurement.",\n',
    '                "benchmark": "deterministic payload-size regression fixture",\n                "disclaimer": (\n                    "Not provider billing, end-to-end savings, latency, or model-quality "\n                    "measurement; modern agents may summarize, cache, or selectively read."\n                ),\n',
)
replace_once(
    "scripts/benchmark_project_memory.py",
    '                "proxy_reduction_percent": round(reduction * 100, 1),\n',
    '                "serialized_payload_ratio": round(memory_tokens / baseline_tokens, 4)\n                if baseline_tokens\n                else None,\n                "interpretation": (\n                    "Compare raw fixture payloads across releases; do not convert this ratio "\n                    "into a provider-token or product-quality claim."\n                ),\n',
)
replace_once(
    "scripts/benchmark_project_memory.py",
    '    print(f"Recovery-payload proxy reduction: {result[\'proxy_reduction_percent\']}%")\n',
    '    print("Interpretation: compare raw fixture payloads; no end-to-end savings percentage is claimed.")\n',
)

replace_once(
    "src/djobs/gain.py",
    '"""Explainable token-savings analytics for durable djobs state."""',
    '"""Explainable heuristic context comparisons for durable djobs state."""',
)
replace_once(
    "src/djobs/gain.py",
    '    """Build an explainable savings report without mutating durable state."""',
    '    """Build an explainable heuristic comparison without mutating durable state."""',
)
replace_once(
    "src/djobs/gain.py",
    '        "estimate_kind": "avoided replay/re-read/re-plan tokens",\n',
    '        "estimate_kind": "simple replay baseline versus stored durable context",\n',
)
replace_once(
    "src/djobs/gain.py",
    '                "Estimates compare replaying completed work after context loss with "\n                "the compact durable state djobs can restore. They are not provider billing data."\n',
    '                "Estimates compare a configurable replay baseline with compact durable state. "\n                "Modern agents may summarize, cache, or selectively reread, so the baseline can "\n                "overstate avoided work. This is not observed provider usage or billing data."\n',
)
replace_once(
    "src/djobs/gain.py",
    '        f"  {label:<9} {_format_tokens(data[\'estimated_saved_tokens\']):>10} tokens saved  "\n        f"({data[\'completed_records\']} completed, {data[\'estimated_saved_percent\']:.1f}%)"\n',
    '        f"  {label:<9} {_format_tokens(data[\'estimated_saved_tokens\']):>10} heuristic delta  "\n        f"({data[\'completed_records\']} completed records)"\n',
)
replace_once(
    "src/djobs/gain.py",
    '    print("djobs gain - estimated durable-context savings")\n',
    '    print("djobs gain - heuristic durable-context comparison")\n',
)
replace_once(
    "src/djobs/gain.py",
    '    print("\\nSavings sources")\n',
    '    print("\\nEstimated delta sources")\n',
)
replace_once(
    "src/djobs/gain.py",
    '    print("\\n  Estimate only: avoided replay/re-read/re-plan, not provider billing data.")\n',
    '    print(\n        "\\n  Heuristic only: modern agents may summarize or selectively reread; "\n        "not observed provider usage or guaranteed savings."\n    )\n',
)
replace_once(
    "src/djobs/gain.py",
    '        description="Show explainable token savings from durable djobs state",\n',
    '        description="Show explainable local context heuristics from durable djobs state",\n',
)

release_test = Path("tests/unit/test_release_surfaces.py")
release_text = release_test.read_text(encoding="utf-8")
release_text = release_text.replace(
    '        "docs/RELEASE.md",\n',
    '        "docs/RELEASE.md",\n        "docs/USER_GUIDE.md",\n        "examples/README.md",\n',
    1,
)
if '        "97.1%",\n' not in release_text:
    raise RuntimeError("expected benchmark percentage marker missing from release-surface test")
release_text = release_text.replace(
    '        "97.1%",\n',
    '        "not an end-to-end",\n',
    1,
)
release_test.write_text(release_text, encoding="utf-8")

replace_once(
    "tests/unit/test_project_memory_benchmark.py",
    '    assert result["benchmark"] == "deterministic recovery-payload proxy"\n    assert "Not provider billing" in result["disclaimer"]\n',
    '    assert result["benchmark"] == "deterministic payload-size regression fixture"\n    assert "Not provider billing" in result["disclaimer"]\n    assert "modern agents" in result["disclaimer"]\n    assert "proxy_reduction_percent" not in result\n    assert result["serialized_payload_ratio"] < 1\n',
)
replace_once(
    "tests/unit/test_gain.py",
    '    assert "provider billing data" in result["assumptions"]["note"]\n',
    '    assert "provider usage or billing data" in result["assumptions"]["note"]\n    assert "Modern agents" in result["assumptions"]["note"]\n',
)

append_once(
    "tests/unit/test_release_surfaces.py",
    "def test_public_benchmark_copy_is_cautious_and_consistent",
    r'''
def test_public_benchmark_copy_is_cautious_and_consistent() -> None:
    paths = ("README.md", "vscode-ext/README.md", "docs/index.html")
    for path in paths:
        text = (ROOT / path).read_text(encoding="utf-8").lower()
        assert "payload-size regression fixture" in text
        assert "not an end-to-end savings claim" in text
        assert "modern agents" in text
        assert "97.1%" not in text
        assert "payload proxy reduction" not in text

    benchmark = (ROOT / "scripts/benchmark_project_memory.py").read_text(encoding="utf-8")
    assert "proxy_reduction_percent" not in benchmark
    assert "no end-to-end savings percentage is claimed" in benchmark

    guide = (ROOT / "docs/USER_GUIDE.md").read_text(encoding="utf-8").lower()
    assert "bounded local storage" in guide
    assert "not a permanent audit archive" in guide
    assert "while paused" in guide
    assert "manual" in guide


def test_markdown_matches_real_pull_request_gates() -> None:
    paths = ("CONTRIBUTING.md", "AGENTS.md", "docs/RELEASE.md")
    for path in paths:
        text = (ROOT / path).read_text(encoding="utf-8").lower()
        assert "every pull request" in text
        assert "no-op placeholder" in text
        assert "ordinary pull requests run one fast" not in text
        assert "ordinary prs run the quick" not in text
''',
)
