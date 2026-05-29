# Architecture Notes

這份文件描述目標架構與模組邊界。請注意，目前多數內容是設計方向，不代表已經實作完成。

## System Overview

Distributed Job System 的核心流程：

```text
Client / API
  -> create job
  -> persist job
  -> enqueue pending job
  -> worker claims job
  -> worker executes handler
  -> update state
  -> write event log
  -> expose logs / metrics / timeline
```

初版採用單機 SQLite，後續再演進到 PostgreSQL 與多 worker。

## Core Concepts

### Job

Job 是一個 durable unit of work。

Job 應該包含：

- identity: `id`。
- routing: `type`。
- input: `payload`。
- state: `status`。
- retry: `attempt`、`max_attempts`。
- time: `created_at`、`updated_at`、`run_after`。
- safety: `idempotency_key`。
- execution: `leased_by`、`lease_expires_at`，Phase 3 開始需要。
- coordination: `depends_on`（依賴的 job id 列表，全部 succeeded 後才可被 claim）、`resource_key`（同一 key 同時只跑一個 task），multi-agent（M2 / M3）開始。
### Handler

Handler 是真正執行 job 的 function 或 class。

Handler 設計原則：

- 盡量 idempotent。
- 不直接改 job table，透過 worker / queue service 回報結果。
- 錯誤要能被分類為 retryable 或 non-retryable。
- 長任務要能配合 heartbeat，Phase 3 開始。

### Worker

Worker 負責：

- 從 queue claim job。
- 找到對應 handler。
- 執行 handler。
- 更新 job status。
- 寫入 event log。
- 維護 heartbeat，Phase 3 開始。
- graceful shutdown，Phase 3 到 Phase 5 強化。

### Queue

Queue 不是單純 in-memory list，而是「可持久化、可被 recovery 的 job selection mechanism」。

Queue service 負責：

- enqueue。
- claim next job。
- complete job。
- fail job。
- schedule retry。
- move to DLQ。
- recover expired lease。

### Storage

Storage 是 durable source of truth。

Phase 1 到 Phase 3：SQLite。

Phase 7：PostgreSQL。

Storage 要避免讓其他模組直接寫 SQL。建議透過 repository 或 storage adapter 封裝。

### Event Log

Event log 是 observability 與 debugging 的基礎。

Phase 1 先建立 minimal event log，只記錄核心 lifecycle 事件。Phase 2 之後再加入 retry / DLQ 事件，Phase 6 則把 event log 整理成 timeline、inspect command 與 metrics 來源。

每次重要事件都應記錄：

- job created。
- job claimed。
- job started。
- job succeeded。
- job failed。
- retry scheduled。
- job dead-lettered。
- lease expired。
- job recovered。

Event log 不一定是 event sourcing。初版可以只是 audit trail。

### Agent（multi-agent，已實作）

Agent 代表一個共用同一個 queue 的工作者（通常是一個 AI coding agent）。

Agent registry 負責：

- `register_agent`：註冊 / 更新 agent（capabilities + metadata），標記 ONLINE。
- `agent_heartbeat`：維持 agent ONLINE。
- `list_agents`：列出 agent，可依狀態過濾。
- 超時未 heartbeat 的 agent 會被標記為 OFFLINE：`SchedulerLoop.tick()` 每個週期主動呼叫 `reap_stale_agents`（不再只在 `list_agents` / dashboard 讀取時才 lazy 回收）。

搭配 Job 的 `leased_by` / `lease_expires_at`，多個 agent 可以安全共用同一個 DB：`claim` 透過原子租用確保同一個 task 不會被兩個 agent 同時領取。

### Dashboard（已實作，唯讀）

Dashboard 是給人看的 local-first 唯讀檢視，不參與排程決策。

- `djobs dashboard` 啟動 stdlib HTTP server（預設 http://127.0.0.1:8787）。
- 顯示 queue health、agent fleet（ONLINE / OFFLINE）、active tasks 與其 lease holder。
- `GET /api/state` 提供 JSON snapshot。
- 無新依賴；agent 不會自己啟動 dashboard。
- **安全性**：dashboard 沒有任何驗證機制，預設只綁定 `127.0.0.1`。請勿綁定到 `0.0.0.0` 或對外網路；遠端存取請改用 SSH tunnel。綁到非 loopback 位址時會印出警告。

## Module Boundaries

### `src/djobs/core`

放 domain model 與規則。

適合包含：

- Job dataclass / model。
- JobStatus enum。
- state transition validator。
- retry policy dataclass。
- domain exceptions。
- `Agent` dataclass 與 `AgentStatus` enum（multi-agent）。

不應包含：

- SQL。
- worker loop。
- CLI parsing。

### `src/djobs/storage`

放 persistence adapter。

適合包含：

- SQLite connection helper。
- schema initialization。
- job repository。
- event repository。

不應包含：

- handler execution。
- retry policy business decisions，除非只是儲存欄位。

### `src/djobs/queue`

放 queue operations 與 job lifecycle coordination。

適合包含：

- enqueue job。
- claim next job。
- mark succeeded。
- mark failed。
- schedule retry。
- DLQ transition。
- lease recovery。

不應包含：

- 具體 handler business logic。
- HTTP routes。

### `src/djobs/worker`

放 worker runtime。

適合包含：

- worker loop。
- handler registry。
- execution result mapping。
- heartbeat loop。
- shutdown handling。

不應包含：

- raw SQL。
- scheduler policy。

### `src/djobs/scheduler`

放 time-based promotion。

適合包含：

- delayed job promotion。
- recurring job expansion。
- retry due job promotion。

不應包含：

- handler execution。

### `src/djobs/observability`

放 logs、metrics、timeline helpers。

適合包含：

- structured logging config。
- metrics collector interface。
- event timeline formatter。
- correlation id utilities。

不應包含：

- job state mutation business logic。

### `src/djobs/api`

放對外入口。

初版可以是 CLI，後續才加 HTTP API。

適合包含：

- submit command。
- worker command。
- inspect job command。
- list queue command。

## State Machine

### Phase 1 Minimal State Machine

```text
pending -> running -> succeeded
pending -> running -> failed
```

規則：

- 只有 pending job 可以被 claim 成 running。
- running job 可以 succeeded 或 failed。
- succeeded 是 terminal state。
- failed 在 Phase 1 可以是 terminal state。

### Phase 2 Retry State Machine

```text
pending -> running -> succeeded
pending -> running -> retry_scheduled -> pending
pending -> running -> dead_lettered
pending -> running -> failed
```

規則：

- retryable failure 且 attempt 未達上限時，進入 `retry_scheduled`。
- retry due 後回到 `pending`。
- attempt 達上限後進入 `dead_lettered`。
- non-retryable failure 可以直接 `failed` 或 `dead_lettered`，需在 Phase 2 決定。

### Phase 3 Lease Recovery

```text
running -> pending
```

規則：

- 只有 lease expired / worker stale 時可以發生。
- 必須寫 event log，否則很難 debug duplicate execution。
- 這代表系統語義是 at-least-once，不是 exactly-once。

## Data Model Draft

Phase 1 可以先用一張 `jobs` 表與一張 `job_events` 表。

`jobs` draft：

```text
id TEXT PRIMARY KEY
type TEXT NOT NULL
payload_json TEXT NOT NULL
status TEXT NOT NULL
attempt INTEGER NOT NULL DEFAULT 0
max_attempts INTEGER NOT NULL DEFAULT 1
run_after TEXT NULL
idempotency_key TEXT NULL
last_error TEXT NULL
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
```

Phase 3 再加入：

```text
leased_by TEXT NULL
lease_expires_at TEXT NULL
heartbeat_at TEXT NULL
```

`job_events` draft：

```text
id TEXT PRIMARY KEY
job_id TEXT NOT NULL
event_type TEXT NOT NULL
message TEXT NULL
metadata_json TEXT NULL
created_at TEXT NOT NULL
```

## Delivery Semantics

初版應明確採用 at-least-once delivery。

意思是：

- job 可能被執行超過一次。
- 系統會盡力避免重複 claim。
- crash recovery 可能讓同一 job 被重新執行。
- handler 必須考慮 idempotency。

不要宣稱 exactly-once。真正 exactly-once 需要非常嚴格的外部 side effect 協調，通常不實際。

## Failure Handling Philosophy

失敗不應只是一個 boolean。

需要分辨：

- handler business failure。
- transient infrastructure failure。
- worker crash。
- timeout。
- poison message。
- downstream rate limit。

Phase 1 可以先簡化為 success / failure。Phase 2 開始再分類 retryable / non-retryable。

## Observability Philosophy

每個 job 都要能回答：

- 它什麼時候被建立？
- 被哪個 worker claim？
- 跑了幾次？
- 每次 attempt 發生什麼？
- 現在卡在哪個 state？
- 最後一次錯誤是什麼？

這些答案應來自 event log、job table、structured logs，而不是憑記憶或 console print。

## Future Distributed Design

PostgreSQL distributed mode 時，claim job 應該是 atomic。

可能策略：

- `SELECT ... FOR UPDATE SKIP LOCKED`。
- single `UPDATE ... WHERE id = (...) RETURNING *`。
- advisory lock for scheduler leader。
- transaction per claim。

需要避免：

- 先 select pending job，再單獨 update，導致 race condition。
- worker local memory 作為 source of truth。
- 沒有 lease 的 long-running job。
