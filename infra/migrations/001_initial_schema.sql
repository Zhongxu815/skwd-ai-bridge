-- =============================================================================
-- Skwd AI — Initial Schema
-- Migration: 001
-- Created: April 28, 2026
-- Description: Creates the agent_messages table — source of truth for all
--              agent-to-agent communication in the Squad.
--
-- Idempotent: safe to re-run. All CREATE statements use IF NOT EXISTS.
-- Reversible: see 001_initial_schema.down.sql for the rollback.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Extensions
-- -----------------------------------------------------------------------------
-- pgcrypto provides gen_random_uuid() for UUID v4 generation.
-- Already installed by default on Railway Postgres, but we ensure it.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -----------------------------------------------------------------------------
-- agent_messages: the core messaging table
-- -----------------------------------------------------------------------------
-- Every agent-to-agent communication lives here. Slack is a view on top.
-- All Dashboard surfaces, HR reviews, and Board Room records query this table.
-- See message-schema.md for the full data contract.

CREATE TABLE IF NOT EXISTS agent_messages (
    -- Identity
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id           UUID NOT NULL,
    reply_to            UUID REFERENCES agent_messages(id) ON DELETE SET NULL,

    -- Routing
    from_agent          TEXT NOT NULL,
    to_agent            TEXT NOT NULL,

    -- Classification
    message_type        TEXT NOT NULL,
    priority            TEXT NOT NULL,
    subject             TEXT NOT NULL,
    payload             JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Response expectations
    requires_response   BOOLEAN NOT NULL DEFAULT FALSE,
    response_deadline   TIMESTAMPTZ,

    -- Slack mirror — populated by the bridge after it posts to Slack
    slack_channel       TEXT,
    slack_ts            TEXT,
    slack_thread_ts     TEXT,

    -- Lifecycle timestamps
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_at             TIMESTAMPTZ,
    acted_at            TIMESTAMPTZ,
    closed_at           TIMESTAMPTZ,

    -- Human intervention (Jay's reactions in Slack)
    human_action        TEXT,
    human_note          TEXT,

    -- Status enum (drives inbox queries)
    status              TEXT NOT NULL DEFAULT 'pending',

    -- Constraints (CHECK rather than ENUM for forward-compatibility)
    CONSTRAINT priority_valid CHECK (priority IN ('p0', 'p1', 'p2')),
    CONSTRAINT status_valid CHECK (status IN ('pending', 'read', 'acted', 'closed', 'failed')),
    CONSTRAINT message_type_valid CHECK (message_type IN (
        'task',
        'task_result',
        'query',
        'query_response',
        'proposal',
        'escalation',
        'notification',
        'approval_required',
        'debug_diagnosis'
    )),
    CONSTRAINT human_action_valid CHECK (
        human_action IS NULL OR human_action IN (
            'approved', 'rejected', 'held', 'flagged', 'redirected',
            'annotated', 'escalated', 'de_escalated', 'acknowledged'
        )
    ),

    -- Subject is required and meaningful (not empty)
    CONSTRAINT subject_not_empty CHECK (length(trim(subject)) > 0),

    -- A response deadline only makes sense if a response is required
    CONSTRAINT deadline_implies_response CHECK (
        response_deadline IS NULL OR requires_response = TRUE
    ),

    -- Closed messages must have an acted_at (you can't close without acting)
    CONSTRAINT closed_implies_acted CHECK (
        closed_at IS NULL OR acted_at IS NOT NULL
    )
);

-- -----------------------------------------------------------------------------
-- Indexes
-- -----------------------------------------------------------------------------
-- Most common query: agent fetching its inbox.
-- WHERE to_agent = $1 AND status = 'pending' ORDER BY priority, created_at
CREATE INDEX IF NOT EXISTS idx_agent_messages_inbox
    ON agent_messages (to_agent, status, priority, created_at)
    WHERE status IN ('pending', 'read');

-- Thread fetching: pull all messages in a conversation
CREATE INDEX IF NOT EXISTS idx_agent_messages_thread
    ON agent_messages (thread_id, created_at);

-- Sender history: agent's own activity log (for HR reviews, Agent Detail page)
CREATE INDEX IF NOT EXISTS idx_agent_messages_sender_history
    ON agent_messages (from_agent, created_at DESC);

-- Slack lookup: bridge needs to find a message by its Slack timestamp
-- (when a reaction comes in, we need to map slack_ts -> message id)
CREATE INDEX IF NOT EXISTS idx_agent_messages_slack_ts
    ON agent_messages (slack_ts)
    WHERE slack_ts IS NOT NULL;

-- Stale message detection (Argus SLA monitoring)
-- Find messages that have been pending too long
CREATE INDEX IF NOT EXISTS idx_agent_messages_stale
    ON agent_messages (status, priority, created_at)
    WHERE status = 'pending';

-- Bridge polling: find messages that need to be posted to Slack
-- Messages with no slack_ts haven't been mirrored yet
CREATE INDEX IF NOT EXISTS idx_agent_messages_unposted
    ON agent_messages (created_at)
    WHERE slack_ts IS NULL AND status = 'pending';

-- -----------------------------------------------------------------------------
-- Trigger: automatic thread_id for top-level messages
-- -----------------------------------------------------------------------------
-- If thread_id is not provided AND it's a top-level message (no reply_to),
-- thread_id defaults to the message's own id.
-- This way, any new conversation automatically starts a new thread.

CREATE OR REPLACE FUNCTION set_thread_id_default()
RETURNS TRIGGER AS $$
BEGIN
    -- If this is a reply, inherit the thread from the parent
    IF NEW.reply_to IS NOT NULL AND NEW.thread_id IS NULL THEN
        SELECT thread_id INTO NEW.thread_id
        FROM agent_messages
        WHERE id = NEW.reply_to;
    END IF;

    -- If still no thread_id, use this message's own id (top-level conversation)
    IF NEW.thread_id IS NULL THEN
        NEW.thread_id := NEW.id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_set_thread_id ON agent_messages;
CREATE TRIGGER trg_set_thread_id
    BEFORE INSERT ON agent_messages
    FOR EACH ROW
    EXECUTE FUNCTION set_thread_id_default();

-- -----------------------------------------------------------------------------
-- Trigger: auto-update lifecycle timestamps based on status changes
-- -----------------------------------------------------------------------------
-- When status moves to 'read', set read_at if not already set.
-- When status moves to 'acted', set acted_at if not already set.
-- When status moves to 'closed', set closed_at if not already set.

CREATE OR REPLACE FUNCTION update_lifecycle_timestamps()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status = 'read' AND OLD.status = 'pending' AND NEW.read_at IS NULL THEN
        NEW.read_at := now();
    END IF;

    IF NEW.status = 'acted' AND NEW.acted_at IS NULL THEN
        NEW.acted_at := now();
        -- If we somehow skipped 'read', backfill it
        IF NEW.read_at IS NULL THEN
            NEW.read_at := now();
        END IF;
    END IF;

    IF NEW.status = 'closed' AND NEW.closed_at IS NULL THEN
        NEW.closed_at := now();
        -- Backfill missing earlier timestamps
        IF NEW.acted_at IS NULL THEN
            NEW.acted_at := now();
        END IF;
        IF NEW.read_at IS NULL THEN
            NEW.read_at := now();
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_lifecycle_timestamps ON agent_messages;
CREATE TRIGGER trg_lifecycle_timestamps
    BEFORE UPDATE ON agent_messages
    FOR EACH ROW
    WHEN (OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION update_lifecycle_timestamps();

-- -----------------------------------------------------------------------------
-- Migrations bookkeeping table
-- -----------------------------------------------------------------------------
-- Tracks which migrations have run. Lets us avoid re-running them.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version         TEXT PRIMARY KEY,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    description     TEXT
);

INSERT INTO schema_migrations (version, description)
VALUES ('001', 'Initial schema: agent_messages table with triggers and indexes')
ON CONFLICT (version) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Comments (auto-documentation; visible via psql \d+ and most DB tools)
-- -----------------------------------------------------------------------------

COMMENT ON TABLE agent_messages IS
    'Source of truth for all Squad agent communication. Slack is a view on this table.';

COMMENT ON COLUMN agent_messages.thread_id IS
    'Groups related messages into a conversation. Auto-populated by trigger if not provided.';

COMMENT ON COLUMN agent_messages.reply_to IS
    'Set when this message is a direct reply to another. NULL for top-level messages.';

COMMENT ON COLUMN agent_messages.from_agent IS
    'Sender ID. Lowercase string like "audra", "ollie", "jay". Not foreign-keyed for flexibility.';

COMMENT ON COLUMN agent_messages.to_agent IS
    'Recipient ID. Same format as from_agent.';

COMMENT ON COLUMN agent_messages.payload IS
    'Type-specific structured content. Schema varies by message_type. See message-schema.md.';

COMMENT ON COLUMN agent_messages.priority IS
    'p0 = blocking urgent (within 1hr), p1 = today, p2 = ambient FYI';

COMMENT ON COLUMN agent_messages.slack_ts IS
    'Slack message timestamp. Populated by bridge after successful Slack post. NULL = not yet posted.';

COMMENT ON COLUMN agent_messages.human_action IS
    'Set when Jay reacts in Slack. Drives downstream agent behavior on next inbox read.';

COMMENT ON COLUMN agent_messages.status IS
    'Lifecycle state. pending -> read -> acted -> closed. Or pending -> failed.';
