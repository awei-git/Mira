CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS {schema}.control_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS {schema}.tasks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    origin TEXT NOT NULL,
    quick BOOLEAN NOT NULL DEFAULT FALSE,
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    parent_id TEXT,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    queued_at TEXT,
    started_at TEXT,
    heartbeat_at TEXT,
    completed_at TEXT,
    worker_pid INTEGER,
    workspace TEXT,
    workflow_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 2,
    failure_class TEXT,
    error_code TEXT,
    error_message TEXT,
    retryable BOOLEAN NOT NULL DEFAULT FALSE,
    result_path TEXT,
    result_summary TEXT,
    task_type TEXT,
    verification JSONB,
    outcome_verified BOOLEAN NOT NULL DEFAULT FALSE,
    verification_method TEXT,
    archived_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_updated ON {schema}.tasks(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON {schema}.tasks(user_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_worker_pid ON {schema}.tasks(worker_pid);
CREATE INDEX IF NOT EXISTS idx_tasks_heartbeat ON {schema}.tasks(status, heartbeat_at);
CREATE INDEX IF NOT EXISTS idx_tasks_outcome_verified ON {schema}.tasks(user_id, outcome_verified, updated_at DESC);

CREATE TABLE IF NOT EXISTS {schema}.threads (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_threads_user_updated ON {schema}.threads(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS {schema}.messages (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES {schema}.tasks(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'text',
    content TEXT NOT NULL,
    image_path TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_task_created ON {schema}.messages(task_id, created_at);

CREATE TABLE IF NOT EXISTS {schema}.audit_events (
    event_id BIGSERIAL PRIMARY KEY,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    task_id TEXT,
    workflow_id TEXT,
    user_id TEXT,
    payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_audit_events_ts ON {schema}.audit_events(ts);
CREATE INDEX IF NOT EXISTS idx_audit_events_task_id ON {schema}.audit_events(task_id, event_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_user_id ON {schema}.audit_events(user_id, event_id);

CREATE TABLE IF NOT EXISTS {schema}.task_events (
    id BIGSERIAL PRIMARY KEY,
    task_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT,
    payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_user_id ON {schema}.task_events(user_id, id);
CREATE INDEX IF NOT EXISTS idx_events_task_id ON {schema}.task_events(task_id, id);
