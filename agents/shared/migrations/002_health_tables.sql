-- Health monitoring tables
-- All data stays on localhost PostgreSQL, never sent to cloud

-- Time-series vitals and measurements
CREATE TABLE IF NOT EXISTS health_metrics (
    id SERIAL PRIMARY KEY,
    person_id VARCHAR(30) NOT NULL,
    metric_type VARCHAR(50) NOT NULL,
    value FLOAT NOT NULL,
    unit VARCHAR(20) DEFAULT '',
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source VARCHAR(30) DEFAULT 'manual',
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_hm_person_type
    ON health_metrics(person_id, metric_type, recorded_at DESC);

-- Parsed medical checkup reports
CREATE TABLE IF NOT EXISTS health_reports (
    id SERIAL PRIMARY KEY,
    person_id VARCHAR(30) NOT NULL,
    report_date DATE NOT NULL,
    report_type VARCHAR(50) DEFAULT 'annual_checkup',
    source_file VARCHAR(500),
    parsed_json JSONB NOT NULL DEFAULT '{}',
    summary TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_hr_person
    ON health_reports(person_id, report_date DESC);

-- Self-reported notes: symptoms, medications, observations
CREATE TABLE IF NOT EXISTS health_notes (
    id SERIAL PRIMARY KEY,
    person_id VARCHAR(30) NOT NULL,
    note_date DATE NOT NULL DEFAULT CURRENT_DATE,
    category VARCHAR(50) DEFAULT 'general',
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_hn_person
    ON health_notes(person_id, note_date DESC);
