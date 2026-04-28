"""Slack reaction emoji → DB action mapping.

Names follow Slack's emoji-name format (no surrounding colons).

Per the spec:
  - Most emojis update human_action. Setting human_action does NOT advance
    status; agents read their inbox and decide what to do next.
  - ✅ (white_check_mark) is the exception: it sets status='closed' and
    fires the lifecycle-timestamp trigger.
  - Unknown emojis are ignored (caller logs at debug, no error).
"""

from __future__ import annotations

from typing import Literal

EMOJI_TO_ACTION: dict[str, str] = {
    "+1": "approved",
    "x": "rejected",
    "octagonal_sign": "held",
    "eye": "flagged",
    "repeat": "redirected",
    "arrow_up": "escalated",
    "arrow_down": "de_escalated",
    "eyes": "acknowledged",
    "brain": "annotated",
}

CLOSE_EMOJI = "white_check_mark"

ResolvedEmoji = tuple[Literal["human_action", "status"], str]


def resolve_emoji(name: str) -> ResolvedEmoji | None:
    if name == CLOSE_EMOJI:
        return ("status", "closed")
    action = EMOJI_TO_ACTION.get(name)
    if action is not None:
        return ("human_action", action)
    return None
