# Phased Roadmap

This roadmap divides the Distributed Job System into multiple deliverable phases. Each phase should have clear demos, tests, and architecture stories.

## Phase 0: Project Foundation

Status: Completed.

Goal: Make the project developable, testable, and stable for the next round of AI handoff.

Deliverables:

- Python package structure.
- `pyproject.toml`.
- pytest setup.
- ruff setup.
- basic config.
- basic structured logging.
- smoke tests.
- README and docs completion.

Out of scope:

- Do not implement queue behavior.
- Do not implement worker crash recovery.
- Do not implement distributed coordination.

Interview story:

- Why start with a small and clean package boundary.
- Why a distributed systems project should first define domain models and state machines.

## Phase 1: Single-Node Job Queue MVP

Status: Completed.

Goal: Build a working durable job queue.

Scope principle: First implement single-node, synchronous, persistable queue. Do not implement retry policy, lease, heartbeat, rate limiting, or distributed locking, but leave the repository interface ready for future atomic claim evolution.

Features:

- create job.
- enqueue job.
- worker poll pending job.
- worker claim job.
- handler registry.
- execute handler.
- update job status.
- SQLite storage.
- minimal event log: record `job_created`, `job_claimed`, `job_succeeded`, `job_failed`.
- basic CLI or example script.

State machine:

```text
pending -> running -> succeeded
pending -> running -> failed
```

Test focus:

- job can be created.
- pending job can be claimed.
- successful handler marks job succeeded.
- failing handler marks job failed.
- state changes write minimal event log.
- invalid state transition is rejected.

Interview story:

- How to design job lifecycle.
- Whether queue and state should be in the same table or separate.
- How workers claim tasks.
- Why start with at-least-once mental model.
- Why Phase 1 should already leave event log, letting subsequent observability grow naturally.

## Phase 2: Retry, Idempotency, And Dead Letter Queue

Status: Completed.

Goal: Add reliability core.

Scope principle: Handle "whether to retry after failure" and "how to preserve manual intervention space after retry exhaustion". Do not implement worker lease, heartbeat, rate limiter, or complete scheduler in this phase.

Features:

- retry policy.
- exponential backoff.
- max attempts.
- failed reason.
- retryable vs non-retryable error.
- idempotency key.
- dead letter queue.
- retry promotion: convert due `retry_scheduled` jobs back to `pending`.
- richer event log: record retry scheduled, retry promoted, dead-lettered.

New states:

```text
retry_scheduled
dead_lettered
```

Important design:

- Handler failure does not immediately mark failed.
- If attempts have not reached the limit, transition to `retry_scheduled`.
- After `run_after` expires, return to `pending`.
- After reaching max attempts, enter `dead_lettered`.
- `failed` means non-retryable terminal failure.
- `dead_lettered` means retryable failure has exhausted retry budget, requiring manual inspection.

Test focus:

- retry attempt increments.
- backoff time is calculated.
- due retry is promoted back to pending.
- max attempts sends job to DLQ.
- same idempotency key does not create duplicate active job.
- event log records state changes.

Interview story:

- How to avoid retry storms.
- How to handle duplicate execution.
- Why idempotent handlers are important.
- Purpose of DLQ is to preserve manual intervention space.

## Phase 3: Lease, Heartbeat, And Crash Recovery

Status: Completed.

Goal: Let jobs be recovered by the system after worker crash.

Features:

- task lease.
- visibility timeout.
- worker id.
- worker heartbeat.
- expired lease detection.
- stale running job requeue.
- graceful shutdown.

New fields:

- `leased_by`.
- `lease_expires_at`.
- `heartbeat_at`.

Important design:

- When worker claims job, set lease.
- Worker periodically heartbeats during execution.
- If worker crashes, heartbeat stops.
- Recovery loop finds expired leases, converts job back to `pending` or `retry_scheduled`.

Test focus:

- active lease prevents other worker from claiming job.
- expired lease allows recovery.
- heartbeat extends lease.
- worker shutdown does not lose job.

Interview story:

- Difference between lease and lock.
- How to recovery when worker crashes.
- Why visibility timeout exists.
- Why exactly-once is hard.

## Phase 4: Scheduler And Delayed Jobs

Status: Completed.

Goal: Add delayed job and scheduled retry promotion.

Scope principle: First stabilize time-based job promotion. Recurring tasks easily involve schedule identity, duplicate triggers, and multi-scheduler coordination; list as optional Phase 4b, not a Phase 4 completion requirement.

Features:

- scheduler loop.
- delayed job promotion.
- retry promotion.
- optional Phase 4b: simple recurring schedule.
- optional Phase 4b: schedule metadata.

Important design:

- Only jobs with `run_after <= now` can be claimed.
- Scheduler promotes due scheduled jobs to pending.
- If Phase 2 has a simplified retry promotion version, Phase 4 will refactor it as part of the scheduler loop.
- When multiple schedulers exist, avoid duplicate triggers for the same schedule; Phase 7 strengthens with leader election or scheduler lock.

Test focus:

- future job is not claimed early.
- due job is promoted.
- retry_scheduled job is promoted when due.
- optional Phase 4b: recurring job creates next occurrence.

Interview story:

- How scheduler avoids duplicate triggers.
- Relationship between delayed retry and queue priority.
- How to test time-based systems.

## Phase 5: Concurrency, Backpressure, And Rate Limiting

Status: Completed.

Goal: Handle production pressure.

Features:

- worker concurrency limit.
- queue-level concurrency.
- rate limiting.
- backpressure.
- queue backlog metrics.
- graceful drain.

Important design:

- Worker should not claim jobs without limit.
- Queue can set concurrency per job type.
- Retry and rate limit must be considered together to avoid massive retries exploding downstream.

Test focus:

- concurrency limit is respected.
- rate limiter delays execution.
- backlog metrics are updated.
- worker can drain before shutdown.

Interview story:

- What to do when backlog grows.
- How to protect the system when downstream API explodes.
- Where the upper limit of worker scaling is.

## Phase 6: Observability

Status: Completed.

Goal: Organize accumulated logs, event log, and queue state into actionable observability, not a black box.

Scope principle: Phase 0 already has structured logging; Phase 1 has minimal event log. Phase 6 should not start recording events but strengthen query, metrics, health, and troubleshooting experience.

Features:

- structured logs polish.
- metrics.
- job event timeline.
- worker health endpoint or CLI health command.
- trace id / correlation id.
- queue depth view.

Important design:

- Every state transition writes event log.
- Each job has correlation id.
- Worker logs include worker id, job id, attempt, duration.
- Failed job inspect output should answer "when did it fail, how many times did it fail, what was the last error, is next retry or manual intervention".

Test focus:

- event log records all transitions.
- metrics expose queue depth.
- failed job includes error details.

Interview story:

- How to debug stuck job.
- How to know if worker is healthy.
- How to track a job from submit to completion.
- How SLO and alert can be designed.

## Phase 7: Distributed Mode

Status: Completed.

Goal: Evolve from single-node to truly multi-worker, multi-process system.

Features:

- PostgreSQL storage.
- docker compose.
- multiple workers.
- transactional claim.
- repository contract tests: SQLite and PostgreSQL must comply with same repository behavior.
- scheduler coordination: can use advisory lock or leader election, keep optional for now.
- migration scripts.

Important design:

- Claiming job must be atomic operation.
- When multiple workers claim simultaneously, only one can succeed.
- Job claim preferentially uses PostgreSQL transaction, row lock, and `SKIP LOCKED`.
- Advisory lock is more suitable for scheduler leader election or cross-row coordination, not the first choice for general job claim.

Test focus:

- concurrent workers do not claim same job.
- lock timeout is handled.
- crashed worker lease can recover.
- SQLite / PostgreSQL repository contract tests pass.

Interview story:

- How multiple workers avoid claiming same job simultaneously.
- Choice of DB transaction isolation.
- How row lock, `SKIP LOCKED`, lock timeout / deadlock are handled.
- Where the scale-out limit is.

## Phase 8: AI Task Platform Demo

Status: Completed.

Goal: Package the system as a demonstrable AI workload platform.

Scope principle: First demonstrate special pressure AI workloads exert on queue system: long tasks, expensive retries, rate limits, cost tracking, and idempotent side effects. Dependency / DAG can be reserved as extension, not a necessary condition for the first-version demo.

Features:

- AI job type examples.
- batch task submission.
- retryable AI call simulation.
- cost / token usage metadata.
- final demo script.
- optional extension: task dependency / simplified DAG.
- optional extension: workflow run.

Important design:

- AI tasks are often long, expensive, and rate-limited.
- Retry must consider cost, not just success rate.
- Idempotency is important for AI side effects, such as writing files, sending notifications, updating databases.

Interview story:

- Why this resembles mini Temporal.
- What special problems AI workloads have.
- How long tasks, failure, retry, cost control are designed.
- How to evolve from MVP to platform.

## Phase Discipline

Only do one small slice at a time. Before completing a phase, do not steal implementation of later major features. If you must reserve fields or interfaces, please mark them as future-ready in documentation, not claim them as completed.

## Phase 9: Durable AI Agent Runtime (MCP)

Status: Completed.

Goal: Package the job system as "durable execution runtime for AI agents". Solve the pain point of Copilot/AI agent sessions being interrupted and losing in-progress work.

Scope principle: Leverage existing lease + heartbeat + crash recovery capability, expose to VS Code Copilot Chat via MCP server. Do not implement HTTP transport or standalone deployment.

Features:

- MCP server (stdio transport) exposes 5 tools:
  - `enqueue_task`: submit durable task.
  - `check_task`: check task status.
  - `list_tasks`: list tasks by correlation_id.
  - `resume_session`: crash recovery entry point, find incomplete tasks.
  - `health`: queue health status.
- `.vscode/mcp.json`: VS Code MCP integration settings.
- `.agent.md`: Durable Coder agent rule definition.
- crash recovery demo: simulate interruption then recovery, zero data loss.
- 16 MCP tool unit tests.

Important design:

- Agent calls `resume_session` at start of each conversation to recover interrupted work.
- Use `correlation_id` = workspace path to cross-session link all tasks.
- Use `idempotency_key` to prevent duplicate submissions.
- MCP server directly reuses QueueService + SQLiteJobRepository.

Interview story:

- Biggest pain point of AI agent: session interruption loses work.
- Why durable execution (lease + heartbeat + recovery) solves it.
- Positioning comparison with Temporal / Inngest / Restate.
- Evolution path from queue system to agent runtime.

## Post-Phase 9 Product Strategy Checkpoint

Status: Recommended adoption.

Core judgment: djobs' technical foundation is now sufficient to support durable queue / MCP / audit trail, but product value still needs sharper use cases. The next phase should not rush into becoming a generic workflow engine, hosted SaaS, or complete VS Code extension, but should first prove a concrete pain point: durable recovery when AI coding agent makes multi-file changes after interruption.

Four-role confirmation:

- Product perspective: first bind to codebase migration / multi-file refactor, stop using "generic queue" as the opening narrative.
- Architecture perspective: do not delete Postgres, daemon, scheduler, worker pool; but lower these capabilities to advanced / internals, not on the first-screen value proposition.
- Reliability perspective: must strengthen against agent forgetting to report, IDE crash, lease expiry, audit evidence and other failure modes, otherwise demo is easily pierced by real usage.
- Engineering management perspective: Phase 10 to Phase 12 first use low-cost validation of market signals; VS Code extension, multi-agent orchestration, hosted dashboard all wait for signal confirmation before scheduling.

Priority order of subsequent phases:

```text
Phase 10  Killer use case demo and README repositioning
Phase 11  Reliability hardening for the killer workflow
Phase 12  Community signal validation
Phase 13  VS Code sidebar MVP, only if signal is good
Phase 14  Multi-agent (done, M1–M5) / hosted dashboard, optional future tracks
```

## Phase 10: Killer Use Case Demo And Positioning

Status: Not started.

Goal: Converge djobs' external story from "durable task queue" to "AI coding agent doing multi-file refactor won't lose progress because of IDE / chat interruption".

Scope principle: This phase prioritizes changing demo, README narrative, and user first impression, not core queue architecture. First prove the killer use case is self-evident, then decide whether to invest in VS Code extension or SaaS.

Features / Deliverables:

- Add or rewrite a codebase migration demo: simulate agent executing docstring / type hint / mechanical refactor on multiple files.
- Demo flow should clearly show: enqueue multiple file-level tasks, complete some, simulate interruption, restart and `resume_session` to recover incomplete tasks, finally complete all.
- README first screen changes to pain-point driven, no longer leads with "SQLite-backed durable queue" as first sentence value proposition.
- README first screen only highlights three core operations: `enqueue_task`, `complete_task`, `resume_session`.
- Postgres, scheduler, daemon, worker pool, rate limit content moves to advanced / internals narrative, not main homepage value proposition.
- Prepare 30-second demo GIF / asciinema script, protagonist is "interrupted mid-edit, resume and continue".

Out of scope:

- Do not delete Postgres backend.
- Do not delete daemon / scheduler / worker pool.
- Do not add VS Code extension.
- Do not add hosted dashboard.
- Do not implement multi-agent orchestration.

Validation focus:

- Demo can run completely with one command in clean environment.
- Demo output lets users see crash recovery value without understanding queue theory.
- README top half should first answer "why I need this", not "what is used underneath".

Interview / Product story:

- Why start from codebase migration instead of generic job queue.
- Why file-level task checklist suits AI coding agent.
- Why changing positioning and demo is more important than adding new features.

## Phase 11: Reliability Hardening For Agent Workflows

Status: Completed.

Goal: Strengthen Phase 10 killer workflow's real reliability, avoid demo success but users hitting false success, false pending, or incomprehensible stuck task in the first week.

Scope principle: Only handle failure modes AI agent durable checklist directly encounters. Do not expand into complete distributed workflow engine.

Features / Deliverables:

- Add optional evidence / summary field to `complete_task`, let agent leave "what was changed" evidence when reporting task completion.
- audit log displays task completion evidence, let users review actual AI agent behavior.
- health / inspect displays stuck running tasks, such as tasks running longer than lease or exceeding set threshold count.
- Documentation adds Failure Modes explanation: agent forgets to complete, agent completes but without sufficient evidence, IDE crash, MCP process crash, lease expiry each have different outcomes.
- Evaluate `djobs serve` as independent persistent daemon usage pattern, let lease recovery not entirely depend on MCP process lifecycle.

Out of scope:

- Do not implement web dashboard.
- Do not implement workflow builder.
- Do not implement team permissions, login, remote sync.
- Do not pursue exactly-once execution.

Validation focus:

- When agent completes task, audit log can see evidence.
- When task stuck running long, CLI / MCP inspect can clearly point out risk.
- After simulating MCP process interruption, restarting still recovers incomplete tasks via lease recovery.

Interview / Product story:

- Why durable agent runtime needs audit evidence, not just status flag.
- Why stuck task is a UX problem, not just queue implementation detail.
- Why honestly describe failure modes, avoid packaging demo as non-existent exactly-once guarantee.

## Phase 12: Community Signal Validation

Status: In progress.

Goal: Before investing in VS Code extension, multi-agent orchestration, or SaaS, first validate whether "AI coding agent multi-file task interruption recovery" is a painful enough problem.

Scope principle: This is a product validation phase, no core code expected. Use Phase 10 demo and README to test market reaction.

Deliverables:

- Release Phase 10 demo to r/ChatGPTCoding, Hacker News Show, Cursor / Cline / Claude Code related communities.
- Consistently validate question: Have users experienced AI coding agent interrupted mid-edit, losing track of which files are done and which need continuation.
- Collect GitHub stars, issues, discussions, PyPI downloads, actual installation and usage feedback.
- Categorize feedback: unclear positioning, installation difficulty, MCP barrier, missing UI, missing review / CI integration, missing team audit.

Pass condition:

- Have clear real user feedback, not just generic praise.
- At least someone willing to try Phase 10 workflow in their own repo.

