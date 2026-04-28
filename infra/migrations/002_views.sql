-- =============================================================================
-- Skwd AI — Helper Views
-- Migration: 002
-- Description: Views that wrap the most common queries agents will run.
--              Keeps SQL clean in the bridge code and per-agent code.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- agent_inbox: messages waiting for an agent to process
-- -----------------------------------------------------------------------------
-- Usage:
--   SELECT * FROM agent_inbox WHERE recipient = 'audra' LIMIT 50;
-- Ordered by priority then age. p0 first, oldest within priority first.

CREATE OR REPLACE VIEW agent_inbox AS
SELECT
    id,
    thread_id,
    reply_to,
    from_agent,
    to_agent AS recipient,
    message_type,
    priority,
    subject,
    payload,
    requires_response,
    response_deadline,
    created_at,
    -- Calculated: how old is this message?
    EXTRACT(EPOCH FROM (now() - created_at))::INTEGER AS age_seconds,
    -- Calculated: is it overdue?
    CASE
        WHEN response_deadline IS NULL THEN FALSE
        ELSE response_deadline < now()
    END AS overdue,
    -- Calculated: priority order for sorting (p0 = 0, p1 = 1, p2 = 2)
    CASE priority
        WHEN 'p0' THEN 0
        WHEN 'p1' THEN 1
        WHEN 'p2' THEN 2
    END AS priority_order,
    -- Appended (not inserted mid-list) to satisfy CREATE OR REPLACE VIEW's
    -- "cannot change name of view column" constraint when re-running
    -- against an older deployed view.
    slack_channel
FROM agent_messages
WHERE status = 'pending';

COMMENT ON VIEW agent_inbox IS
    'All pending messages, ready for agents to fetch their inbox. Filter by recipient.';

-- -----------------------------------------------------------------------------
-- agent_thread: full thread of messages on a topic
-- -----------------------------------------------------------------------------
-- Usage:
--   SELECT * FROM agent_thread WHERE thread_id = '...' ORDER BY created_at;
-- Returns the full conversation context for an agent processing a message.

CREATE OR REPLACE VIEW agent_thread AS
SELECT
    id,
    thread_id,
    reply_to,
    from_agent,
    to_agent,
    message_type,
    priority,
    subject,
    payload,
    status,
    human_action,
    human_note,
    created_at,
    read_at,
    acted_at,
    closed_at,
    slack_channel,
    slack_ts
FROM agent_messages;

COMMENT ON VIEW agent_thread IS
    'Full message data for thread reconstruction. Same as agent_messages but explicit.';

-- -----------------------------------------------------------------------------
-- agent_unposted: messages not yet mirrored to Slack
-- -----------------------------------------------------------------------------
-- Usage by the bridge:
--   SELECT * FROM agent_unposted ORDER BY priority_order, created_at LIMIT 10;
-- The bridge polls this view every few seconds.

CREATE OR REPLACE VIEW agent_unposted AS
SELECT
    id,
    thread_id,
    reply_to,
    from_agent,
    to_agent,
    message_type,
    priority,
    subject,
    payload,
    requires_response,
    created_at,
    CASE priority
        WHEN 'p0' THEN 0
        WHEN 'p1' THEN 1
        WHEN 'p2' THEN 2
    END AS priority_order,
    -- Appended (not inserted mid-list) to satisfy CREATE OR REPLACE VIEW's
    -- "cannot change name of view column" constraint. Bridge needs this
    -- to detect routing overrides set at insert time.
    slack_channel
FROM agent_messages
WHERE slack_ts IS NULL
  AND status = 'pending';

COMMENT ON VIEW agent_unposted IS
    'Messages that exist in DB but have not yet been posted to Slack. Bridge consumes this.';

-- -----------------------------------------------------------------------------
-- agent_activity_summary: per-agent message counts
-- -----------------------------------------------------------------------------
-- Usage by the dashboard / Harper's HR view:
--   SELECT * FROM agent_activity_summary;
-- Returns per-agent send/receive counts for the past 7 / 30 / 90 days.

CREATE OR REPLACE VIEW agent_activity_summary AS
WITH sent_counts AS (
    SELECT
        from_agent AS agent,
        COUNT(*) FILTER (WHERE created_at > now() - INTERVAL '7 days') AS sent_7d,
        COUNT(*) FILTER (WHERE created_at > now() - INTERVAL '30 days') AS sent_30d,
        COUNT(*) FILTER (WHERE created_at > now() - INTERVAL '90 days') AS sent_90d
    FROM agent_messages
    GROUP BY from_agent
),
received_counts AS (
    SELECT
        to_agent AS agent,
        COUNT(*) FILTER (WHERE created_at > now() - INTERVAL '7 days') AS received_7d,
        COUNT(*) FILTER (WHERE created_at > now() - INTERVAL '30 days') AS received_30d,
        COUNT(*) FILTER (WHERE created_at > now() - INTERVAL '90 days') AS received_90d
    FROM agent_messages
    GROUP BY to_agent
),
escalated_counts AS (
    SELECT
        from_agent AS agent,
        COUNT(*) FILTER (
            WHERE message_type = 'escalation'
              AND created_at > now() - INTERVAL '30 days'
        ) AS escalations_30d
    FROM agent_messages
    GROUP BY from_agent
)
SELECT
    COALESCE(s.agent, r.agent, e.agent) AS agent,
    COALESCE(s.sent_7d, 0) AS sent_7d,
    COALESCE(s.sent_30d, 0) AS sent_30d,
    COALESCE(s.sent_90d, 0) AS sent_90d,
    COALESCE(r.received_7d, 0) AS received_7d,
    COALESCE(r.received_30d, 0) AS received_30d,
    COALESCE(r.received_90d, 0) AS received_90d,
    COALESCE(e.escalations_30d, 0) AS escalations_30d
FROM sent_counts s
FULL OUTER JOIN received_counts r ON s.agent = r.agent
FULL OUTER JOIN escalated_counts e ON COALESCE(s.agent, r.agent) = e.agent;

COMMENT ON VIEW agent_activity_summary IS
    'Per-agent message volume over rolling windows. Powers HR view and Harper''s reviews.';

-- -----------------------------------------------------------------------------
-- agent_stale_messages: SLA monitoring (Argus consumes this)
-- -----------------------------------------------------------------------------
-- Usage:
--   SELECT * FROM agent_stale_messages;
-- Returns messages exceeding their priority's SLA.

CREATE OR REPLACE VIEW agent_stale_messages AS
SELECT
    id,
    from_agent,
    to_agent,
    message_type,
    priority,
    subject,
    created_at,
    EXTRACT(EPOCH FROM (now() - created_at))::INTEGER AS age_seconds,
    CASE priority
        -- P0: 1 hour for read
        WHEN 'p0' THEN
            CASE WHEN now() - created_at > INTERVAL '1 hour'
                THEN 'p0_unread_over_1h' ELSE NULL END
        -- P1: 1 business day (8 hours rough proxy)
        WHEN 'p1' THEN
            CASE WHEN now() - created_at > INTERVAL '8 hours'
                THEN 'p1_unread_over_1d' ELSE NULL END
        -- P2: 3 days
        WHEN 'p2' THEN
            CASE WHEN now() - created_at > INTERVAL '3 days'
                THEN 'p2_unread_over_3d' ELSE NULL END
    END AS sla_breach
FROM agent_messages
WHERE status = 'pending'
  AND (
    (priority = 'p0' AND now() - created_at > INTERVAL '1 hour') OR
    (priority = 'p1' AND now() - created_at > INTERVAL '8 hours') OR
    (priority = 'p2' AND now() - created_at > INTERVAL '3 days')
  );

COMMENT ON VIEW agent_stale_messages IS
    'Messages that have breached their SLA. Argus monitors this.';

-- -----------------------------------------------------------------------------
-- Record migration
-- -----------------------------------------------------------------------------

INSERT INTO schema_migrations (version, description)
VALUES ('002', 'Helper views: agent_inbox, agent_thread, agent_unposted, activity_summary, stale_messages')
ON CONFLICT (version) DO NOTHING;
