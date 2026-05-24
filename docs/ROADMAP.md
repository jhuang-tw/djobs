# Phased Roadmap

這份 roadmap 把 Distributed Job System 拆成多個可交付 phase。每個 phase 都應該有明確的 demo、測試與 architecture story。

## Phase 0: Project Foundation

狀態：已完成。

目標：讓專案可開發、可測試、可被下一輪 AI 穩定接手。

交付內容：

- Python package structure。
- `pyproject.toml`。
- pytest setup。
- ruff setup。
- basic config。
- basic structured logging。
- smoke tests。
- README 與 docs 補齊。

不做：

- 不做 queue behavior。
- 不做 worker crash recovery。
- 不做 distributed coordination。

面試故事：

- 為什麼先從小而乾淨的 package boundary 開始。
- 為什麼 distributed system project 要先定義 domain model 與 state machine。

## Phase 1: Single-Node Job Queue MVP

狀態：已完成。

目標：做出可跑的 durable job queue。

範圍原則：先做單機、同步、可持久化的最小 queue。不要做 retry policy、lease、heartbeat、rate limiting 或 distributed locking，但 repository interface 要保留未來 atomic claim 的演進空間。

功能：

- create job。
- enqueue job。
- worker poll pending job。
- worker claim job。
- handler registry。
- execute handler。
- update job status。
- SQLite storage。
- minimal event log：記錄 `job_created`、`job_claimed`、`job_succeeded`、`job_failed`。
- basic CLI 或 example script。

State machine：

```text
pending -> running -> succeeded
pending -> running -> failed
```

測試重點：

- job can be created。
- pending job can be claimed。
- successful handler marks job succeeded。
- failing handler marks job failed。
- state changes write minimal event log。
- invalid state transition is rejected。

面試故事：

- Job lifecycle 怎麼設計。
- queue 與 state 是同一張表還是分開。
- worker 怎麼拿任務。
- 為什麼先做 at-least-once 心智模型。
- 為什麼 Phase 1 就先留下 event log，讓後續 observability 能自然長出來。

## Phase 2: Retry, Idempotency, And Dead Letter Queue

狀態：已完成。

目標：加入可靠性核心。

範圍原則：處理「失敗後是否重試」與「重試耗盡後怎麼保留人工介入空間」。不要在這個 phase 做 worker lease、heartbeat、rate limiter 或完整 scheduler。

功能：

- retry policy。
- exponential backoff。
- max attempts。
- failed reason。
- retryable vs non-retryable error。
- idempotency key。
- dead letter queue。
- retry promotion：將到期的 `retry_scheduled` job 轉回 `pending`。
- richer event log：記錄 retry scheduled、retry promoted、dead-lettered。

新增 state：

```text
retry_scheduled
dead_lettered
```

重要設計：

- handler 失敗時，不一定立刻 failed。
- 若 attempt 尚未達上限，轉成 `retry_scheduled`。
- `run_after` 到期後再回到 `pending`。
- 達上限後進入 `dead_lettered`。
- `failed` 表示 non-retryable terminal failure。
- `dead_lettered` 表示 retryable failure 已經耗盡 retry budget，需要人工檢查。

測試重點：

- retry attempt increments。
- backoff time is calculated。
- due retry is promoted back to pending。
- max attempts sends job to DLQ。
- same idempotency key does not create duplicate active job。
- event log records state changes。

面試故事：

- retry storm 怎麼避免。
- duplicate execution 怎麼處理。
- idempotent handler 為什麼重要。
- DLQ 的目的是保留人工介入空間。

## Phase 3: Lease, Heartbeat, And Crash Recovery

狀態：已完成。

目標：讓 worker crash 後 job 能被系統恢復。

功能：

- task lease。
- visibility timeout。
- worker id。
- worker heartbeat。
- expired lease detection。
- stale running job requeue。
- graceful shutdown。

新增欄位：

- `leased_by`。
- `lease_expires_at`。
- `heartbeat_at`。

重要設計：

- worker claim job 時設定 lease。
- worker 執行中定期 heartbeat。
- 若 worker crash，heartbeat 停止。
- recovery loop 找出 expired lease，將 job 轉回 `pending` 或 `retry_scheduled`。

測試重點：

- active lease prevents other worker from claiming job。
- expired lease allows recovery。
- heartbeat extends lease。
- worker shutdown does not lose job。

面試故事：

- lease 和 lock 的差別。
- worker crash 怎麼 recovery。
- visibility timeout 為什麼存在。
- exactly-once 為什麼難。

## Phase 4: Scheduler And Delayed Jobs

狀態：已完成。

目標：加入 delayed job 與 scheduled retry promotion。

範圍原則：先把 time-based job promotion 做穩。Recurring task 容易牽涉 schedule identity、重複觸發與多 scheduler 協調，先列為 optional Phase 4b，不作為 Phase 4 完成條件。

功能：

- scheduler loop。
- delayed job promotion。
- retry promotion。
- optional Phase 4b：simple recurring schedule。
- optional Phase 4b：schedule metadata。

重要設計：

- `run_after <= now` 的 job 才能被 claim。
- scheduler 負責把到期的 scheduled jobs 變成 pending。
- retry promotion 若 Phase 2 已有簡化版，Phase 4 會把它整理成 scheduler loop 的一部分。
- 多 scheduler 時要避免同一筆 schedule 重複觸發，Phase 7 再用 leader election 或 scheduler lock 強化。

測試重點：

- future job is not claimed early。
- due job is promoted。
- retry_scheduled job is promoted when due。
- optional Phase 4b：recurring job creates next occurrence。

面試故事：

- scheduler 如何避免重複觸發。
- delayed retry 與 queue priority 的關係。
- time-based systems 如何測試。

## Phase 5: Concurrency, Backpressure, And Rate Limiting

狀態：已完成。

目標：處理 production pressure。

功能：

- worker concurrency limit。
- queue-level concurrency。
- rate limiting。
- backpressure。
- queue backlog metrics。
- graceful drain。

重要設計：

- worker 不應無限制 claim job。
- queue 可以依 job type 設定 concurrency。
- retry 與 rate limit 要一起考慮，避免失敗後大量重試打爆下游。

測試重點：

- concurrency limit is respected。
- rate limiter delays execution。
- backlog metrics are updated。
- worker can drain before shutdown。

面試故事：

- backlog 變大怎麼辦。
- 下游 API 爆掉時怎麼保護系統。
- worker scaling 的上限在哪。

## Phase 6: Observability

狀態：已完成。

目標：把前面累積的 logs、event log 與 queue state 整理成可操作的 observability，而不是黑盒。

範圍原則：Phase 0 已有 structured logging，Phase 1 已有 minimal event log。Phase 6 不應該才開始記錄事件，而是強化查詢、指標、health 與排障體驗。

功能：

- structured logs polish。
- metrics。
- job event timeline。
- worker health endpoint 或 CLI health command。
- trace id / correlation id。
- queue depth view。

重要設計：

- 每一次 state transition 都寫 event log。
- 每個 job 有 correlation id。
- worker log 包含 worker id、job id、attempt、duration。
- failed job inspect output 要能回答「何時失敗、失敗幾次、最後錯誤是什麼、下一步是 retry 還是人工介入」。

測試重點：

- event log records all transitions。
- metrics expose queue depth。
- failed job includes error details。

面試故事：

- 如何 debug stuck job。
- 如何知道 worker 是否健康。
- 如何追蹤一個 job 從 submit 到完成。
- SLO 與 alert 可以怎麼設計。

## Phase 7: Distributed Mode

狀態：已完成。

目標：從單機演進成真正多 worker、多 process 的系統。

功能：

- PostgreSQL storage。
- docker compose。
- multiple workers。
- transactional claim。
- repository contract tests：SQLite 與 PostgreSQL 需符合相同 repository 行為。
- scheduler coordination：可用 advisory lock 或 leader election，先保持 optional。
- migration scripts。

重要設計：

- claim job 必須是 atomic operation。
- 多 worker 同時 claim 時，只能有一個成功。
- job claim 優先使用 PostgreSQL transaction、row lock 與 `SKIP LOCKED`。
- advisory lock 比較適合 scheduler leader election 或 cross-row coordination，不作為一般 job claim 的第一選擇。

測試重點：

- concurrent workers do not claim same job。
- lock timeout is handled。
- crashed worker lease can recover。
- SQLite / PostgreSQL repository contract tests pass。

面試故事：

- 多 worker 同時 claim job 怎麼避免重複。
- DB transaction isolation 的選擇。
- row lock、`SKIP LOCKED`、lock timeout / deadlock 怎麼處理。
- scale-out 的限制在哪。

## Phase 8: AI Task Platform Demo

狀態：已完成。

目標：把系統包裝成可展示的 AI workload platform。

範圍原則：先展示 AI workload 對 queue system 的特殊壓力：長任務、昂貴 retry、rate limit、成本追蹤與 idempotent side effects。Dependency / DAG 可以保留為 extension，不作為第一版 demo 的必要條件。

功能：

- AI job type examples。
- batch task submission。
- retryable AI call simulation。
- cost / token usage metadata。
- final demo script。
- optional extension：task dependency / simplified DAG。
- optional extension：workflow run。

重要設計：

- AI 任務常常長時間、昂貴、會 rate limited。
- retry 要考慮成本，不只是成功率。
- idempotency 對 AI side effects 很重要，例如寫檔、發通知、更新資料庫。

面試故事：

- 為什麼這像 mini Temporal。
- AI workload 有什麼特別問題。
- 長任務、失敗、重試、成本控制怎麼設計。
- 如何從 MVP 演進成 platform。

## Phase Discipline

每次只做一個小 slice。完成一個 phase 前，不要偷做後面的大功能。若一定要先留欄位或 interface，請在文件中明確標記為 future-ready，而不是宣稱已完成。

## Phase 9: Durable AI Agent Runtime (MCP)

狀態：已完成。

目標：把 job system 包裝成「給 AI agent 用的 durable execution runtime」。解決 Copilot/AI agent 會話中斷就丟失 in-progress 工作的痛點。

範圍原則：利用既有 lease + heartbeat + crash recovery 能力，透過 MCP server 暴露給 VS Code Copilot Chat。不做 HTTP transport 或 standalone 部署。

功能：

- MCP server（stdio transport）暴露 5 個 tool：
  - `enqueue_task`：提交 durable task。
  - `check_task`：檢查 task 狀態。
  - `list_tasks`：按 correlation_id 列出 tasks。
  - `resume_session`：crash recovery 入口，找出未完成的 tasks。
  - `health`：queue 健康狀態。
- `.vscode/mcp.json`：VS Code MCP 整合設定。
- `.agent.md`：Durable Coder agent 規則定義。
- crash recovery demo：模擬中斷後恢復，zero data loss。
- 16 個 MCP tool 單元測試。

重要設計：

- Agent 每次開始對話時先 `resume_session`，找回中斷的工作。
- 用 `correlation_id` = workspace path 跨 session 串聯所有 task。
- 用 `idempotency_key` 防止重複提交。
- MCP server 直接複用 QueueService + SQLiteJobRepository。

面試故事：

- AI agent 最大痛點：session 中斷就丟失工作。
- 為什麼 durable execution（lease + heartbeat + recovery）能解決。
- 與 Temporal / Inngest / Restate 的定位比較。
- 從 queue system 到 agent runtime 的演進路徑。
