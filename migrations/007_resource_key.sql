-- M3: resource locks. A job may declare a resource_key (e.g. a file path).
-- While a job with a given resource_key is RUNNING, no other job with the
-- same resource_key is claimable — guaranteeing two agents never work on the
-- same resource concurrently. NULL means the job holds no resource lock.

-- SQLite
ALTER TABLE jobs ADD COLUMN resource_key TEXT NULL;

-- PostgreSQL (idempotent variant)
-- ALTER TABLE jobs ADD COLUMN IF NOT EXISTS resource_key TEXT NULL;
