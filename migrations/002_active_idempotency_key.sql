CREATE UNIQUE INDEX idx_jobs_active_idempotency_key
ON jobs (idempotency_key)
WHERE idempotency_key IS NOT NULL
  AND status IN ('pending', 'running', 'retry_scheduled');