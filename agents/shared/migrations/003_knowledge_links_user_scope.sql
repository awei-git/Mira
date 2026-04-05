-- Multi-user scoping for knowledge links.
-- Backward-compatible: existing rows default to user_id='ang'.

ALTER TABLE IF EXISTS knowledge_links
    ADD COLUMN IF NOT EXISTS user_id VARCHAR(100) NOT NULL DEFAULT 'ang';

CREATE INDEX IF NOT EXISTS idx_kl_user_source
    ON knowledge_links(user_id, source_type, source_id);

CREATE INDEX IF NOT EXISTS idx_kl_user_target
    ON knowledge_links(user_id, target_type, target_id);
