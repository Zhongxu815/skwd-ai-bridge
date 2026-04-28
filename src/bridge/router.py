"""Channel routing rules + bot token lookup.

Pure functions — no IO. The poller calls these to decide where each row
goes and which bot identity to post as.

Routing rules from the spec, evaluated in order, first match wins:
  0. slack_channel override (if set on the row at insert time) wins.
  1. to_agent='jay' AND priority IN (p0, p1) AND
     message_type IN (escalation, approval_required) → #squad-escalations
  2. from_agent='argus'                              → #squad-infra
  3. otherwise                                       → #agent-{from_agent}
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

ESCALATION_CHANNEL = "#squad-escalations"
INFRA_CHANNEL = "#squad-infra"

ESCALATION_PRIORITIES = frozenset({"p0", "p1"})
ESCALATION_TYPES = frozenset({"escalation", "approval_required"})

Row = Mapping[str, Any]


def route_channel(row: Row) -> str:
    override = row.get("slack_channel")
    if override:
        return str(override)

    if (
        row.get("to_agent") == "jay"
        and row.get("priority") in ESCALATION_PRIORITIES
        and row.get("message_type") in ESCALATION_TYPES
    ):
        return ESCALATION_CHANNEL

    from_agent = row.get("from_agent")
    if from_agent == "argus":
        return INFRA_CHANNEL

    return f"#agent-{from_agent}"


def bot_token_for(from_agent: str, bot_tokens: Mapping[str, str]) -> str | None:
    """Look up the bot token for the agent that authored the row.

    Returns None if no token is configured. Caller logs a warning and
    skips the row (per clarification: from_agent='jay' has no token and
    is treated the same as a missing-token agent).
    """
    return bot_tokens.get(from_agent)
