-- Health insights table — stores daily insights, weekly reports, alerts
-- Source of truth for all generated health content (bridge is just a cache)

CREATE TABLE IF NOT EXISTS health_insights (
    id SERIAL PRIMARY KEY,
    person_id VARCHAR(30) NOT NULL,
    insight_date DATE NOT NULL,
    insight_type VARCHAR(30) NOT NULL DEFAULT 'daily',  -- 'daily', 'weekly', 'alert'
    content TEXT NOT NULL,
    model VARCHAR(50) DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_hi_person_date_type
    ON health_insights(person_id, insight_date, insight_type);
