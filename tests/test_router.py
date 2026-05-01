from __future__ import annotations

from typing import Any

import pytest

from bridge.router import (
    CHANNEL_BUS,
    CHANNEL_ESCALATIONS,
    CHANNEL_INFRA,
    bot_token_for,
    route_channel,
)


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "abc12345",
        "from_agent": "audra",
        "to_agent": "ollie",
        "message_type": "task_result",
        "priority": "p2",
        "slack_channel": None,
    }
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────────────────
# Rule 1 — explicit override
# ──────────────────────────────────────────────────────────────────────────


class TestRule1Override:
    def test_override_wins_over_default(self) -> None:
        row = _row(slack_channel="#squad-boardroom")
        assert route_channel(row) == "#squad-boardroom"

    def test_override_wins_over_p0(self) -> None:
        # Override beats the P0 backstop — sender may want a war-room channel.
        row = _row(slack_channel="#squad-ops", priority="p0")
        assert route_channel(row) == "#squad-ops"

    def test_override_wins_over_argus_default(self) -> None:
        row = _row(
            from_agent="argus",
            to_agent="ollie",
            slack_channel="#squad-boardroom",
        )
        assert route_channel(row) == "#squad-boardroom"

    def test_empty_string_override_falls_through(self) -> None:
        # Empty string is falsy and should not override.
        row = _row(slack_channel="")
        assert route_channel(row) == CHANNEL_BUS


# ──────────────────────────────────────────────────────────────────────────
# Rule 2 — P0 backstop
# ──────────────────────────────────────────────────────────────────────────


class TestRule2P0Backstop:
    @pytest.mark.parametrize(
        "from_agent,to_agent",
        [
            ("audra", "ollie"),
            ("ollie", "jules"),
            ("jules", "audra"),
            ("cash", "harper"),
        ],
    )
    def test_p0_from_any_to_any_lands_in_escalations(
        self, from_agent: str, to_agent: str
    ) -> None:
        row = _row(
            from_agent=from_agent,
            to_agent=to_agent,
            priority="p0",
            message_type="task",
        )
        assert route_channel(row) == CHANNEL_ESCALATIONS

    def test_p0_from_argus_lands_in_escalations_not_infra(self) -> None:
        # Precedence: rule 2 outranks rule 6. Production-on-fire from Argus
        # must reach Jay, not get buffered alongside routine health pings.
        row = _row(
            from_agent="argus",
            to_agent="ollie",
            priority="p0",
            message_type="notification",
        )
        assert route_channel(row) == CHANNEL_ESCALATIONS

    def test_p0_to_jay_lands_in_escalations(self) -> None:
        # Also catches potential rule-4 fallthrough.
        row = _row(
            from_agent="audra",
            to_agent="jay",
            priority="p0",
            message_type="query",
        )
        assert route_channel(row) == CHANNEL_ESCALATIONS


# ──────────────────────────────────────────────────────────────────────────
# Rule 3 — escalation/approval to Jay
# ──────────────────────────────────────────────────────────────────────────


class TestRule3EscalationOrApprovalToJay:
    @pytest.mark.parametrize("priority", ["p1", "p2"])
    def test_escalation_to_jay_lands_in_escalations(self, priority: str) -> None:
        row = _row(
            from_agent="ollie",
            to_agent="jay",
            priority=priority,
            message_type="escalation",
        )
        assert route_channel(row) == CHANNEL_ESCALATIONS

    @pytest.mark.parametrize("priority", ["p1", "p2"])
    def test_approval_required_to_jay_lands_in_escalations(
        self, priority: str
    ) -> None:
        row = _row(
            from_agent="audra",
            to_agent="jay",
            priority=priority,
            message_type="approval_required",
        )
        assert route_channel(row) == CHANNEL_ESCALATIONS


# ──────────────────────────────────────────────────────────────────────────
# Rule 4 — non-urgent message to Jay → sender's agent channel
# ──────────────────────────────────────────────────────────────────────────


class TestRule4NonUrgentToJay:
    def test_p2_query_to_jay_lands_in_sender_channel(self) -> None:
        row = _row(
            from_agent="audra",
            to_agent="jay",
            priority="p2",
            message_type="query",
        )
        assert route_channel(row) == "#agent-audra"

    def test_notification_to_jay_lands_in_sender_channel(self) -> None:
        # e.g. weekly burn report from Cash
        row = _row(
            from_agent="cash",
            to_agent="jay",
            priority="p2",
            message_type="notification",
        )
        assert route_channel(row) == "#agent-cash"

    def test_task_result_to_jay_lands_in_sender_channel(self) -> None:
        row = _row(
            from_agent="harper",
            to_agent="jay",
            priority="p2",
            message_type="task_result",
        )
        assert route_channel(row) == "#agent-harper"


# ──────────────────────────────────────────────────────────────────────────
# Rule 5 — Jay-initiated DM to a specific agent
# ──────────────────────────────────────────────────────────────────────────


class TestRule5JayInitiated:
    def test_jay_to_audra_lands_in_audra_channel(self) -> None:
        row = _row(
            from_agent="jay",
            to_agent="audra",
            priority="p2",
            message_type="task",
        )
        assert route_channel(row) == "#agent-audra"

    def test_jay_to_ollie_lands_in_ollie_channel(self) -> None:
        row = _row(
            from_agent="jay",
            to_agent="ollie",
            priority="p2",
            message_type="query",
        )
        assert route_channel(row) == "#agent-ollie"


# ──────────────────────────────────────────────────────────────────────────
# Rule 6 — Argus default → infra
# ──────────────────────────────────────────────────────────────────────────


class TestRule6ArgusDefault:
    def test_argus_p2_notification_lands_in_infra(self) -> None:
        row = _row(
            from_agent="argus",
            to_agent="ollie",
            priority="p2",
            message_type="notification",
        )
        assert route_channel(row) == CHANNEL_INFRA

    def test_argus_escalation_to_jay_takes_rule_3(self) -> None:
        # Rule 3 fires before rule 6 — the escalation-to-Jay path beats
        # Argus's default infra routing for non-P0 escalations.
        row = _row(
            from_agent="argus",
            to_agent="jay",
            priority="p1",
            message_type="escalation",
        )
        assert route_channel(row) == CHANNEL_ESCALATIONS


# ──────────────────────────────────────────────────────────────────────────
# Rule 7 — bus default
# ──────────────────────────────────────────────────────────────────────────


class TestRule7BusDefault:
    def test_canonical_audra_to_ollie_task_result(self) -> None:
        # The canonical Audra retrofit case: PR review task_result lands
        # in #squad-bus, not #agent-audra.
        row = _row(
            from_agent="audra",
            to_agent="ollie",
            message_type="task_result",
            priority="p2",
        )
        assert route_channel(row) == CHANNEL_BUS

    @pytest.mark.parametrize(
        "from_agent,to_agent,message_type",
        [
            ("ollie", "audra", "query"),
            ("jules", "harper", "proposal"),
            ("cash", "lex", "notification"),
            ("harper", "audra", "query_response"),
        ],
    )
    def test_various_agent_to_agent_combinations(
        self, from_agent: str, to_agent: str, message_type: str
    ) -> None:
        row = _row(
            from_agent=from_agent,
            to_agent=to_agent,
            message_type=message_type,
            priority="p2",
        )
        assert route_channel(row) == CHANNEL_BUS

    def test_p1_between_two_non_jay_agents_stays_on_bus(self) -> None:
        # P1 alone does not escalate. Only P0 (rule 2) or to_agent='jay'
        # with the right message_type (rule 3) routes to escalations.
        row = _row(
            from_agent="audra",
            to_agent="ollie",
            priority="p1",
            message_type="task",
        )
        assert route_channel(row) == CHANNEL_BUS


# ──────────────────────────────────────────────────────────────────────────
# bot_token_for
# ──────────────────────────────────────────────────────────────────────────


class TestBotTokenFor:
    def test_present(self) -> None:
        assert bot_token_for("audra", {"audra": "xoxb-1"}) == "xoxb-1"

    def test_missing(self) -> None:
        assert bot_token_for("ollie", {"audra": "xoxb-1"}) is None

    def test_jay_has_no_token_per_clarification(self) -> None:
        assert bot_token_for("jay", {"audra": "xoxb-1"}) is None

    def test_empty_token_dict(self) -> None:
        assert bot_token_for("audra", {}) is None
