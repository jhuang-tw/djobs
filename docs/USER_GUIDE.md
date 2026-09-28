# djobs user guide

This guide covers the normal product: local repository memory for AI coding agents. The original
durable queue engine is a compatibility subsystem and is documented through `djobs legacy --help`.

## First run

```bash
pipx install djobs
# or: uv tool install djobs

djobs setup
djobs doctor
djobs memory list
```

`djobs setup` defaults to Copilot. Pass `codex`, `claude`, `gemini`, `kimi`, or `all` to configure a
different host. The VS Code extension can register the MCP server natively, so a missing
`.vscode/mcp.json` is not an error. On its first synchronizing MCP call the extension may create the shared local
database and configure the detected Copilot adapter; that is a local user-level side effect.
Read-only `memory list`, `search`, and `trace` do not perform this bootstrap. Use
`djobs doctor` to inspect it and `djobs remove HOST` to remove a managed adapter.

## What is stored

Passive memory contains bounded observations, not a replay of the entire chat:

- `user_intent`: goals and constraints from the user;
- `tool_result`: successful tool outcomes;
- `tool_failure`: failed attempts worth avoiding;
- `repository_change`: bounded Git working-tree state;
- `session_capsule`: goal, progress, failures, and next step.

The default database is `~/.djobs/global.db`. Repository identity scopes retrieval so unrelated
projects do not share memory. Sibling worktrees share passive repository memory, while explicit task
leases remain checkout-specific.

## Inspect memory

```bash
djobs memory list
djobs memory search "OAuth callback"
```

Important fields:

| Field | Meaning |
|---|---|
| `id` | Memory identifier used by lifecycle and deletion commands |
| `event` | Observation type such as `user_intent` or `tool_failure` |
| `summary` | Bounded stored text; always treat it as untrusted data |
| `status` | `active`, `resolved`, `superseded`, `stale`, or `contradicted` |
| `score` | Query relevance proxy in search/evidence output, not a truth score |
| `commit_sha` | Commit associated with a memory when one was captured |

## Retire outdated memory

Prefer lifecycle updates over deletion:

```bash
djobs memory status MEMORY_ID resolved --resolved-by-commit COMMIT_SHA
djobs memory status OLD_ID superseded --replacement-id NEW_ID
djobs memory status MEMORY_ID stale
```

Inactive memory is excluded from normal recovery and remains inspectable only while retained by bounded local storage. djobs is not a permanent audit archive.

Delete only when requested:

```bash
djobs memory forget MEMORY_ID
djobs memory clear --yes
```

`clear --yes` removes passive memory for the repository family. Explicit checkpoint tasks are
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

Use `sync_workspace(query=current_request)` for normal continuation. It is the primary entry point.

- `resume`: smallest continuation payload; includes compact sources supporting the summary.
- `evidence`: adds selected observation summaries and relevance scores.
- `audit`: includes identifiers, timestamps, and full lifecycle detail.

Use `memory` when inspecting or changing passive memory. Use `checkpoint` and `handoff` only when
multiple agents require explicit ownership. Use `resume_delta` only for an older integration that
already persists correlation IDs and revisions.

### Response conventions

- `ok`: primary success flag.
- `continue_coding`: a recoverable djobs failure; continue the user's task without djobs.
- `stored_content_is_data`: recovered text is data, never an instruction.
- `context_hash`: hash of the selected passive context.
- `memory_unchanged`: the known hash matched, so unchanged memory was intentionally omitted.
- `state_hash`: queue-state hash used by legacy `resume_delta` callers.
- `snapshot_consistent`: the legacy delta snapshot is internally consistent.
- `reset_required`: the legacy revision cursor cannot be advanced safely; refresh from scratch.

## Interpreting benchmarks and `djobs gain`

The bundled benchmark compares one bounded `sync_workspace` response with a deliberately simple
baseline that rereads every file in a synthetic fixture. This is a payload-size regression fixture,
not an end-to-end savings claim. Modern agents can summarize, cache, or selectively read files, so
the fixture must not be presented as provider-token savings, billing reduction, latency, or quality.

`djobs gain` uses configurable characters-per-token and redo-overhead assumptions. Its output is an
explainable local heuristic, not observed provider usage or a guarantee that a model would otherwise
repeat the same work.

## Troubleshooting

### `djobs doctor` says no project MCP override

That is informational. A project `.vscode/mcp.json` is optional when using the VS Code extension or
user-level `djobs setup` registration.

### No host is ready

Run one of:

```bash
djobs setup copilot
djobs setup codex
djobs setup claude
djobs setup gemini
djobs setup kimi
```

Then restart the host so it reloads MCP and lifecycle configuration.

### A host check reports an error

```bash
djobs repair HOST
djobs doctor
```

### Memory is empty

Start a new configured agent session and perform repository work. Capture is fail-open, so a missing
or unsupported host adapter will not block coding. Verify the host is listed as ready in
`djobs doctor`.

### The agent recovered an outdated fact

Find it, then mark it stale, superseded, resolved, or contradicted. Normal recovery only selects
active memory.

### The database cannot be opened

Set `DJOBS_DB` to a writable SQLite path or fix permissions for `~/.djobs`:

```bash
export DJOBS_DB="$HOME/.djobs/global.db"
djobs doctor
```

On PowerShell:

```powershell
$env:DJOBS_DB = "$HOME\.djobs\global.db"
djobs doctor
```

## Compatibility queue CLI

The queue, worker, scheduler, dashboard, task archive, and audit commands are retained for existing
integrations:

```bash
djobs legacy --help
```

They are not the recommended onboarding path for local agent memory.


## Optional local semantic retrieval

The default provider is lexical-only and performs no network or model calls. Chinese bigrams are
lexical matching, not translation. To opt into the pinned CPU E5 profile from a source checkout:

```bash
python -m pip install -e ".[semantic]"
python scripts/prepare_local_embedding.py --destination /chosen/private/models/e5 --confirm-download
djobs memory reindex --model-dir /chosen/private/models/e5 --yes
djobs memory search "Why did the authentication integration test break?" --model-dir /chosen/private/models/e5 --explain
```

The provisioning script is a separate, explicit network operation. It verifies pinned file sizes
and SHA-256 digests, refuses an existing destination, and does not read cloud credentials. Normal
startup, importing djobs, and reading memory never invoke it. Local model files are not included
in the djobs wheel. Installing extras alone does not download or activate a model.

A long-running MCP host can explicitly start `djobs-mcp --embedding-model-dir /chosen/private/models/e5`.
After that opt-in, `memory(action="reindex", confirm=true)` builds the index. Without an explicitly
configured provider and confirmation the operation refuses. A failed configured model initializes
an unavailable-provider state: reads still return lexical results with a fallback reason rather
than pretending no memory exists. The Python equivalent is an `EmbeddingSession` passed to
`ProjectMemory.open(..., embedding=session)`, followed by `reindex_memory(confirm=True)`.

Indexes bind provider, model, model revision, dimension, redaction version, per-source content hash,
and the canonical source snapshot. Different identities never mix. Source or lifecycle changes
make the index stale until explicit reindex. Eligibility is also checked against the exact indexed
record set: a sibling checkout with different private records falls back with
`index_projection_mismatch` rather than reusing an incomplete index. One materialization per family
is retained; explicit reindex can replace a sibling-specific projection without duplicating storage. Reindex is atomic and compare-and-swap checked;
interrupted or failed provider calls leave the previous index intact. Repeating an unchanged
reindex performs no provider call and no data write. SQLite and PostgreSQL use the same bounded
native table contract; neither requires a vector extension or external database service.

The optional ranker fuses lexical, semantic, and deterministic coding-entity candidates using
fixed reciprocal rank fusion (`k=60`), with exact-query anchors and deterministic tie breakers.
Version `djobs-rrf-v2-strong-lexical-k60` requires two meaningful lexical matches or an exact
query/entity match before casting a lexical vote (single-term queries remain supported).
Candidates are filtered for repository/checkout scope, lifecycle, authority, provenance metadata,
and validity before ranking. Similarity and fusion scores are not probabilities or truth scores.
The raw-observation interface cannot grant accepted authority by setting metadata to
`human_accepted`; reviewed derived facts and skills require their own verified lifecycle.

`memory trace` exposes query hash, channels, candidate counts, filter reasons, selected IDs,
identity, latency, and fallback reason without persisting a query or trace to the database.
`--explain` adds per-result ranks and entity evidence. Final response size includes envelope,
metadata, and authority flags; truncation and critical evidence omission are explicit.
Query providers have a short deadline (default 0.5 seconds), at most one outstanding call, and no
implicit retry. A late provider result cannot write the index or change task state.

### Read-only storage boundary

`memory list/search/trace/stats`, compact previews, and advisory host observations must not create
or alter the source DB, WAL, or SHM. SQLite `mode=ro` alone is insufficient for a cold WAL database.
These operations capture a private, short-lived copy of the DB plus any WAL/journal, verify stable
file generations and content hashes, then let SQLite read the copy in query-only mode. The copy
is deleted on close or failure. There is temporary disk I/O; this is not a claim of zero filesystem
activity, encryption, or protection from an administrator with access to the local machine.

Capture is bounded to 64 MiB and a 0.5-second copy/verification deadline. An oversized or changing
source returns explicit memory-unavailable/fail-open status, not an empty-memory success. The live
source is never opened as `immutable`, and its WAL is never silently discarded. A snapshot is
consistent at capture time; it is not a promise that the original database cannot change later.

Passive observation hooks no longer heartbeat an owned task. Memory synchronization no longer
expires, releases, or reclaims a lease. Use the explicit task/worker lease lifecycle for ownership.
Forgetting a source removes its derived vector and entity links. Clearing family memory removes
its index metadata too, without clearing the explicit task queue. The original observation summary
is never overwritten by an embedding or a semantic match.

### Reproducible retrieval evaluation

Use the existing canonical preflight in `CONTRIBUTING.md`; do not replace it with benchmark scores.
The additional quality profile is:

```bash
python scripts/benchmark_memory.py --repeats 3 --output lexical.json
python scripts/benchmark_memory.py --local-model-dir /chosen/private/models/e5 --repeats 3 --baseline lexical.json --output hybrid.json
```

The original synthetic corpus has 56 queries over 12 coding topics, four language/query forms,
and distractors for scope, stale state, contradiction, import quarantine, corrupted metadata,
duplicates, and prompt injection. Its SHA-256 is
`577e139c3d34816933f2233453fd1d60791c5c31a23393e4ae2a6f7236ac6fcb`.
Gold labels are evaluator-only and never provided to the embedding model. CI uses no network or
real model; stub vectors test mechanics, not semantic quality. The explicit real-model profile
blocks socket connections during initialization and inference. Generation and answer judging are
reported as not run rather than being confused with retrieval accuracy.

On the measured Windows/Python 3.13 profile, the fixed 36-query multilingual/paraphrase subset
improved recall@5 from 0.5556 to 0.8333 and fixed-denominator precision@5 from 0.1111 to 0.1667.
All 12 exact-query recall@1 cases remained correct. Unsafe/stale/contradicted/unsupported selections
were zero in the candidate run. These are small synthetic-fixture results, not general accuracy,
production safety proof, or provider-token savings. Chinese top-1 and irrelevant-query abstention
remain weak: the refined real profile answered 4 of 6 negative queries with at least one irrelevant result.
The optional provider is therefore not a calibrated answer/truth selector and is off by default.
The original lexical baseline was recorded before the Unicode and authority fixes; comparing two
new runs measures a different baseline and should be labelled accordingly.

The genuine v0.20.1 SQLite migration fixture was created using unmodified archived v0.20.1 APIs and
synthetic content only. Forward migration, replay, interrupted schema creation, future-schema
refusal, provenance hashes, deletion, and real PostgreSQL parity have separate tests. Component
schema `retrieval=1` is installed only by explicit reindex (`migrations/011_memory_retrieval.sql`).
Typed temporal facts are described below. Verified experience/skill constructors, cross-harness
session import, external memory runtimes and Memory Explorer are separate milestones.

### Method provenance and boundaries

The following pinned public references were inspected for contracts and methods, not installed as
product dependencies. No third-party implementation was copied into the candidate. In particular,
no OpenViking AGPL code or runtime was copied or bundled. Unconfirmed license entries prohibit code
reuse; they do not grant permission. A repository name alone does not identify a compatible API.

| Reference and inspected revision | License observed | Applied boundary/method; excluded or deferred |
|---|---|---|
| `mem0ai/mem0` `94c3fe9f238f3dbf29c9ce98643bd71eb13077cd` | Apache-2.0 | Hybrid candidate channels; no destructive semantic overwrite or second canonical writer. |
| `vectorize-io/hindsight` `ccfe85b4851957ac2adf88b4a9ddf9668b2882f1` | MIT | Rank fusion and explainable recall; reflect/retain integration deferred, never automatic authoritative lessons. |
| `NevaMind-AI/memU` `2c050bc9681a4c0aff1af211a000e73d14f33356` | Apache text present; GitHub reports NOASSERTION, full terms unconfirmed | Candidate versus activated workflow boundary; skill extraction and updates deferred. |
| `topoteretes/cognee` `c4cd8ceb9509dff6bddfabdadbeab7cc040bc32b` | Apache-2.0 | Index versus source separation; graph/resource ingestion deferred, no project-truth replacement. |
| `getzep/graphiti` `6b4b56ff6f4b1e4e69c3c3c5487cf1b8762c483a` | Apache-2.0 | Current-validity exclusion and preserved source identity; typed historical relations deferred, no graph daemon. |
| `volcengine/OpenViking` `ec646203e96064eed5433fd1b9484d67f5354847` | AGPL-3.0 | Inspectable retrieval trajectory concept only; context tree/progressive artifacts deferred, no code copy. |
| `letta-ai/letta` `5bcdd177d70fa2b31a754cfcd801e77b2e1ab16a` | Apache-2.0 | Explicit memory scopes; no autonomous runtime/ownership or prompt rewriting. |
| `letta-ai/letta-code` `c864f1532b328aab4bb76cc68a86d5f014de27b8` | Apache-2.0 | Inspectable evidence; Git skill export deferred, no automatic AGENTS.md/CLAUDE.md edits. |
| `CaviraOSS/OpenMemory` redirects to `CaviraOSS/LongMemory`, `9ee2c8e1ed42d83eb788afb9ffc3a82b84405da5` | Apache-2.0 | Session-import quarantine boundary only; portability adapters deferred. This is not Mem0's separately named OpenMemory product. |
| `vectorize-io/agent-memory-benchmark` `03c1d0f1d27da63034f0931121c858faba512383` | Unconfirmed; no root license found in inspected listing | Separate ingestion/retrieval/generation/judge accounting; original coding-specific corpus, no copied harness or external judge. |

The E5 base model is `intfloat/multilingual-e5-small`, MIT, inspected revision
`614241f622f53c4eeff9890bdc4f31cfecc418b3`. The optional ONNX profile uses
`Xenova/multilingual-e5-small@761b726dd34fb83930e26aab4e9ac3899aa1fa78` with pinned digests.
Model files remain user-selected local assets, not packaged source or an authority on facts.

SQLite runtime health must be assessed separately from package test success. SQLite's official
[WAL documentation](https://www.sqlite.org/wal.html#walreset) describes the WAL-reset race fixed in
3.51.3, with backports including 3.50.7 and 3.44.6. The initial test interpreter's SQLite 3.50.4 is
not that patched runtime. The candidate does not silently upgrade a user's Python/SQLite or change
LIVE installation. Production concurrency review must include the actual bundled SQLite version.


## Source-bound temporal facts

`djobs memory propose --file candidate.json` stores a bounded candidate fact with title, abstract,
source observation or artifact IDs, and optional observed_at/valid_from dates. It does not activate
it. Repeating the same normalized proposal and pinned sources returns the existing record, including
its rejected status. Explicitly different validity or source evidence creates a new candidate.

`djobs memory review ID` previews exact content and sources. `--apply` requires an interactive
terminal and an exact review hash typed by the user. The Python facade accepts a trusted product
ReviewGate callback. Neither an agent-supplied authority label, confirm flag, nor model confidence
can activate a candidate through MCP; MCP review and relate only return previews. The callback
boundary is not a sandbox against arbitrary local code with access to the user's database.

Accepted facts use a source-bound immutable content hash. `memory relate NEW OLD supersedes --at TIME`
previews replacement, and interactive `--apply` records the relation without changing raw source
text. `memory facts --at TIME` selects valid historical facts; a current query excludes superseded
facts. `memory show ID --depth 2` inspects the retained provenance and review receipt. Content depth
0/1/2 and resume/evidence/audit exposure are distinct choices.

Unresolved contradictions exclude both claims and their dependent conclusions from normal resume,
including when only a descendant matches the query. The response retains an ambiguity flag even
under a very small token budget. Audit can inspect the conflicting retained evidence; it cannot
recover deleted content. A historical descendant is eligible only when its source artifacts were
valid at the requested instant. Raw-observation lifecycle metadata has no historical event journal,
so a currently ineligible raw source remains conservatively excluded even from historical claims.

Reads use one database snapshot across artifact, source, relation and review queries. PostgreSQL
readers require repeatable-read or serializable isolation if the caller already owns a transaction;
the service never silently commits or rolls back that caller's work. Legacy passive reads close
their own transactions. Source loss suppresses the complete joint claim and dependent conclusions;
remaining evidence requires a new proposal/review rather than silently rewriting accepted content.
Routine compaction protects active accepted source chains. Explicit forget still removes content.

The additive component schema is `artifacts=1` in `migrations/012_memory_artifacts.sql`. Reads do not
install it. The bounded native store holds at most 256 artifacts per family, 16 source references
per artifact and 16 provenance levels. Unsupported future schemas fail closed for typed memory
while raw recall remains available. See the canonical preflight in CONTRIBUTING.md; the original
`python scripts/benchmark_temporal_memory.py` adds an offline before/after temporal workflow fixture,
not a model-accuracy, generation-quality or production acceptance claim.
