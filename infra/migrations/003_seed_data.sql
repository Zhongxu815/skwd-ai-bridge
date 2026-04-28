-- =============================================================================
-- Skwd AI — Seed Data (synthetic, for testing the bridge)
-- Migration: 003
-- Description: Inserts ~12 representative messages spanning all 9 types,
--              all 3 priorities, and various agent pairings. Use for
--              testing the bridge before any real agent is wired up.
--
-- DELETION: To remove seed data without dropping the table:
--   DELETE FROM agent_messages WHERE subject LIKE '[SEED]%';
-- =============================================================================

-- ALL seed messages have subjects prefixed with "[SEED]" so they're easy to clean up.

BEGIN;

-- Message 1: Audra delegates a task back to Ollie (task_result)
INSERT INTO agent_messages (
    from_agent, to_agent, message_type, priority, subject, payload,
    requires_response, status
) VALUES (
    'audra',
    'ollie',
    'task_result',
    'p2',
    '[SEED] PR #127 review complete',
    jsonb_build_object(
        'task_id', '00000000-0000-0000-0000-000000000001',
        'outcome', 'completed',
        'summary', 'PR #127 reviewed. 1 rule 5.4 violation flagged. 2 style notes.',
        'artifacts', jsonb_build_array(
            jsonb_build_object('type', 'pr_comment', 'url', 'https://github.com/example/repo/pull/127')
        ),
        'tokens_used', 14800,
        'duration_seconds', 62
    ),
    FALSE,
    'pending'
);

-- Message 2: Audra peer-queries Jules (query)
INSERT INTO agent_messages (
    from_agent, to_agent, message_type, priority, subject, payload,
    requires_response, status
) VALUES (
    'audra',
    'jules',
    'query',
    'p2',
    '[SEED] Have you seen this session-leak pattern recently?',
    jsonb_build_object(
        'question', 'Have you seen this session-leak pattern in recent PRs?',
        'context', jsonb_build_object(
            'pattern_id', 'session_leak_v2',
            'file', 'main.py:142'
        ),
        'urgency', 'soon'
    ),
    TRUE,
    'pending'
);

-- Message 3: Jules responds to Audra (query_response)
-- Note: this references the message above, so we'll set reply_to in a follow-up.
INSERT INTO agent_messages (
    from_agent, to_agent, message_type, priority, subject, payload,
    requires_response, status
) VALUES (
    'jules',
    'audra',
    'query_response',
    'p2',
    '[SEED] Re: session-leak pattern — yes, 3rd occurrence',
    jsonb_build_object(
        'query_id', '00000000-0000-0000-0000-000000000002',
        'answer', 'Yes, this is occurrence #3 in two weeks. Rule 5.4 catches it locally but isn''t enforced pre-commit.',
        'confidence', 'high',
        'supporting_evidence', jsonb_build_array(
            jsonb_build_object('type', 'message_ref', 'id', 'previous-msg-uuid')
        )
    ),
    FALSE,
    'pending'
);

-- Message 4: Jules diagnoses a bug for Dex (debug_diagnosis)
INSERT INTO agent_messages (
    from_agent, to_agent, message_type, priority, subject, payload,
    requires_response, status
) VALUES (
    'jules',
    'dex',
    'debug_diagnosis',
    'p1',
    '[SEED] Nightly digest job failing — locale issue',
    jsonb_build_object(
        'bug_summary', 'Nightly digest job failing intermittently since Apr 20',
        'root_cause', 'Date parsing in digby/renderer.py:88 uses locale-aware strftime; Railway deploy set LC_ALL=C but local dev has en_US.UTF-8',
        'evidence', jsonb_build_array(
            jsonb_build_object('type', 'log_excerpt', 'ref', 'argus-msg-uuid-abc'),
            jsonb_build_object('type', 'reproduction_steps', 'payload', 'LC_ALL=C python -m digby.render --date 2026-04-22')
        ),
        'suggested_fix', jsonb_build_object(
            'approach', 'Replace strftime with isoformat() + manual formatting',
            'files_touched', jsonb_build_array('digby/renderer.py'),
            'line_count_estimate', 3,
            'test_updates_needed', 'Add test case for LC_ALL=C in tests/test_renderer.py'
        ),
        'alternatives_considered', jsonb_build_array(
            jsonb_build_object(
                'approach', 'Set LOCALE env var in Railway deploy config',
                'tradeoff', 'Faster but fragile — same issue will recur on any env without the fix'
            )
        ),
        'confidence', 'high',
        'risk_tier', 'simple',
        'recommended_handoff', 'dex'
    ),
    TRUE,
    'pending'
);

-- Message 5: Ollie escalates to Jay (escalation)
INSERT INTO agent_messages (
    from_agent, to_agent, message_type, priority, subject, payload,
    requires_response, response_deadline, status
) VALUES (
    'ollie',
    'jay',
    'escalation',
    'p1',
    '[SEED] Architectural decision needed: session pooling refactor',
    jsonb_build_object(
        'escalation_reason', 'authority_required',
        'what_i_tried', jsonb_build_array(
            'Reviewed Jules debug_diagnosis on session leaks (3rd occurrence)',
            'Considered patch fix but Jules flagged risk_tier=architectural',
            'Cannot apply at COO level — requires roadmap commitment'
        ),
        'what_i_need', 'Decision: patch the symptom now and refactor pooling later, OR pause feature work for 2-week refactor sprint',
        'context_summary', 'Recurring session leaks indicate connection pooling design is wrong. Three patches will not fix root cause.',
        'supporting_thread_id', '00000000-0000-0000-0000-000000000004'
    ),
    TRUE,
    now() + INTERVAL '24 hours',
    'pending'
);

-- Message 6: Cash sends a notification (notification)
INSERT INTO agent_messages (
    from_agent, to_agent, message_type, priority, subject, payload,
    requires_response, status
) VALUES (
    'cash',
    'jay',
    'notification',
    'p2',
    '[SEED] Weekly burn: $3.42 — under budget',
    jsonb_build_object(
        'event', 'weekly_burn_report',
        'details', jsonb_build_object(
            'period', 'Apr 21-27',
            'actual_spend', 3.42,
            'budget', 25.00,
            'breakdown', jsonb_build_object(
                'audra', 0.84,
                'jules', 0.00,
                'infrastructure', 2.58
            ),
            'trend', 'stable'
        ),
        'references', jsonb_build_array()
    ),
    FALSE,
    'pending'
);

-- Message 7: Lex proposes prioritization (proposal)
INSERT INTO agent_messages (
    from_agent, to_agent, message_type, priority, subject, payload,
    requires_response, status
) VALUES (
    'lex',
    'jay',
    'proposal',
    'p1',
    '[SEED] Recommend: Member CRM next, Content Center after',
    jsonb_build_object(
        'proposal_type', 'roadmap_prioritization',
        'title', 'Sequence Legacy Ops features: Member CRM > Content Center > Finance Agent',
        'rationale', 'Member CRM unblocks Nicole''s daily ops. Content Center is high-leverage but lower urgency. Finance Agent waits for Cash to mature.',
        'alternatives_considered', jsonb_build_array(
            jsonb_build_object(
                'option', 'Content Center first',
                'tradeoff', 'Higher visible polish but Nicole still drowns in member admin'
            )
        ),
        'estimated_cost', '6-8 weeks dev time, $0 incremental tooling',
        'decision_needed_by', '2026-05-01',
        'supporting_thread_id', null
    ),
    TRUE,
    'pending'
);

-- Message 8: Ollie requests approval (approval_required) for high-stakes action
INSERT INTO agent_messages (
    from_agent, to_agent, message_type, priority, subject, payload,
    requires_response, response_deadline, status
) VALUES (
    'ollie',
    'jay',
    'approval_required',
    'p1',
    '[SEED] Approve: deploy Audra prompt refinement v2',
    jsonb_build_object(
        'action_pending', 'apply_audra_prompt_refinement_v2',
        'summary', 'Add pre-commit-hook recommendation to Audra''s output template when she sees rule 5.4 violations',
        'what_approval_unlocks', 'Deploys the refinement immediately; monitor next 10 PRs for impact',
        'reject_consequence', 'Refinement held; pattern will likely recur',
        'supporting_proposal_id', '00000000-0000-0000-0000-000000000007',
        'default_if_no_response', 'hold'
    ),
    TRUE,
    now() + INTERVAL '48 hours',
    'pending'
);

-- Message 9: Ollie delegates a task to Audra (task)
INSERT INTO agent_messages (
    from_agent, to_agent, message_type, priority, subject, payload,
    requires_response, response_deadline, status
) VALUES (
    'ollie',
    'audra',
    'task',
    'p2',
    '[SEED] Review PR #131',
    jsonb_build_object(
        'action_requested', 'review_pr',
        'context', jsonb_build_object(
            'pr_number', 131,
            'repo', 'legacy-pilates-ops',
            'description', 'Refactor member-api authentication middleware'
        ),
        'acceptance_criteria', 'Flag any rule violations + estimate review time',
        'parent_task', null,
        'budget_tokens', 20000
    ),
    TRUE,
    now() + INTERVAL '4 hours',
    'pending'
);

-- Message 10: Argus reports a P0 production incident
INSERT INTO agent_messages (
    from_agent, to_agent, message_type, priority, subject, payload,
    requires_response, response_deadline, status
) VALUES (
    'argus',
    'jay',
    'notification',
    'p0',
    '[SEED] PRODUCTION INCIDENT: API 500 errors spike',
    jsonb_build_object(
        'event', 'production_incident',
        'details', jsonb_build_object(
            'service', 'legacy-pilates-ops',
            'symptom', '500 errors increased from 0.1% to 4.2% in last 5 minutes',
            'started_at', '2026-04-28T14:32:00Z',
            'affected_endpoints', jsonb_build_array('/api/members', '/api/bookings'),
            'suspected_cause', 'session pool exhaustion (matches Jules diagnosis pattern)',
            'auto_remediation', 'Pool size temporarily doubled; monitoring impact'
        ),
        'references', jsonb_build_array(
            jsonb_build_object('type', 'sentry_issue', 'url', 'https://sentry.io/...')
        )
    ),
    TRUE,
    now() + INTERVAL '15 minutes',
    'pending'
);

-- Message 11: Already-acted message (for testing the lifecycle)
-- This one has been processed: status='acted', acted_at populated, with a human approval.
INSERT INTO agent_messages (
    from_agent, to_agent, message_type, priority, subject, payload,
    requires_response, status, human_action, human_note,
    read_at, acted_at
) VALUES (
    'audra',
    'jay',
    'task_result',
    'p2',
    '[SEED] PR #125 approved (historical)',
    jsonb_build_object(
        'task_id', '00000000-0000-0000-0000-000000000011',
        'outcome', 'completed',
        'summary', 'Clean review. No violations.',
        'tokens_used', 8200,
        'duration_seconds', 42
    ),
    FALSE,
    'acted',
    'approved',
    'lgtm',
    now() - INTERVAL '2 days',
    now() - INTERVAL '2 days' + INTERVAL '5 minutes'
);

-- Message 12: A closed thread (for testing the full lifecycle)
INSERT INTO agent_messages (
    from_agent, to_agent, message_type, priority, subject, payload,
    requires_response, status,
    read_at, acted_at, closed_at
) VALUES (
    'audra',
    'ollie',
    'task_result',
    'p2',
    '[SEED] PR #100 reviewed and shipped (closed)',
    jsonb_build_object(
        'task_id', '00000000-0000-0000-0000-000000000012',
        'outcome', 'completed',
        'summary', 'Reviewed, approved, merged.',
        'tokens_used', 7400
    ),
    FALSE,
    'closed',
    now() - INTERVAL '5 days',
    now() - INTERVAL '5 days' + INTERVAL '2 minutes',
    now() - INTERVAL '5 days' + INTERVAL '10 minutes'
);

-- -----------------------------------------------------------------------------
-- Verify by selecting a summary
-- -----------------------------------------------------------------------------
-- Run this after seeding to confirm it worked:
--
--   SELECT message_type, priority, COUNT(*)
--   FROM agent_messages
--   WHERE subject LIKE '[SEED]%'
--   GROUP BY message_type, priority
--   ORDER BY priority, message_type;
--
-- Expected: ~12 rows across 7 message types (task, task_result, query,
--   query_response, debug_diagnosis, escalation, notification, proposal,
--   approval_required), priorities p0/p1/p2.

-- Record migration
INSERT INTO schema_migrations (version, description)
VALUES ('003', 'Seed data: ~12 synthetic messages for bridge testing')
ON CONFLICT (version) DO NOTHING;

COMMIT;
