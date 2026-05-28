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

## Post-Phase 9 Product Strategy Checkpoint

狀態：建議採用。

核心判斷：目前 djobs 的技術基礎已經足夠支撐 durable queue / MCP / audit trail，但產品價值還需要更尖銳的 use case。下一階段不應急著擴成通用 workflow engine、hosted SaaS 或完整 VS Code extension，而應先證明一個具體痛點：AI coding agent 做 multi-file change 時，中斷後可以可靠恢復。

四角色確認：

- 產品視角：先綁定 codebase migration / multi-file refactor，不再用「通用 queue」當首頁敘事。
- 架構視角：不刪 Postgres、daemon、scheduler、worker pool；但把這些能力降到 advanced / internals，不放在首屏賣點。
- 可靠性視角：要補強 agent 忘記回報、IDE crash、lease 過期、audit evidence 等 failure mode，否則 demo 容易被真實使用打穿。
- 工程管理視角：Phase 10 到 Phase 12 先用低成本驗證市場訊號；VS Code extension、multi-agent orchestration、hosted dashboard 都要等訊號成立後才排期。

後續 phase 的優先順序：

```text
Phase 10  Killer use case demo and README repositioning
Phase 11  Reliability hardening for the killer workflow
Phase 12  Community signal validation
Phase 13  VS Code sidebar MVP, only if signal is good
Phase 14  Multi-agent / hosted dashboard, optional future tracks
```

## Phase 10: Killer Use Case Demo And Positioning

狀態：未開始。

目標：把 djobs 的對外故事從「durable task queue」收斂成「AI coding agent 做多檔重構時，不會因為 IDE / chat 中斷而丟失進度」。

範圍原則：這個 phase 優先改 demo、README 敘事與使用者第一印象，不改核心 queue 架構。先證明 killer use case 能讓人一眼理解，再決定要不要投入 VS Code extension 或 SaaS。

功能 / 交付：

- 新增或改寫一個 codebase migration demo：模擬 agent 對多個檔案執行 docstring / type hint / mechanical refactor。
- demo 流程要清楚展示：enqueue 多個 file-level tasks、完成一部分、模擬中斷、重新啟動後 `resume_session` 找回未完成任務、最後全部完成。
- README 首屏改成痛點導向，不再以「SQLite-backed durable queue」作為第一句賣點。
- README 首屏只主打三個核心操作：`enqueue_task`、`complete_task`、`resume_session`。
- Postgres、scheduler、daemon、worker pool、rate limit 等內容移到 advanced / internals 敘事，不作為首頁主要價值主張。
- 準備 30 秒 demo GIF / asciinema 腳本，主角是「改到一半中斷，重開後接續」。

不做：

- 不刪除 Postgres backend。
- 不刪除 daemon / scheduler / worker pool。
- 不新增 VS Code extension。
- 不新增 hosted dashboard。
- 不做 multi-agent orchestration。

驗證重點：

- 乾淨環境可以用一條指令跑完 demo。
- demo 輸出要讓使用者不用懂 queue theory，也能看出 crash recovery 的價值。
- README 上半頁應優先回答「我為什麼需要這個」，而不是「底層用了什麼」。

面試 / 產品故事：

- 為什麼先從 codebase migration 切入，而不是做通用 job queue。
- 為什麼 file-level task checklist 適合 AI coding agent。
- 為什麼先改定位和 demo，比先做新功能更重要。

## Phase 11: Reliability Hardening For Agent Workflows

狀態：已完成。

目標：補強 Phase 10 killer workflow 的真實可靠性，避免 demo 成功但使用者第一週就遇到 false success、false pending 或無法理解的 stuck task。

範圍原則：只處理 AI agent durable checklist 會直接碰到的 failure modes。不擴成完整 distributed workflow engine。

功能 / 交付：

- 為 `complete_task` 增加 optional evidence / summary 欄位，讓 agent 回報完成任務時可以留下「改了什麼」的佐證。
- audit log 顯示 task 完成 evidence，讓使用者可以回看 AI agent 的實際行為。
- health / inspect 顯示 stuck running tasks，例如 running 時間超過 lease 或超過設定門檻的任務數。
- 文件新增 Failure Modes 說明：agent 忘記 complete、agent 完成但未留下足夠 evidence、IDE crash、MCP process crash、lease expiry 各自會發生什麼。
- 評估 `djobs serve` 作為獨立常駐 daemon 的使用方式，讓 lease recovery 不完全依賴 MCP process 生命週期。

不做：

- 不做 web dashboard。
- 不做 workflow builder。
- 不做團隊權限、登入、遠端同步。
- 不追求 exactly-once execution。

驗證重點：

- agent 完成任務時，audit log 能看見 evidence。
- task 長時間卡在 running 時，CLI / MCP inspect 能明確指出風險。
- 模擬 MCP process 中斷後，重新啟動仍能透過 lease recovery 找回未完成任務。

面試 / 產品故事：

- 為什麼 durable agent runtime 需要 audit evidence，不只是 status flag。
- 為什麼 stuck task 是 UX 問題，不只是 queue implementation detail。
- 為什麼要誠實描述 failure modes，避免把 demo 包裝成不存在的 exactly-once 保證。

## Phase 12: Community Signal Validation

狀態：進行中。

目標：在投入 VS Code extension、multi-agent orchestration 或 SaaS 前，先驗證「AI coding agent 多檔任務中斷恢復」是不是足夠痛的問題。

範圍原則：這是一個產品驗證 phase，預設不寫核心 code。用 Phase 10 的 demo 和 README 去測試市場反應。

交付：

- 發布 Phase 10 demo 到 r/ChatGPTCoding、Hacker News Show、Cursor / Cline / Claude Code 相關社群。
- 固定驗證問題：使用者是否遇過 AI coding agent 改到一半中斷，導致不知道哪些檔案已完成、哪些需要接續。
- 收集 GitHub stars、issues、討論、PyPI downloads、實際安裝與使用回饋。
- 將回饋分類：定位不清、安裝困難、MCP 門檻、缺 UI、缺 review / CI integration、缺 team audit。

通過條件：

- 有明確真實使用者回饋，而不只是泛泛稱讚。
- 至少有人表示願意在自己的 repo 內嘗試 Phase 10 workflow。
- 回饋集中在「想更容易看狀態 / resume / audit」，而不是完全不理解場景。

退出條件：

- 若訊號集中在「MCP 太難裝」或「沒有 UI 不會用」，再進 Phase 13。
- 若訊號集中在「我其實想要 PR review / CI fixer」，回到 Phase 10 重新選 killer use case。
- 若幾乎沒有回饋，不直接做 extension；先重寫 README / demo 或換推廣渠道。

不做：

- 不因為少量好奇 feedback 就直接開發 hosted SaaS。
- 不因為 roadmap 看起來漂亮就投入 multi-agent DAG。
- 不把 stars 當唯一成功指標；真實使用回饋更重要。

## Phase 13: VS Code Sidebar MVP

狀態：條件式，只有 Phase 12 訊號成立後才開始。

目標：把 djobs 從 CLI / MCP tool 提升成使用者看得到的 local-first UI，降低 MCP 使用門檻並強化 resume 體驗。

範圍原則：VS Code extension 是人機介面，不取代 MCP server。第一版只做 task visibility 和少量控制，不做完整 workflow builder。

功能 / 交付：

- VS Code sidebar 顯示目前 workspace correlation_id 下的 tasks。
- task list 顯示 succeeded、running、pending、retry_scheduled、failed、dead_lettered 等狀態。
- 點選 task 可以查看 audit log / evidence / last_error。
- 提供 Resume All 動作：讀取 `resume_session` 結果，協助 agent 接續未完成任務。
- 提供 Cancel / Mark Failed 動作：使用者可中止不想繼續的 task，並寫入 audit trail。
- extension 優先讀取本機 djobs DB 或呼叫本機 djobs CLI，不引入 hosted dependency。

不做：

- 不在 UI 裡建立完整 task graph。
- 不做 multi-agent routing UI。
- 不做登入、團隊 workspace、遠端同步。
- 不要求使用者離開 local-first 模型。

驗證重點：

- 使用者不用看 SQLite / CLI，也能知道 agent 做到哪裡。
- UI 顯示和 MCP / CLI 查到的 task 狀態一致。
- Resume All 行為清楚，不讓使用者誤以為系統會自動修正所有失敗。

面試 / 產品故事：

- 為什麼 MCP 解決 agent integration，但 VS Code extension 解決 user trust。
- 為什麼第一版 UI 只做 visibility，不做 authoring。
- 如何在 local-first、低依賴的前提下改善 developer experience。

## Phase 14: Optional Future Tracks

狀態：不排期，等 Phase 12 / Phase 13 出現明確需求後再拆 phase。

目標：保留 multi-agent orchestration 與 hosted observability 的演進方向，但不讓它們干擾近期 killer use case 驗證。

### Phase 14a: Multi-Agent Orchestration

啟動條件：使用者明確需要多個 AI agent 串接，例如 Agent A 改 code、Agent B 跑測試、失敗後派回 Agent A。

可能範圍：

- task dependency / simplified DAG。
- agent role metadata。
- handoff event log。
- loop guard，避免 agent 互相反覆派工。
- policy：哪些 task 可以自動觸發，哪些需要人工確認。

暫不做原因：

- 會大幅增加 product surface。
- 需要先解決 trust、audit、failure handling。
- 沒有真實使用者前，很容易做成展示漂亮但沒人用的 workflow engine。

### Phase 14b: Hosted Audit Dashboard

啟動條件：出現團隊使用者，且他們需要跨開發者、跨 repo、跨 CI run 查看 AI agent audit trail。

可能範圍：

- optional export / sync。
- hosted dashboard。
- GitHub Actions / CI report integration。
- team-level audit timeline。
- retention、redaction、workspace privacy policy。

暫不做原因：

- 原始碼與 agent 行為紀錄有高度隱私風險。
- auth、multi-tenant、billing、資料保留會把專案推向另一條產品線。
- local-first audit trail 尚未證明前，不應先做 hosted SaaS。

## Roadmap Noise Reduction

狀態：建議下一次 README 更新時執行。

原則：首頁 roadmap 應只保留和 killer workflow 直接相關的項目。通用 queue infra 能力可以存在，但不要讓使用者誤以為 djobs 的主要方向是成為 Celery / Temporal 的替代品。

建議從 README 首頁 roadmap 降級的項目：

- Async worker support。
- Priority queues。
- Web dashboard for audit trail。
- Rate limiting per job type。

這些項目可移到 advanced / internals / possible improvements，等 Phase 12 或 Phase 13 的真實回饋證明需要後再重新排期。
