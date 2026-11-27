-- H.E.R.M.E.S agent communication bus — initial schema (SQLite, WAL).
-- Applied by comms.store.Store.migrate(); never edit an applied migration.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL DEFAULT '',
    task_id TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS idx_conversations_task ON conversations(task_id);
CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at_ms);

CREATE TABLE IF NOT EXISTS conversation_participants (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL,
    joined_at_ms INTEGER NOT NULL,
    PRIMARY KEY (conversation_id, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_participants_agent ON conversation_participants(agent_id);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender TEXT NOT NULL,
    task_id TEXT,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    priority INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending',
    reply_to TEXT,
    depth INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}',
    fingerprint TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_time ON messages(conversation_id, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_messages_sender_time ON messages(sender, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);
CREATE INDEX IF NOT EXISTS idx_messages_reply_to ON messages(reply_to);
CREATE INDEX IF NOT EXISTS idx_messages_fingerprint_time ON messages(fingerprint, timestamp_ms);

CREATE TABLE IF NOT EXISTS message_recipients (
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    delivered_at_ms INTEGER,
    read_at_ms INTEGER,
    PRIMARY KEY (message_id, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_recipients_agent_status ON message_recipients(agent_id, status);
CREATE INDEX IF NOT EXISTS idx_recipients_agent_time ON message_recipients(agent_id, delivered_at_ms);
