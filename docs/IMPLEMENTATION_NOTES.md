# Implementation Notes

這份文件把 Phase 0 狀態與 Phase 1 拆成下一輪可以直接執行的小步驟。

## Phase 0 Implementation Plan

狀態：已完成。

目前已完成：

- `.venv`，使用 Python 3.13。
- `pyproject.toml`，採用 `src` layout、hatchling、pytest、ruff。
- package init files。
- `Config` dataclass + environment variables。
- structured logging helper。
- `Job` model、`JobStatus`、state transition validator。
- import / config / logging / job model / state transition tests。

驗證命令：

```powershell
python -m ruff check .
python -m pytest -v
```

最後驗證結果：ruff 0 errors，pytest 32 passed。

以下是原本 Phase 0 計畫，保留作為歷史紀錄與交付對照。

### Step 1: Python Project Setup

新增：

```text
pyproject.toml
```

建議設定：

- project name: `distributed-job-system`。
- package source: `src` layout。
- Python version: 3.13。
- test dependency: `pytest`。
- dev dependency: `ruff`。

若不知道使用者 Python 版本，先檢查本機環境再決定。

### Step 2: Package Init Files

新增：

```text
src/djobs/__init__.py
src/djobs/api/__init__.py
src/djobs/core/__init__.py
src/djobs/observability/__init__.py
src/djobs/queue/__init__.py
src/djobs/scheduler/__init__.py
src/djobs/storage/__init__.py
src/djobs/worker/__init__.py
```

### Step 3: Core Types

新增：

```text
src/djobs/core/states.py
src/djobs/core/models.py
src/djobs/core/errors.py
```

建議內容：

- `JobStatus` enum / `StrEnum`。
- `Job` dataclass。
- `InvalidStateTransitionError` exception。
- `validate_transition(from_status, to_status)`。

Phase 1 只允許：

```text
pending -> running
running -> succeeded
running -> failed
```

### Step 4: Smoke Test

新增：

```text
tests/unit/test_imports.py
tests/unit/test_job_state.py
```

測試：

- package import works。
- valid transitions pass。
- invalid transitions raise error。

### Step 5: Run Verification

建議命令：

```powershell
python -m pytest
python -m ruff check .
```

若環境還沒有安裝 dev dependency，先用可用命令驗證，並在回覆中說明未安裝的部分。

## Phase 1 Implementation Plan

狀態：已完成。

目前已完成：

- `migrations/001_initial.sql`。
- `src/djobs/storage/sqlite.py`：SQLite schema initialization、job repository、minimal event log。
- `src/djobs/queue/service.py`：submit / claim / complete / fail。
- `src/djobs/worker/registry.py`：handler registry。
- `src/djobs/worker/runner.py`：`run_once()` worker runner。
- `examples/run_echo_job.py`：echo job demo。
- unit / integration tests：repository、queue service、worker registry、worker runner、SQLite end-to-end flow。

驗證命令：

```powershell
python -m ruff check .
python -m pytest -v
$env:DJOBS_EXAMPLE_DB_PATH="$env:TEMP\djobs_phase1_demo.db"; .\.venv\Scripts\python.exe examples\run_echo_job.py
```

最後驗證結果：ruff 0 errors，pytest 53 passed，echo demo 可跑到 `succeeded` 並產生 `job_created`、`job_claimed`、`job_succeeded` events。

以下是原本 Phase 1 計畫，保留作為歷史紀錄與交付對照。

Phase 1 的目標是單機 durable job queue MVP。

範圍原則：只做單機、同步、SQLite-backed flow。不要做 retry、lease、heartbeat、scheduler、rate limiting 或 distributed coordination。但 repository interface 要保留未來 atomic claim 的空間。

### Step 1: SQLite Schema

新增：

```text
src/djobs/storage/sqlite.py
migrations/001_initial.sql
```

第一版 schema：

```sql
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    run_after TEXT NULL,
    idempotency_key TEXT NULL,
    last_error TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE job_events (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NULL,
    metadata_json TEXT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
```

Phase 1 先記錄 minimal event log：

```text
job_created
job_claimed
job_succeeded
job_failed
```

這不是 event sourcing，只是 audit trail，讓 Phase 6 的 timeline / inspect 可以自然建立在同一份資料上。

### Step 2: Repository

建議 repository methods：

```text
create_job(job)
get_job(job_id)
claim_next_job(worker_id)
mark_succeeded(job_id)
mark_failed(job_id, error)
append_event(job_id, event_type, message, metadata)
```

Phase 1 可以先不處理多 process concurrent workers，但 `claim_next_job` 不要散落成「外部先 select、再外部 update」。應由 repository 包成單一 method，內部用 transaction 做出 atomic claim 的形狀。Phase 7 才把 SQLite 實作替換成 PostgreSQL row lock + `SKIP LOCKED`。

建議 `claim_next_job(worker_id)` 行為：

- 找出最早建立、狀態為 `pending`、且 `run_after IS NULL OR run_after <= now` 的 job。
- 在同一個 repository operation 中更新為 `running`。
- 寫入 `job_claimed` event。
- 回傳 claimed job；若沒有 job，回傳 `None`。

### Step 3: Queue Service

新增：

```text
src/djobs/queue/service.py
```

Queue service 包裝 repository，負責：

- submit job。
- claim job。
- complete job。
- fail job。

Queue service 應負責 lifecycle 語意，不直接暴露 SQL 細節。Phase 1 只處理 `pending -> running -> succeeded/failed`。

### Step 4: Handler Registry

新增：

```text
src/djobs/worker/registry.py
```

建議 API：

```python
registry.register("demo.echo", handler)
registry.get("demo.echo")
```

Handler signature 先簡化：

```python
def handler(payload: dict) -> dict:
    ...
```

Phase 3 再考慮 context、heartbeat、cancellation。

### Step 5: Worker Runner

新增：

```text
src/djobs/worker/runner.py
```

Worker runner 做：

1. claim next job。
2. find handler by job type。
3. execute handler。
4. mark succeeded or failed。
5. repository / queue service 寫入對應 event log。

Phase 1 可以先做 `run_once()`，不要急著做 long-running daemon。

### Step 6: Example

新增：

```text
examples/run_echo_job.py
```

Demo 流程：

1. initialize SQLite db。
2. register `demo.echo` handler。
3. submit a job。
4. run worker once。
5. print final job state。

### Step 7: Tests

建議測試：

```text
tests/unit/test_queue_service.py
tests/unit/test_worker_registry.py
tests/unit/test_worker_runner.py
tests/integration/test_sqlite_job_flow.py
```

測試重點：

- submit job creates pending job。
- worker success marks succeeded。
- worker exception marks failed。
- unknown handler marks failed with useful reason。
- state changes create event log records。
- `claim_next_job` returns `None` when no job is available。
- future `run_after` job is not claimed early。

## Coding Principles

請下一輪遵守：

- 小步提交概念，不要一次塞完整平台。
- 先 domain model，再 storage，再 worker。
- 每個 state mutation 都應該可測。
- Phase 1 不需要 async。
- Phase 1 不需要 retry。
- Phase 1 不需要 scheduler loop。
- Phase 1 不需要 Redis / Kafka。
- Phase 1 不需要 HTTP server。
- 不要把 business handler 和 queue internals 混在一起。
- 每個 job state mutation 都要有 state transition validation 與 event log。

## Phase 2 Implementation Notes

狀態：已完成。

目前已完成：

- `src/djobs/core/retry.py`：`RetryPolicy` 與 exponential backoff。
- `src/djobs/core/states.py`：新增 `retry_scheduled`、`dead_lettered`。
- `src/djobs/core/errors.py`：新增 `RetryableJobError`、`NonRetryableJobError`。
- `src/djobs/storage/sqlite.py`：retry scheduling、retry promotion、DLQ、active idempotency key lookup。
- `migrations/002_active_idempotency_key.sql`。
- `src/djobs/queue/service.py`：`retry_or_dead_letter()`、`promote_due_retries()`。
- `src/djobs/worker/runner.py`：retryable error 進 retry / DLQ，non-retryable error 進 failed。
- `examples/run_retry_job.py`：retry -> promote -> rerun -> succeeded demo。
- unit / integration tests：retry policy、state machine、idempotency、retry scheduling、DLQ、retry promotion、worker retry flow。

驗證命令：

```powershell
python -m ruff check .
python -m pytest -v
$env:DJOBS_RETRY_EXAMPLE_DB_PATH="$env:TEMP\djobs_phase2_retry_demo.db"; .\.venv\Scripts\python.exe examples\run_retry_job.py
```

最後驗證結果：ruff 0 errors，pytest 78 passed，retry demo 可跑到 `succeeded`，事件序列為 `job_created`、`job_claimed`、`retry_scheduled`、`retry_promoted`、`job_claimed`、`job_succeeded`。

## Naming Suggestions

建議使用清楚名稱：

- `JobStatus.PENDING`
- `JobStatus.RUNNING`
- `JobStatus.SUCCEEDED`
- `JobStatus.FAILED`
- `JobRepository`
- `QueueService`
- `WorkerRunner`
- `HandlerRegistry`

## Risks To Watch

### Scope Creep

這個 project 很容易一次想做太多。每輪只選一個可測 slice。

### Fake Distributed Claims

如果還沒做 atomic claim，不要在 README 宣稱支援 distributed workers。

### Missing Event Log

如果沒有 event log，後面 observability story 會變弱。Phase 1 可以先簡單記錄 created / claimed / succeeded / failed。

### Over-Abstraction

不要太早做 plugin framework、DAG engine、multi-backend storage abstraction。先讓單一路徑穩定。

## Suggested Next Response To User Before Phase 3

開始 Phase 3 前，可以回覆：

```text
Phase 0 到 Phase 2 已完成，下一步建議進 Phase 3：加入 lease、visibility timeout、worker heartbeat、expired lease detection 和 stale running job recovery。範圍先鎖在 crash recovery，不碰 scheduler daemon、rate limiter 或 PostgreSQL distributed mode。
```
