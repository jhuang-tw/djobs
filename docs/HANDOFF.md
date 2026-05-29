# AI Handoff Notes

這份文件是寫給下一個 AI 或下一輪開發接手用的。請先讀完再動手。

## User Intent

使用者想把這個 side project 作為 backend / infra / distributed systems 的作品集主軸。主題是 Distributed Job System，方向接近 mini Temporal、mini Airflow、mini Durable Functions，但不需要一開始做成完整產品。

使用者特別在意：

- 能分 phase 完成，不要一次做爆。
- 每個 phase 都能累積面試可講的 architecture story。
- 文件要詳細，讓下一個 AI 能承接。
- 文件以中文為主。

## Current Repository State

目前專案根目錄是：

```text
c:\src\my\distributed-job-system
```

目前已有資料夾骨架：

```text
docker/
examples/
migrations/
scripts/
src/djobs/api/
src/djobs/core/
src/djobs/observability/
src/djobs/queue/
src/djobs/scheduler/
src/djobs/storage/
src/djobs/worker/
tests/integration/
tests/unit/
```

Phase 0–Phase 7 已完成。目前已具備：

- `.venv`，使用 Python 3.13。
- `pyproject.toml`，採用 `src` layout、hatchling、pytest、ruff。
- `src/djobs` package 與各子模組 `__init__.py`。
- `src/djobs/core/config.py`：dataclass + environment variables。
- `src/djobs/observability/logging.py`：JSON / text structured logging helper。
- `src/djobs/core/models.py`：`Job` dataclass。
- `src/djobs/core/states.py`：`JobStatus` 與 Phase 1 state transition validator。
- `src/djobs/core/errors.py`：domain errors。
- `src/djobs/storage/sqlite.py`：SQLite schema initialization、job repository、minimal event log。
- `migrations/001_initial.sql`：Phase 1 SQLite schema。
- `src/djobs/queue/service.py`：submit / claim / complete / fail。
- `src/djobs/worker/registry.py`：handler registry。
- `src/djobs/worker/runner.py`：`run_once()` worker runner。
- `examples/run_echo_job.py`：echo job demo。
- `src/djobs/core/retry.py`：`RetryPolicy` 與 exponential backoff。
- `src/djobs/core/states.py`：`retry_scheduled`、`dead_lettered`。
- `src/djobs/core/errors.py`：`RetryableJobError`、`NonRetryableJobError`。
- `src/djobs/storage/sqlite.py`：retry scheduling、retry promotion、DLQ、active idempotency key lookup。
- `migrations/002_active_idempotency_key.sql`：active idempotency key unique index。
- `examples/run_retry_job.py`：retry -> promote -> rerun -> succeeded demo。
- `src/djobs/scheduler/scheduler.py`：`SchedulerLoop`（`tick()` 單次 + `run_loop()` 持續）、`TickResult`。`tick()` 每週期執行三件事：promote due retries、recover expired leases、reap stale agents。
- `examples/run_scheduler_demo.py`：crash recovery + retry promotion 完整 demo。
- `src/djobs/worker/pool.py`：`WorkerPool`（`max_concurrent` + `ThreadPoolExecutor` + graceful drain）。
- `src/djobs/storage/sqlite.py`：`count_by_status`、`count_running_by_type`、per-type `claim_next_job` 並行上限、`busy_timeout`。
- `src/djobs/queue/service.py`：`backlog()`、`count_running_by_type()`、`claim` 支援 `type_concurrency_limits`。
- `examples/run_pool_demo.py`：WorkerPool 並行控制 + graceful drain demo。
- `src/djobs/core/models.py`：`correlation_id`、`started_at` 欄位。
- `src/djobs/observability/metrics.py`：`MetricsCollector`（counters + gauges + snapshot）。
- `src/djobs/observability/inspect.py`：`inspect_job` job 檢查摘要。
- `src/djobs/queue/service.py`：`inspect()`、`health()`、`submit` 支援 `correlation_id`。
- `src/djobs/worker/pool.py`：每個 job 執行 duration logging。
- `migrations/004_observability_columns.sql`：`correlation_id`、`started_at` 欄位。
- unit / integration tests：含 metrics、observability inspect、correlation id、health、started_at、full lifecycle 整合測試。
- `src/djobs/storage/events.py`：`JobEvent` 共用 dataclass（從 sqlite.py 抽出）。
- `src/djobs/storage/postgres.py`：`PostgresJobRepository`（`SELECT ... FOR UPDATE SKIP LOCKED` 原子 claim）。
- `docker/docker-compose.yml`：PostgreSQL 16 服務。
- `migrations/005_postgres_schema.sql`：PostgreSQL 專用 schema（TIMESTAMPTZ）。
- `pyproject.toml`：`pg` optional dependency（`psycopg[binary]>=3.1`）。
- `tests/integration/test_repository_contract.py`：16 個 contract tests，SQLite 與 PostgreSQL 共用，無 PG 時自動 skip。

最後驗證結果：`python -m ruff check .` 通過，`python -m pytest` 162 passed + 16 skipped（PG tests skip when no PostgreSQL）。

Phase 8 已完成：

- `src/djobs/api/ai_handlers.py`：AI handler 模擬（summarize、classify、generate）。
- `src/djobs/queue/service.py`：`submit_batch()` 批次提交。
- `examples/run_ai_demo.py`：AI platform demo（batch submit + cost tracking）。
- `tests/unit/test_ai_handlers.py`、`tests/integration/test_ai_platform.py`。

驗證結果：`python -m pytest` 172 passed + 16 skipped。

Phase 9 已完成：

- `src/djobs/mcp_server.py`：MCP server（stdio transport），暴露 5 個 tool（enqueue_task、check_task、list_tasks、resume_session、health）。
- `.vscode/mcp.json`：VS Code MCP 整合設定。
- `.agent.md`：Durable Coder agent 規則定義。
- `examples/run_durable_demo.py`：crash recovery demo（enqueue → 部分處理 → 模擬 crash → resume → 完成）。
- `pyproject.toml`：`mcp` optional dependency（`mcp[cli]>=1.0`）。
- `tests/unit/test_mcp_server.py`：MCP tool 單元測試。

驗證結果：`python -m ruff check .` 通過，`python -m pytest` 188 passed + 16 skipped。

## Multi-Agent 演進（M1–M5，已完成）

Phase 9 之後完成了 multi-agent orchestration（對應 ROADMAP Phase 14a），讓多個 AI agent 可以安全共用同一個 queue：

- **M1 共用 queue**：`claim_task`（原子租用）、`heartbeat_task`（續租）、`release_task`（歸還）。
- **M2 task dependency**：`enqueue_task` 的 `depends_on`，依賴全部 succeeded 後才會被 claim。
- **M3 resource lock**：`enqueue_task` 的 `resource_key`，同一 key 同時只跑一個 task。
- **M4 agent registry**：`register_agent` / `agent_heartbeat` / `list_agents`，超時未 heartbeat 自動標記 OFFLINE。
- **M5 web dashboard**：`djobs dashboard`（stdlib HTTP server，唯讀），顯示 queue health、agent fleet、active tasks，預設 http://127.0.0.1:8787。

MCP server 目前共暴露 **14 個 tool**（核心 8 個 + multi-agent 6 個）。`src/djobs/dashboard.py` 為新模組；`migrations/006`–`008` 涵蓋 lease / dependency / resource_key / agents 等欄位。

最新驗證：`python -m ruff check .` 通過，`python -m pytest` 271 passed + 16 skipped。

## Next AI Should Do First

Phase 0–9 與 multi-agent M1–M5 已全部完成。剩餘可選方向：

- HTTP SSE transport 讓 MCP server 可遠端使用。
- agent role-based routing / 自動派工 policy。
- Kubernetes Job backend。

## Do Not Do Yet

請先不要做這些，避免 scope creep：

- 不要一開始做 Web UI。
- 不要一開始接 Redis、Kafka、Temporal、Celery。
- 不要一開始做 Kubernetes。
- 不要一開始做完整 DAG engine。
- 不要一開始追求 exactly-once。

## Recommended Technology Choices

初版建議：

- Language: Python。
- Storage: SQLite for Phase 1 到 Phase 3。
- Tests: pytest。
- Lint / format: ruff。
- CLI: argparse 或 typer。若想少依賴，先用 argparse。
- DB access: 先用 standard library `sqlite3`，等 schema 複雜再評估 SQLAlchemy。
- Config: dataclass + environment variables。

理由：這個 project 的價值在 distributed systems design，不在 framework novelty。越少依賴，越容易展示核心概念。

## First Implementation Slice

如果下一個 AI 要開始寫程式碼，建議第一批檔案：

```text
src/djobs/core/models.py
src/djobs/storage/sqlite.py
src/djobs/queue/service.py
src/djobs/worker/runner.py
tests/unit/test_sqlite_lease.py
tests/unit/test_queue_service_lease.py
tests/integration/test_sqlite_crash_recovery_flow.py
```

第一批不要寫 scheduler daemon、rate limiter 或 PostgreSQL。先讓 claim -> lease -> heartbeat -> expired lease recovery 的 flow 可測。

## Suggested Initial Domain Model

Job 欄位先保持簡單：

- `id`: unique job id。
- `type`: handler type，例如 `send_email`、`ai_summarize`。
- `payload`: JSON payload。
- `status`: `pending`、`running`、`succeeded`、`failed`。
- `attempt`: current attempt number。
- `max_attempts`: maximum retry count。
- `created_at`: create time。
- `updated_at`: update time。
- `run_after`: delayed execution time，Phase 1 可先保留欄位但不做完整 scheduler。
- `idempotency_key`: Phase 2 使用，Phase 1 可以先保留概念。

## Suggested State Machine

Phase 1 先支援：

```text
pending -> running -> succeeded
pending -> running -> failed
```

Phase 2 再加入：

```text
running -> retry_scheduled -> pending
running -> dead_lettered
```

Phase 3 再加入 lease recovery：

```text
running -> pending
```

這個 transition 只能在 lease expired 或 worker lost 時發生，不應該被一般 handler 任意呼叫。

## Definition Of Done For Phase 0

狀態：已完成。

Phase 0 完成條件：

- 可以執行 `pytest`。
- package 可以從 `src` layout 正常 import。
- README 與 docs 描述沒有誇大已完成能力。
- 有最小測試確保環境正確。
- 沒有引入不必要的 heavy dependency。

## Definition Of Done For Phase 1

狀態：已完成。

Phase 1 完成條件：

- 可以 submit job。
- job 會持久化到 SQLite。
- worker 可以 claim pending job。
- worker 可以執行 registered handler。
- 成功時 job 變成 `succeeded`。
- 失敗時 job 變成 `failed`，並保存 error reason。
- 每個 Phase 1 state mutation 都寫入 minimal event log。
- `claim_next_job` 以 repository method 包住，保留未來 atomic claim 演進空間。
- worker runner 有 `run_once()`。
- 至少有 unit tests 覆蓋 state transition 與 repository。
- 至少有一個 example 可以跑完一個 demo job。

## Definition Of Done For Phase 2

狀態：已完成。

Phase 2 完成條件：

- retryable handler failure 會進入 `retry_scheduled`。
- non-retryable handler failure 會進入 `failed`。
- retry exhausted 會進入 `dead_lettered`。
- `run_after` 到期後可以 promote 回 `pending`。
- active idempotency key 不會建立重複 active job。
- event log 會記錄 retry scheduled、retry promoted、dead-lettered。
- retry demo 可以跑完 retry -> promote -> succeeded。

## Communication Style For Future AI

請在回覆使用者時：

- 用繁體中文。
- 簡潔講目前完成了什麼。
- 明確說下一步建議。
- 不要一次丟太大的 implementation plan。
- 若修改檔案，最後列出主要檔案與驗證方式。
