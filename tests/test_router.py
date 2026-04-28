from __future__ import annotations

from typing import Any

import pytest

from bridge.router import (
    ESCALATION_CHANNEL,
    INFRA_CHANNEL,
    bot_token_for,
    route_channel,
)


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "abc12345",
        "from_agent": "audra",
        "to_agent": "ollie",
        "message_type": "task",
        "priority": "p2",
        "slack_channel": None,
    }
    base.update(overrides)
    return base


class TestRouteChannel:
    def test_override_wins(self) -> None:
        row = _row(slack_channel="#squad-boardroom")
        assert route_channel(row) == "#squad-boardroom"

    def test_override_beats_escalation(self) -> None:
        row = _row(
            slack_channel="#squad-ops",
            to_agent="jay",
            priority="p0",
            message_type="escalation",
        )
        assert route_channel(row) == "#squad-ops"

    @pytest.mark.parametrize("priority", ["p0", "p1"])
    @pytest.mark.parametrize("mtype", ["escalation", "approval_required"])
    def test_escalation_rule(self, priority: str, mtype: str) -> None:
        row = _row(to_agent="jay", priority=priority, message_type=mtype)
        assert route_channel(row) == ESCALATION_CHANNEL

    def test_p2_to_jay_does_not_escalate(self) -> None:
        row = _row(to_agent="jay", priority="p2", message_type="escalation")
        assert route_channel(row) == "#agent-audra"

    def test_escalation_to_non_jay_does_not_match(self) -> None:
        row = _row(to_agent="ollie", priority="p0", message_type="escalation")
        assert route_channel(row) == "#agent-audra"

    def test_wrong_message_type_does_not_escalate(self) -> None:
        row = _row(to_agent="jay", priority="p0", message_type="task")
        assert route_channel(row) == "#agent-audra"

    def test_argus_default(self) -> None:
        row = _row(from_agent="argus", to_agent="ollie", priority="p2")
        assert route_channel(row) == INFRA_CHANNEL

    def test_argus_does_not_block_escalation(self) -> None:
        # Rule ordering: escalation matches before argus rule.
        row = _row(
            from_agent="argus",
            to_agent="jay",
            priority="p0",
            message_type="escalation",
        )
        assert route_channel(row) == ESCALATION_CHANNEL

    def test_default_per_agent(self) -> None:
        assert route_channel(_row(from_agent="jules")) == "#agent-jules"

    def test_p2_to_jay_lands_in_sender_channel(self) -> None:
        # Spec note: "p2 messages to Jay land in the sender's agent channel"
        row = _row(
            from_agent="harper",
            to_agent="jay",
            priority="p2",
            message_type="notification",
        )
        assert route_channel(row) == "#agent-harper"

    def test_empty_string_override_falls_through(self) -> None:
        # Empty string is falsy and should not override
        row = _row(slack_channel="")
        assert route_channel(row) == "#agent-audra"


class TestBotTokenFor:
    def test_present(self) -> None:
        assert bot_token_for("audra", {"audra": "xoxb-1"}) == "xoxb-1"

    def test_missing(self) -> None:
        assert bot_token_for("ollie", {"audra": "xoxb-1"}) is None

    def test_jay_has_no_token_per_clarification(self) -> None:
        assert bot_token_for("jay", {"audra": "xoxb-1"}) is None

    def test_empty_token_dict(self) -> None:
        assert bot_token_for("audra", {}) is None
