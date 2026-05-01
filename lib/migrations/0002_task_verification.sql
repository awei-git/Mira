ALTER TABLE {schema}.tasks
    ADD COLUMN IF NOT EXISTS task_type TEXT,
    ADD COLUMN IF NOT EXISTS verification JSONB,
    ADD COLUMN IF NOT EXISTS outcome_verified BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS verification_method TEXT;

CREATE INDEX IF NOT EXISTS idx_tasks_outcome_verified
    ON {schema}.tasks(user_id, outcome_verified, updated_at DESC);
