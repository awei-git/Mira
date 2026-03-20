-- Mira Persistent Memory Schema
-- pgvector extension must already be enabled (CREATE EXTENSION vector)

-- Episodic memory: conversations, episodes, task results, idle-think, memory overflow
CREATE TABLE IF NOT EXISTS episodic_memory (
    id SERIAL PRIMARY KEY,
    source_type VARCHAR(30) NOT NULL,
    source_id VARCHAR(100),
    title VARCHAR(500),
    content TEXT NOT NULL,
    summary TEXT,
    embedding vector(768),
    tags TEXT[] DEFAULT '{}',
    importance FLOAT DEFAULT 0.5,
    access_count INT DEFAULT 0,
    last_accessed TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    expires_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_episodic_source ON episodic_memory(source_type);
CREATE INDEX IF NOT EXISTS idx_episodic_created ON episodic_memory(created_at DESC);

-- Semantic memory: identity, worldview, interests, reading notes, journal, skills, catalog
CREATE TABLE IF NOT EXISTS semantic_memory (
    id SERIAL PRIMARY KEY,
    source_type VARCHAR(30) NOT NULL,
    source_path VARCHAR(500),
    chunk_index INT DEFAULT 0,
    content TEXT NOT NULL,
    content_hash VARCHAR(12) NOT NULL,
    embedding vector(768),
    importance FLOAT DEFAULT 0.5,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_semantic_source ON semantic_memory(source_type);
CREATE INDEX IF NOT EXISTS idx_semantic_hash ON semantic_memory(content_hash);

-- Thought stream: continuous thinking
CREATE TABLE IF NOT EXISTS thought_stream (
    id SERIAL PRIMARY KEY,
    thought_type VARCHAR(30) NOT NULL,
    content TEXT NOT NULL,
    embedding vector(768),
    parent_id INT REFERENCES thought_stream(id),
    source_context TEXT,
    maturity FLOAT DEFAULT 0.0,
    access_count INT DEFAULT 0,
    tags TEXT[] DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_thought_type ON thought_stream(thought_type);
CREATE INDEX IF NOT EXISTS idx_thought_maturity ON thought_stream(maturity);
CREATE INDEX IF NOT EXISTS idx_thought_parent ON thought_stream(parent_id);

-- IVFFlat vector indexes (require rows to exist for training; created with low lists count)
-- These will be recreated with better parameters once data is loaded
CREATE INDEX IF NOT EXISTS idx_episodic_emb ON episodic_memory
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
CREATE INDEX IF NOT EXISTS idx_semantic_emb ON semantic_memory
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
CREATE INDEX IF NOT EXISTS idx_thought_emb ON thought_stream
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
