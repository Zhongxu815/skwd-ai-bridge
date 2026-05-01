"""Channel routing rules + bot token lookup.

Pure functions — no IO. The poller calls these to decide where each row
goes and which bot identity to post as.

Routing rules from the spec (Decision 2), evaluated in order, first
match wins:

  1. slack_channel override (set on the row at insert time)
                                                     → use it as-is
  2. priority='p0'                                   → #squad-escalations
                                                       (P0 backstop —
                                                       never miss a true
                                                       emergency)
  3. to_agent='jay' AND message_type IN
       (escalation, approval_required)               → #squad-escalations
  4. to_agent='jay'                                  → #agent-{from_agent}
                                                       (non-urgent direct
                                                       message to Jay
                                                       lands in sender's
                                                       channel)
  5. from_agent='jay'                                → #agent-{to_agent}
                                                       (Jay-initiated DM
                                                       to a specific agent)
  6. from_agent='argus'                              → #squad-infra
  7. otherwise                                       → #squad-bus
                                                       (default for
                                                       agent-to-agent
                                                       traffic)

Three precedence subtleties baked in by rule order:

- Override (rule 1) beats P0 (rule 2). If a sender explicitly targets
  e.g. #squad-boardroom on a P0, trust them — they may want the row in
  a war-room channel rather than the default escalations channel.
- P0 from Argus (rule 2) beats Argus's default (rule 6). A P0 from Argus
  must reach Jay in #squad-escalations, not get buffered in #squad-infra
  alongside routine health pings.
- P1 alone does NOT escalate. Only P0 (rule 2) or to_agent='jay' with
  the right message_type (rule 3) lands in escalations. A P1 task
  between two agents stays on the bus — escalations is for things meant
  for Jay, not just things urgent within the swarm.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CHANNEL_BUS = "#squad-bus"
CHANNEL_ESCALATIONS = "#squad-escalations"
CHANNEL_INFRA = "#squad-infra"

ESCALATION_TYPES_TO_JAY = frozenset({"escalation", "approval_required"})

Row = Mapping[str, Any]


def route_channel(row: Row) -> str:
    override = row.get("slack_channel")
    if override:
        return str(override)

    if row.get("priority") == "p0":
        return CHANNEL_ESCALATIONS

    to_agent = row.get("to_agent")
    from_agent = row.get("from_agent")

    if to_agent == "jay":
        if row.get("message_type") in ESCALATION_TYPES_TO_JAY:
            return CHANNEL_ESCALATIONS
        return f"#agent-{from_agent}"

    if from_agent == "jay":
        return f"#agent-{to_agent}"

    if from_agent == "argus":
        return CHANNEL_INFRA

    return CHANNEL_BUS


def bot_token_for(from_agent: str, bot_tokens: Mapping[str, str]) -> str | None:
    """Look up the bot token for the agent that authored the row.

    Returns None if no token is configured. Caller logs a warning and
    skips the row (per clarification: from_agent='jay' has no token and
    is treated the same as a missing-token agent).
    """
    return bot_tokens.get(from_agent)
