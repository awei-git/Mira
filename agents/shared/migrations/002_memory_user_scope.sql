-- Multi-user scoping for Mira memory tables.
-- Backward-compatible: existing rows default to user_id='ang'.

ALTER TABLE IF EXISTS episodic_memory
    ADD COLUMN IF NOT EXISTS user_id VARCHAR(100) NOT NULL DEFAULT 'ang';

ALTER TABLE IF EXISTS semantic_memory
    ADD COLUMN IF NOT EXISTS user_id VARCHAR(100) NOT NULL DEFAULT 'ang';

ALTER TABLE IF EXISTS thought_stream
    ADD COLUMN IF NOT EXISTS user_id VARCHAR(100) NOT NULL DEFAULT 'ang';

CREATE INDEX IF NOT EXISTS idx_episodic_user ON episodic_memory(user_id);
CREATE INDEX IF NOT EXISTS idx_semantic_user ON semantic_memory(user_id);
CREATE INDEX IF NOT EXISTS idx_thought_user ON thought_stream(user_id);

CREATE INDEX IF NOT EXISTS idx_episodic_user_source ON episodic_memory(user_id, source_type);
CREATE INDEX IF NOT EXISTS idx_semantic_user_source ON semantic_memory(user_id, source_type);
CREATE INDEX IF NOT EXISTS idx_thought_user_type ON thought_stream(user_id, thought_type);
