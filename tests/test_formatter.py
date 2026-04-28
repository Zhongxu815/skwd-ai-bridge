"""Tests for bridge.formatter.

Payload fixtures mirror migrations/003_seed_data.sql in skwd-ai-infra.
When the seed data evolves, update these fixtures to match.
"""

from __future__ import annotations

import logging
from typing import Any

from bridge.formatter import ANSWER_MAX, ID_PREFIX_LEN, format_message


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "abc12345-def0-rest",
        "from_agent": "audra",
        "to_agent": "ollie",
        "message_type": "task",
        "priority": "p2",
        "subject": "Test subject",
        "payload": {},
    }
    base.update(overrides)
    return base


def _section_text(blocks: list[dict[str, Any]], idx: int) -> str:
    return str(blocks[idx]["text"]["text"])


def _context_text(block: dict[str, Any]) -> str:
    return str(block["elements"][0]["text"])


# ──────────────────────────────────────────────────────────────────────────
# Block-structure tests
# ──────────────────────────────────────────────────────────────────────────


class TestStructure:
    def test_block_layout(self) -> None:
        blocks = format_message(
            _row(
                subject="Hello",
                message_type="task_result",
                priority="p1",
                to_agent="ollie",
                payload={"summary": "All good."},
            )
        )
        assert [b["type"] for b in blocks] == [
            "section",
            "context",
            "section",
            "divider",
            "context",
        ]
        assert _section_text(blocks, 0) == "*Hello*"
        meta = _context_text(blocks[1])
        assert "task_result" in meta
        assert "p1" in meta
        assert "→" in meta
        assert "ollie" in meta

    def test_id_truncated_in_footer(self) -> None:
        blocks = format_message(_row(id="0123456789abcdef-extra-stuff"))
        footer = _context_text(blocks[-1])
        assert "01234567" in footer
        assert "abcdef-extra-stuff" not in footer
        assert len("0123456789abcdef-extra-stuff"[:ID_PREFIX_LEN]) == ID_PREFIX_LEN

    def test_missing_subject(self) -> None:
        blocks = format_message(_row(subject=None))
        assert _section_text(blocks, 0) == "*(no subject)*"

    def test_missing_message_type_priority_to_agent(self) -> None:
        blocks = format_message(
            _row(message_type=None, priority=None, to_agent=None)
        )
        meta = _context_text(blocks[1])
        assert meta.count("?") >= 3

    def test_missing_id(self) -> None:
        blocks = format_message(_row(id=None))
        assert "(no id)" in _context_text(blocks[-1])

    def test_payload_not_a_mapping_treated_empty(self) -> None:
        blocks = format_message(_row(payload="not a dict"))
        assert "no summary" in _section_text(blocks, 2)

    def test_payload_none_treated_empty(self) -> None:
        blocks = format_message(_row(payload=None))
        assert "no summary" in _section_text(blocks, 2)

    def test_unknown_message_type_falls_back(self) -> None:
        blocks = format_message(
            _row(message_type="unicorn", payload={"foo": "bar"})
        )
        assert "no summary" in _section_text(blocks, 2)


# ──────────────────────────────────────────────────────────────────────────
# Per-message-type summaries (fixtures from 003_seed_data.sql)
# ──────────────────────────────────────────────────────────────────────────


class TestTaskSummary:
    # Seed msg 9: Ollie → Audra, "[SEED] Review PR #131"
    def test_seed_shape(self) -> None:
        blocks = format_message(
            _row(
                message_type="task",
                payload={
                    "action_requested": "review_pr",
                    "context": {
                        "pr_number": 131,
                        "repo": "legacy-pilates-ops",
                        "description": "Refactor member-api authentication middleware",
                    },
                    "acceptance_criteria": "Flag any rule violations + estimate review time",
                    "parent_task": None,
                    "budget_tokens": 20000,
                },
            )
        )
        text = _section_text(blocks, 2)
        assert "PR #131" in text
        assert "Refactor member-api authentication middleware" in text
        assert "legacy-pilates-ops" in text
        assert "review_pr" in text
        assert "Flag any rule violations" in text

    def test_context_without_pr_number(self) -> None:
        blocks = format_message(
            _row(
                message_type="task",
                payload={
                    "context": {"description": "Just a description"},
                    "action_requested": "Do it",
                },
            )
        )
        text = _section_text(blocks, 2)
        assert "Just a description" in text
        assert "PR #" not in text
        assert "Do it" in text

    def test_no_context_falls_back_to_action(self) -> None:
        blocks = format_message(
            _row(
                message_type="task",
                payload={
                    "action_requested": "Run nightly batch.",
                    "acceptance_criteria": "Zero rows in DLQ.",
                },
            )
        )
        text = _section_text(blocks, 2)
        assert "Run nightly batch." in text
        assert "Zero rows in DLQ." in text
        assert "PR #" not in text
        assert "*Action:*" in text

    def test_context_not_a_mapping(self) -> None:
        blocks = format_message(
            _row(
                message_type="task",
                payload={"context": "just a string", "action_requested": "Do it"},
            )
        )
        text = _section_text(blocks, 2)
        assert "Do it" in text
        assert "just a string" not in text

    def test_only_action(self) -> None:
        blocks = format_message(
            _row(message_type="task", payload={"action_requested": "Just do it"})
        )
        text = _section_text(blocks, 2)
        assert "Just do it" in text
        assert "*Criteria:*" not in text

    def test_only_criteria(self) -> None:
        blocks = format_message(
            _row(message_type="task", payload={"acceptance_criteria": "All green"})
        )
        text = _section_text(blocks, 2)
        assert "All green" in text
        assert "*Action:*" not in text

    def test_pr_with_no_repo(self) -> None:
        blocks = format_message(
            _row(
                message_type="task",
                payload={
                    "context": {"pr_number": 42, "description": "Fix it"},
                },
            )
        )
        text = _section_text(blocks, 2)
        assert "PR #42" in text
        assert "Fix it" in text

    def test_empty_payload(self) -> None:
        blocks = format_message(_row(message_type="task", payload={}))
        assert "no summary" in _section_text(blocks, 2)


class TestTaskResultSummary:
    # Seed msg 1
    def test_seed_shape(self) -> None:
        blocks = format_message(
            _row(
                message_type="task_result",
                payload={
                    "task_id": "00000000-0000-0000-0000-000000000001",
                    "outcome": "completed",
                    "summary": "PR #127 reviewed. 1 rule 5.4 violation flagged. 2 style notes.",
                    "tokens_used": 14800,
                    "duration_seconds": 62,
                },
            )
        )
        assert "PR #127 reviewed" in _section_text(blocks, 2)


class TestQuerySummary:
    # Seed msg 2
    def test_seed_shape(self) -> None:
        blocks = format_message(
            _row(
                message_type="query",
                payload={
                    "question": "Have you seen this session-leak pattern in recent PRs?",
                    "context": {"pattern_id": "session_leak_v2", "file": "main.py:142"},
                    "urgency": "soon",
                },
            )
        )
        assert "session-leak pattern" in _section_text(blocks, 2)


class TestQueryResponseSummary:
    # Seed msg 3
    def test_seed_shape(self) -> None:
        blocks = format_message(
            _row(
                message_type="query_response",
                payload={
                    "query_id": "00000000-0000-0000-0000-000000000002",
                    "answer": "Yes, this is occurrence #3 in two weeks. Rule 5.4 catches it locally but isn't enforced pre-commit.",
                    "confidence": "high",
                },
            )
        )
        assert "occurrence #3" in _section_text(blocks, 2)

    def test_truncates_to_200(self) -> None:
        blocks = format_message(
            _row(message_type="query_response", payload={"answer": "x" * 500})
        )
        text = _section_text(blocks, 2)
        assert len(text) <= ANSWER_MAX
        assert text.endswith("…")

    def test_under_limit(self) -> None:
        blocks = format_message(
            _row(message_type="query_response", payload={"answer": "Short."})
        )
        assert _section_text(blocks, 2) == "Short."

    def test_missing_answer(self) -> None:
        blocks = format_message(_row(message_type="query_response", payload={}))
        assert "no summary" in _section_text(blocks, 2)


class TestProposalSummary:
    # Seed msg 7
    def test_seed_shape(self) -> None:
        blocks = format_message(
            _row(
                message_type="proposal",
                payload={
                    "proposal_type": "roadmap_prioritization",
                    "title": "Sequence Legacy Ops features: Member CRM > Content Center > Finance Agent",
                    "rationale": "Member CRM unblocks Nicole's daily ops. Content Center is high-leverage but lower urgency.",
                    "estimated_cost": "6-8 weeks dev time, $0 incremental tooling",
                },
            )
        )
        text = _section_text(blocks, 2)
        assert "Sequence Legacy Ops features" in text
        assert "Member CRM unblocks Nicole" in text

    def test_only_title(self) -> None:
        blocks = format_message(
            _row(message_type="proposal", payload={"title": "Just a title"})
        )
        assert "Just a title" in _section_text(blocks, 2)

    def test_only_rationale(self) -> None:
        blocks = format_message(
            _row(message_type="proposal", payload={"rationale": "Strong rationale"})
        )
        assert "Strong rationale" in _section_text(blocks, 2)


class TestEscalationSummary:
    # Seed msg 5
    def test_seed_shape(self) -> None:
        blocks = format_message(
            _row(
                message_type="escalation",
                payload={
                    "escalation_reason": "authority_required",
                    "what_i_tried": [
                        "Reviewed Jules debug_diagnosis on session leaks (3rd occurrence)",
                        "Considered patch fix but Jules flagged risk_tier=architectural",
                        "Cannot apply at COO level — requires roadmap commitment",
                    ],
                    "what_i_need": "Decision: patch the symptom now and refactor pooling later, OR pause feature work for 2-week refactor sprint",
                    "context_summary": "Recurring session leaks indicate connection pooling design is wrong.",
                },
            )
        )
        text = _section_text(blocks, 2)
        assert "*Need:*" in text
        assert "Decision: patch the symptom" in text
        assert "*Tried:*" in text
        # First item only
        assert "Reviewed Jules debug_diagnosis" in text
        assert "Considered patch fix" not in text
        assert "Cannot apply at COO level" not in text

    def test_only_what_i_need(self) -> None:
        blocks = format_message(
            _row(
                message_type="escalation",
                payload={"what_i_need": "API key for the partner"},
            )
        )
        text = _section_text(blocks, 2)
        assert "API key for the partner" in text
        assert "*Tried:*" not in text

    def test_only_what_i_tried(self) -> None:
        blocks = format_message(
            _row(
                message_type="escalation",
                payload={"what_i_tried": ["Step one"]},
            )
        )
        text = _section_text(blocks, 2)
        assert "Step one" in text
        assert "*Need:*" not in text

    def test_what_i_tried_string_form(self) -> None:
        # Tolerate non-list shapes
        blocks = format_message(
            _row(
                message_type="escalation",
                payload={"what_i_need": "Help", "what_i_tried": "Tried foo"},
            )
        )
        text = _section_text(blocks, 2)
        assert "Tried foo" in text

    def test_what_i_tried_empty_list(self) -> None:
        blocks = format_message(
            _row(
                message_type="escalation",
                payload={"what_i_need": "Help", "what_i_tried": []},
            )
        )
        text = _section_text(blocks, 2)
        assert "*Need:*" in text
        assert "*Tried:*" not in text


class TestNotificationSummary:
    # Seed msg 6 (cash weekly burn)
    def test_seed_shape_weekly_burn(self) -> None:
        blocks = format_message(
            _row(
                message_type="notification",
                payload={
                    "event": "weekly_burn_report",
                    "details": {
                        "period": "Apr 21-27",
                        "actual_spend": 3.42,
                        "budget": 25.00,
                        "trend": "stable",
                    },
                },
            )
        )
        text = _section_text(blocks, 2)
        assert "weekly_burn_report" in text
        # JSON-compact: no spaces between separators
        assert '"period":"Apr 21-27"' in text
        assert '"actual_spend":3.42' in text

    # Seed msg 10 (argus production incident)
    def test_seed_shape_production_incident(self) -> None:
        blocks = format_message(
            _row(
                message_type="notification",
                payload={
                    "event": "production_incident",
                    "details": {
                        "service": "legacy-pilates-ops",
                        "symptom": "500 errors increased from 0.1% to 4.2%",
                        "started_at": "2026-04-28T14:32:00Z",
                    },
                },
            )
        )
        text = _section_text(blocks, 2)
        assert "production_incident" in text
        assert "legacy-pilates-ops" in text

    def test_without_details(self) -> None:
        blocks = format_message(
            _row(message_type="notification", payload={"event": "ping"})
        )
        assert "ping" in _section_text(blocks, 2)

    def test_only_details(self) -> None:
        blocks = format_message(
            _row(
                message_type="notification",
                payload={"details": {"k": "v"}},
            )
        )
        text = _section_text(blocks, 2)
        assert '"k":"v"' in text

    def test_details_string_form(self) -> None:
        blocks = format_message(
            _row(
                message_type="notification",
                payload={"event": "happened", "details": "raw string"},
            )
        )
        text = _section_text(blocks, 2)
        assert "happened" in text
        # json.dumps of a string yields quoted string
        assert '"raw string"' in text

    def test_details_empty_dict_is_falsy(self) -> None:
        blocks = format_message(
            _row(
                message_type="notification",
                payload={"event": "ping", "details": {}},
            )
        )
        text = _section_text(blocks, 2)
        assert "ping" in text
        assert "{}" not in text

    def test_details_truncated_when_long(self) -> None:
        blocks = format_message(
            _row(
                message_type="notification",
                payload={"event": "happened", "details": {"key": "x" * 500}},
            )
        )
        text = _section_text(blocks, 2)
        assert "…" in text

    def test_circular_details_falls_back(self) -> None:
        # json.dumps raises ValueError on circular refs; fall back to str()
        a: dict[str, Any] = {}
        b: dict[str, Any] = {"a": a}
        a["b"] = b
        blocks = format_message(
            _row(
                message_type="notification",
                payload={"event": "circular", "details": a},
            )
        )
        text = _section_text(blocks, 2)
        assert "circular" in text


class TestApprovalRequiredSummary:
    # Seed msg 8
    def test_seed_shape(self) -> None:
        blocks = format_message(
            _row(
                message_type="approval_required",
                payload={
                    "action_pending": "apply_audra_prompt_refinement_v2",
                    "summary": "Add pre-commit-hook recommendation to Audra's output template when she sees rule 5.4 violations",
                    "what_approval_unlocks": "Deploys the refinement immediately",
                    "default_if_no_response": "hold",
                },
            )
        )
        text = _section_text(blocks, 2)
        assert "*Pending:*" in text
        assert "apply_audra_prompt_refinement_v2" in text
        assert "Add pre-commit-hook recommendation" in text

    def test_only_summary(self) -> None:
        blocks = format_message(
            _row(
                message_type="approval_required",
                payload={"summary": "Just a summary"},
            )
        )
        assert "Just a summary" in _section_text(blocks, 2)

    def test_only_pending(self) -> None:
        blocks = format_message(
            _row(
                message_type="approval_required",
                payload={"action_pending": "approve_this"},
            )
        )
        text = _section_text(blocks, 2)
        assert "approve_this" in text
        assert "*Pending:*" in text


class TestDebugDiagnosisSummary:
    # Seed msg 4
    def test_seed_shape(self) -> None:
        blocks = format_message(
            _row(
                message_type="debug_diagnosis",
                payload={
                    "bug_summary": "Nightly digest job failing intermittently since Apr 20",
                    "root_cause": "Date parsing in digby/renderer.py:88 uses locale-aware strftime; Railway deploy set LC_ALL=C but local dev has en_US.UTF-8",
                    "confidence": "high",
                    "risk_tier": "simple",
                    "recommended_handoff": "dex",
                },
            )
        )
        text = _section_text(blocks, 2)
        assert "*Risk:*" in text
        assert "simple" in text
        assert "*Bug:*" in text
        assert "Nightly digest job failing" in text
        assert "*Root cause:*" in text
        assert "locale-aware strftime" in text

    def test_no_risk_tier(self) -> None:
        blocks = format_message(
            _row(
                message_type="debug_diagnosis",
                payload={"bug_summary": "Foo", "root_cause": "Bar"},
            )
        )
        text = _section_text(blocks, 2)
        assert "Foo" in text
        assert "Bar" in text
        assert "*Risk:*" not in text

    def test_only_risk_tier(self) -> None:
        blocks = format_message(
            _row(
                message_type="debug_diagnosis",
                payload={"risk_tier": "architectural"},
            )
        )
        text = _section_text(blocks, 2)
        assert "architectural" in text
        assert "*Risk:*" in text

    def test_only_bug_summary(self) -> None:
        blocks = format_message(
            _row(message_type="debug_diagnosis", payload={"bug_summary": "Foo"})
        )
        text = _section_text(blocks, 2)
        assert "Foo" in text
        assert "*Root cause:*" not in text


# ──────────────────────────────────────────────────────────────────────────
# Truncation
# ──────────────────────────────────────────────────────────────────────────


class TestTruncation:
    def test_one_line_takes_first_line(self) -> None:
        blocks = format_message(
            _row(
                message_type="task",
                payload={"action_requested": "Line 1\nLine 2\nLine 3"},
            )
        )
        text = _section_text(blocks, 2)
        assert "Line 1" in text
        assert "Line 2" not in text

    def test_one_line_truncates_long(self) -> None:
        blocks = format_message(
            _row(message_type="task", payload={"action_requested": "y" * 500})
        )
        assert "…" in _section_text(blocks, 2)

    def test_whitespace_only_yields_no_summary(self) -> None:
        blocks = format_message(
            _row(message_type="task", payload={"action_requested": "   "})
        )
        assert "no summary" in _section_text(blocks, 2)

    def test_non_string_value_coerced(self) -> None:
        blocks = format_message(
            _row(message_type="task_result", payload={"summary": 42})
        )
        assert "42" in _section_text(blocks, 2)


# ──────────────────────────────────────────────────────────────────────────
# WARN log on no-summary fallback
# ──────────────────────────────────────────────────────────────────────────


class TestNoSummaryWarning:
    def test_warn_emitted_with_row_context(self, caplog: Any) -> None:
        caplog.set_level(logging.WARNING)
        format_message(_row(id="abc12345-rest", message_type="task", payload={}))
        warns = [r for r in caplog.records if r.message == "formatter_no_summary"]
        assert len(warns) == 1
        rec = warns[0]
        assert rec.levelno == logging.WARNING
        assert getattr(rec, "row_id") == "abc12345-rest"
        assert getattr(rec, "message_type") == "task"
        expected = list(getattr(rec, "expected_fields"))
        assert "context.pr_number" in expected
        assert "action_requested" in expected

    def test_warn_for_unknown_message_type(self, caplog: Any) -> None:
        caplog.set_level(logging.WARNING)
        format_message(_row(message_type="unicorn", payload={"foo": "bar"}))
        warns = [r for r in caplog.records if r.message == "formatter_no_summary"]
        assert len(warns) == 1
        assert getattr(warns[0], "message_type") == "unicorn"
        assert getattr(warns[0], "expected_fields") == []

    def test_no_warn_when_summary_renders(self, caplog: Any) -> None:
        caplog.set_level(logging.WARNING)
        format_message(
            _row(message_type="task_result", payload={"summary": "All good."})
        )
        warns = [r for r in caplog.records if r.message == "formatter_no_summary"]
        assert warns == []

    def test_no_warn_when_only_one_field_present(self, caplog: Any) -> None:
        # Partial summaries are not bland — warn only fires when text is empty.
        caplog.set_level(logging.WARNING)
        format_message(
            _row(message_type="task", payload={"action_requested": "Just do it"})
        )
        warns = [r for r in caplog.records if r.message == "formatter_no_summary"]
        assert warns == []
