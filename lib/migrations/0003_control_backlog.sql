CREATE TABLE IF NOT EXISTS {schema}.backlog_items (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    task_id TEXT,
    kind TEXT NOT NULL,
    executor TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'medium',
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    claimed_at TEXT,
    completed_at TEXT,
    verification_summary TEXT,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_backlog_user_status ON {schema}.backlog_items(user_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_backlog_executor_status ON {schema}.backlog_items(executor, status, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_backlog_task_kind ON {schema}.backlog_items(task_id, kind);
