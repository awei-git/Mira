-- Knowledge Links: explicit relationships between knowledge fragments
-- Enables traversal of related concepts across memory, worldview, skills, notes

CREATE TABLE IF NOT EXISTS knowledge_links (
    id SERIAL PRIMARY KEY,
    source_type VARCHAR(30) NOT NULL,    -- 'memory', 'worldview', 'reading_note', 'skill', 'episode'
    source_id VARCHAR(200) NOT NULL,     -- file path or db ID
    target_type VARCHAR(30) NOT NULL,
    target_id VARCHAR(200) NOT NULL,
    relation VARCHAR(50) NOT NULL,       -- 'supports', 'contradicts', 'extends', 'supersedes', 'related'
    confidence FLOAT DEFAULT 0.5,
    created_at TIMESTAMPTZ DEFAULT now(),
    created_by VARCHAR(50) DEFAULT 'auto'  -- 'auto', 'lint', 'reflect', 'user'
);

CREATE INDEX IF NOT EXISTS idx_kl_source ON knowledge_links(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_kl_target ON knowledge_links(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_kl_relation ON knowledge_links(relation);
