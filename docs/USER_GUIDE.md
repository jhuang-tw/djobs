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
Forgetting raw evidence also deletes same-time or later capsules in the same agent/session and explicitly linked
capsule copies, together with their typed descendants and derived indexes. This is conservative:
legacy truncated capsules cannot prove which copied fields depended on the source. Earlier capsules,
unrelated unlinked sessions, and unrelated raw observations are preserved; an interrupted cascade rolls back.
Equal-time legacy capsules are conservatively erased because the clock cannot establish their order.
This does not identify arbitrary unlinked textual copies in other sessions.
Automatic conflict invalidation preserves original authority and content while moving survivors
back to candidate status. Its maintenance receipt is not a new human review or execution permission.
Capsule source capture is locked and revalidated in the insertion transaction, so a source forgotten
between read and write cannot be copied back into a new capsule. Explicit forget parses each capsule
once and bounds the scan to 1,024 capsules, 16,000 metadata characters per capsule, 4 MiB total encoded
metadata, and 65,536 traversal nodes. Malformed JSON or non-object capsule provenance also refuses
the operation with a bounded error; no content is included in the error. Exceeding a bound refuses
the whole transaction rather than
reporting a partial deletion as success. Explicit repository-family clear remains a separate action.

Deleting one side of an unresolved contradiction is not a reviewed resolution. The surviving facts
and their dependent artifacts become candidates requiring fresh individual review, including known
future-dated conflicts. Their own immutable content and previous review receipts remain available,
and a non-sensitive invalidation receipt contains no forgotten endpoint ID or text. Marking a source
stale does not erase an existing conflict either. Previously resolved conflicts are not reopened.
While re-review is pending, historical resume is also conservatively withheld for demoted records;
the surviving audit record remains inspectable, but this is not a complete bitemporal review archive.

The additive component schema is `artifacts=1` in `migrations/012_memory_artifacts.sql`. Reads do not
install it. The bounded native store holds at most 256 artifacts per family, 16 source references
per artifact and 16 provenance levels. Unsupported future schemas fail closed for typed memory
while raw recall remains available. See the canonical preflight in CONTRIBUTING.md; the original
`python scripts/benchmark_temporal_memory.py` adds an offline before/after temporal workflow fixture,
not a model-accuracy, generation-quality or production acceptance claim.


## Verified experiences, lessons and reviewed skills

`memory experience --file experience.json` previews a source-bound outcome. The document contains
kind=experience, title, abstract, source episode IDs, and details: objective, method, context,
outcome (success/failure), failure_reason, changed_paths, checks, terminal_effect. Each check names
source_id, check and evidence; that source must be a member of a selected episode. No experience
is stored until `--apply` obtains explicit interactive human verification or a trusted product
ReviewGate accepts the exact preview and source state. A supplied exit-code, success text, model
vote, session stop, or transcript never grants verification. This version supports the explicit
human/product acceptance route, not authenticated external check-receipt verification.

A verified failure is retained as failure, never silently promoted to success. Lessons require
verified experiences and conditions, proposed generalization, uncertainty and boundaries. Skill
candidates require verified experience sources and at least one verified success; their details
include name, description, semantic version, when_to_use, when_not_to_use, preconditions, steps,
verification, failure_modes, rollback and boundaries. Propose stores candidates only. Two successful
experiences do not activate a lesson or skill. Every activation requires another explicit review.

The stored record type remains immutable `skill_candidate`. After human acceptance its public
effective type is `skill`, with record_type retained for audit. IDs, source links and content hashes
are not rewritten on promotion. `memory show ID --depth 2` includes the human-readable Markdown.
The Python facade offers verify_experience, propose_lesson, propose_skill, active_skills and
export_skill. MCP adds only actions to the existing memory tool. Experience/review/export actions
through MCP are previews; JSON confirm flags cannot impersonate the trusted human review gate.

`djobs memory export SKILL_ID exports/skill.md` previews a new-file Git diff. `--apply` requires exact
interactive review and rechecks sources and destination. Only an active accepted skill can export,
only to a new Markdown file in an existing real directory inside the chosen Git worktree. Existing
files, traversal, symlink/reparse parents, reserved Windows names, alternate streams, active agent
prompt directories and AGENTS.md/CLAUDE.md are refused. Export does not stage, commit, install,
execute, autosync or change canonical memory. An explicitly exported file is a user-owned copy;
forgetting canonical memory does not silently delete that external copy. This API is not an OS
sandbox against privileged concurrent directory changes.

The original `scripts/benchmark_verified_learning.py` exercises preview/reject/verify, two-experience
candidate lessons, candidate-versus-accepted skill state, preserved content identity and source
forgetting. Its synthetic review callbacks validate plumbing, not actual user acceptance or model
accuracy. SQLite and PostgreSQL run the same workflow in repository contract tests.


## Inspectable context projection

`djobs memory tree` browses a virtual repository context tree without creating files. Supported
categories are episodes, facts, experiences, lessons and skills. Candidate skills and their accepted
effective skill type retain the same canonical URI. No unsupported resource/import folder is
presented as indexed data. Each shown node has a repository-bound `djobs://repo/...` URI; `memory show
URI --depth 1` loads its overview, while `--depth 2` explicitly exposes retained details, provenance,
relations and reviews. Wrong repository/category, noncanonical escaping and traversal are rejected.

L0 is a bounded abstract; L1 adds overview, validity and source counts; L2 adds detailed evidence.
These are content depths, independent of resume/evidence/audit/candidates exposure. Normal current
queries still exclude unreviewed or unsupported artifacts and unresolved conflicts. An old saved
URI does not resurrect a forgotten source. Tree query document fields are uri/depth/at/exposure;
MCP `memory(action="trace", document={"plane":"artifacts"}, query=...)` adds a non-persisted typed
selection trace with a query hash, candidate/filter counts, selected IDs and projection count.
No full trace is added to normal resume. The five MCP tool names remain unchanged.

Only the selected bounded records are projected after L0 title/abstract matching and source checks.
The native store still reads its bounded source-validating snapshot, including full content for hash
validation. This milestone reduces projection work and returned bytes, not physical database I/O.
`python scripts/benchmark_temporal_memory.py --projection` measures this distinction explicitly with
the same five records at L0/L1/L2 and 20 candidate records. The read-through tree is not a second
memory authority, disk mirror, graph database, or prompt installation mechanism.


## Explicit quarantined session portability

`djobs session discover ROOT` lists bounded candidate JSON/JSONL files under an explicit directory,
without parsing their content or inspecting a home directory automatically. It skips links/reparse
points, hidden files and known auth/config filenames. Listed files are not yet format-validated.
`djobs session preview ROOT RELATIVE_FILE --harness claude|codex|opencode|djobs` shows selectable text
message IDs. Repeat with one or more `--id` options to bind an exact redacted selection. It returns
a preview hash and the current repository-family identity without writing the memory database.

`djobs session import ROOT RELATIVE_FILE --harness codex --id MESSAGE_ID --expected-hash HASH
--repo-family FAMILY --yes` rechecks the file, selection and binding before writing. Without --yes,
or with --dry-run, it only previews. Sources are bounded to 2 MiB/4096 records and imports select at
most 32 messages with bounded per-message text. Truncated text, skipped blocks and partial selection
are explicit. A changed file requires a new preview. Exact repeated selections deduplicate, including
when a rejected/reference-reviewed import already exists; no duplicate approval is implied.

Each native import retains harness, inspected shape version, source session ID, source content hash,
adapter version, import time, repository binding, redaction version and source-path fingerprint.
No original path, credential, tool configuration, harness permissions or task ownership is restored.
These are same-database canonical djobs import records, not an external writer. The additive
component `session_imports=1` is installed only by explicit import (migration 013_session_imports.sql).

All imported text starts as imported_unverified in session/import scope. `djobs session review ID`
previews; interactive --apply can mark reviewed_reference or rejected, never an active fact, verified
experience or skill. The review acknowledges inspected reference material, not every statement's
truth. Normal sync_workspace/raw recall/typed resume continue to exclude the transcript after review.
MCP uses the existing memory tool with document.operation; no JSON confirm can impersonate review.
`memory tree --exposure audit` exposes the imports category, and saved repository-bound import URIs
cannot retrieve content after forget. Clearing repository memory clears imports without changing tasks.

`djobs session export ID` writes a redacted djobs.session.v1 conversation bundle to stdout. The user
controls any shell redirection; djobs does not overwrite a destination or install a foreign session.
The bundle can be selectively imported again. It does not transfer review status and never claims
full native resumption. Export retains selected user/assistant text and parent references; tool calls,
attachments, reasoning blocks, permission state and forked history are not restored. External parent
or history_base references are disclosed as not followed, never opened automatically.

The clean-room adapters use these inspected primary-source shapes, not copied implementations:
Claude Agent SDK Python 36f95486ee9fc49d8ee1ed56811f07b5e8e23ac6 (_internal/sessions.py and its official
tests: type/uuid/parentUuid/sessionId/message JSONL); Codex 44fe510ce3ee61c8ef623adcbf89b901c73ddd61
(rollout tests and line parser: session_meta plus response_item/message JSONL); OpenCode
03e67171ab2dc1e7f16e8cebfbc7f778f61b89f0 (cli/cmd/export.ts: info + messages, message info + parts).
These date-pinned observed shapes are not promises to accept every past/future harness format.
Unrecognized schema versions or malformed required fields fail closed; unsupported blocks are
counted. Only original synthetic fixtures were used in validation, not personal session history.

The original scripts/benchmark_session_portability.py exercises all three inputs, duplicate handling,
redacted text roundtrip, quarantine, explicit reference review, no task change and clear. It runs
with the same SQLite/PostgreSQL contract and no model/network dependency. Its output measures safe
text portability, not complete conversation reconstruction, inference quality or token savings.


## Optional external candidate comparisons and AMB bridge

`ExternalMemorySession` accepts an explicitly injected trusted adapter, exact repository family,
explicit enabled=true and a short deadline. The client contract is health/index/retrieve/
delete_derived_copy with a namespace binding adapter revision, repository and redaction version.
Only a confirmed index operation sends bounded redacted canonical text, IDs and hashes. It never
passes a repository handle, task authority, review gate, or credential configuration to the adapter.
This is not an OS sandbox for arbitrary Python code supplied by the embedding application.

`ProjectMemory.external_memory(session, operation="retrieve", query=...)` returns the unchanged
native lexical results plus separate external_candidates. Foreign candidate IDs/hashes are checked
against native source/scope/lifecycle evidence. Foreign text, authority labels and probabilities are
ignored even when the ID is valid. Unmatched, stale, imported-unverified, out-of-scope and forgotten
records are excluded. No unmeasured vendor score alters default resume, activates a fact/skill or
changes ownership. The response is still source-snapshot evidence, not a guarantee that a concurrent
writer cannot change the live database after capture. Smaller payload budgets drop external candidates
before native results.

The same facade supports confirmed index and derived-copy deletion. A timeout returns lexical
fallback and permits only one outstanding client call. Exceptions are reduced to bounded reason
codes; late results have no callback that can write canonical state. External indexing/deletion may
still complete after timeout, so its remote effect is explicitly unknown. A separate external copy
requires external cleanup; canonical forget is protected by native revalidation, not a claim that
an unreachable vendor has deleted its data. No adapter runs unless explicitly injected/enabled.

Vendor-specific Hindsight, Mem0, Cognee, Graphiti and OpenViking transports are not included or
live-validated by this milestone. Their appropriate boundary is this read-only/advisory index contract;
reflection outputs still require a separate candidate proposal and explicit review. No vendor SDK,
model account, AGPL implementation or external daemon is added to the default installation. The
original scripts/benchmark_external_memory.py tests hostile fake responses and both repository
backends; it measures the authority boundary, not external retrieval efficacy.

`djobs.amb_adapter.create_amb_provider()` lazily loads the caller-installed Agent Memory Benchmark
interface and returns a clean-room MemoryProvider bridge. It follows the inspected contract at
vectorize-io/agent-memory-benchmark@03c1d0f1d27da63034f0931121c858faba512383, using the Document fields
id/content/user_id/source_ids. No upstream implementation is copied or bundled, and no AMB package
is imported by normal djobs startup. Interface tests use original fixtures, not a claim that the
complete external AMB dataset or answer-judge suite has run.

Call prepare with an explicit benchmark directory. It creates/reuses only an owner-marked
`djobs-benchmark.db`, refusing to migrate/reset an unknown existing DB. It does not use DJOBS_DB,
~/.djobs/global.db or production observations. Ingestion is redacted, source-ID-preserving, immutable
per document ID, atomically deduplicated, bounded to 1000 documents per unit and 256 units, with
2000-character content truncation explicitly counted. Native lexical retrieval enforces user/unit
isolation, bounds k to 20 and returns exact original source IDs. Async wrappers call the same service.
This V1 bridge does not support timestamp/filter queries or direct answer generation; those modes
raise explicit unsupported errors rather than report fabricated results. It is a retrieval-only
baseline; generation/judging and real model measurements remain separate benchmark phases.


External family namespaces export only `repository_family` observations. Checkout, agent and
session-scoped records are excluded even when locally readable; local eligibility is not permission
to publish private scope into a shared external namespace. Returned candidate IDs are checked again
against this narrower export policy. External mutation confirmation requires literal `true`.
An index/delete timeout reports `external_effect=unknown`, not cancellation: the external operation
may finish later. No retry is sent automatically; adapter-specific reconciliation remains explicit.


## VS Code memory inspection

The native **djobs Memory** Explorer view is an on-demand client of the same CLI/application APIs.
Workspace roots bind to folder-specific runtime/database settings. L0 abstracts load first; opening
an item requests L1, while L2 requires an explicit depth selection. Exposure choices remain separate:
resume, evidence, audit/history, and candidates. Ambiguity and truncation come from the backend and
are not resolved or hidden by the UI. Refine the query when the bounded list is truncated.

Details are bounded read-only JSON virtual documents. Refresh, configuration changes, removed
workspaces, and closed documents invalidate their ephemeral content and discard stale pending
responses. No polling, webview, generated repository projection, or new MCP tool is involved.
Review opens a native preview then an explicitly requested interactive CLI; only the existing
content-bound human review gate can accept or reject. Forget requires modal confirmation and calls
the existing canonical deletion service. Untrusted workspace reads and all automatic acceptance
are refused. See `vscode-ext/README.md` for the controls.

The canonical full preflight now compiles the extension and runs its Node API-contract tests.
`vscode-ext/tests/extensionHost.cjs` is an additional actual-host suite for an explicitly provisioned
synthetic workspace/profile, not a normal startup action. It must never be run against a user's
existing profile/database or be used to claim that synthetic review callbacks are real acceptance.


The Explorer's read-only guarantee concerns normal editor interactions and canonical memory
storage. It is not a sandbox against other trusted extensions: VS Code's privileged WorkspaceEdit
API can alter a displayed buffer without writing the source DB. Such text has no memory lifecycle
or execution authority. Fresh backend reads and review bindings, not an editor buffer, determine
what is eligible or accepted.


`djobs doctor` now reports an offline advisory for the known SQLite WAL-reset fix. It recognizes
3.51.3+ and the 3.50.7/3.44.6 patched release branches; other version strings require a runtime
upgrade or verification of the vendor's backport. It does not contact a server, block coding,
change journal mode, install a runtime, or certify protection against all database issues.
