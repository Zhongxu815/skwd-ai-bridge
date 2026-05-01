"""Format an agent_messages row as Slack blocks.

Single entry point: format_message(row) -> list[Block].
One private helper per message_type for the summary line; if the type is
unknown the summary degrades gracefully and a WARN is logged.

Slack chrome (bot name, sent-at time) is rendered automatically by Slack
when the bridge posts via chat.postMessage as the agent bot, so blocks
start with the subject — not bot/time.

Layout per post:
  section:  *subject*
  context:  `message_type` · `priority` · → `to_agent`
  section:  <summary derived from payload + message_type>
  divider
  context:  id: `<truncated to 8 chars>`

The `→ to_agent` indicator in the context line is essential — not just
useful — for posts that land in `#squad-bus`. Many agents post into the
bus, so bot identity is the only "who's speaking" signal, and
`→ to_agent` is the only "who's this for" signal. Without it a reader
can't tell at a glance whether a message in the firehose is meant for
them or another agent.

Truncation:
  query_response.answer       → 200 chars (per spec)
  fields documented "1 line"  → first non-empty line, then 200-char cap

When the summary collapses to NO_SUMMARY, a WARN is emitted with the row
id, message_type, and the field names this formatter looks at — so a
systematically-wrong field-name guess against real seed data surfaces in
logs instead of producing bland-but-passing posts.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from typing import Any

logger = logging.getLogger("bridge.formatter")

ANSWER_MAX = 200
ONE_LINE_MAX = 200
ID_PREFIX_LEN = 8
NO_SUMMARY = "_(no summary in payload)_"

Row = Mapping[str, Any]
Block = dict[str, Any]
SummaryHandler = Callable[[Mapping[str, Any]], str]


# Fields each summary helper looks at, included in the WARN that fires when
# a payload yields an empty summary. Source: migrations/003_seed_data.sql.
_EXPECTED_FIELDS: dict[str, tuple[str, ...]] = {
    "task": ("context.pr_number", "context.description", "action_requested"),
    "task_result": ("summary",),
    "query": ("question",),
    "query_response": ("answer",),
    "proposal": ("title", "rationale"),
    "escalation": ("what_i_need", "what_i_tried"),
    "notification": ("event", "details"),
    "approval_required": ("action_pending", "summary"),
    "debug_diagnosis": ("bug_summary", "root_cause", "risk_tier"),
}


def format_message(row: Row) -> list[Block]:
    subject = str(row.get("subject") or "(no subject)")
    message_type = str(row.get("message_type") or "?")
    priority = str(row.get("priority") or "?")
    to_agent = str(row.get("to_agent") or "?")

    raw_payload = row.get("payload")
    payload: Mapping[str, Any] = (
        raw_payload if isinstance(raw_payload, Mapping) else {}
    )

    raw_id = row.get("id")
    id_str = str(raw_id) if raw_id else ""
    id_short = id_str[:ID_PREFIX_LEN] if id_str else "(no id)"

    summary = _summary_for(message_type, payload)
    if not summary:
        logger.warning(
            "formatter_no_summary",
            extra={
                "row_id": id_str,
                "message_type": message_type,
                "expected_fields": list(_EXPECTED_FIELDS.get(message_type, ())),
            },
        )
        summary = NO_SUMMARY

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{subject}*"}},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"`{message_type}` · `{priority}` · → `{to_agent}`",
                }
            ],
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": summary}},
        {"type": "divider"},
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"id: `{id_short}`"}],
        },
    ]


def _summary_for(message_type: str, payload: Mapping[str, Any]) -> str:
    handler = _SUMMARY_HANDLERS.get(message_type, _summary_unknown)
    return handler(payload)


def _summary_task(p: Mapping[str, Any]) -> str:
    parts: list[str] = []
    raw_context = p.get("context")
    context: Mapping[str, Any] = (
        raw_context if isinstance(raw_context, Mapping) else {}
    )
    pr_number = context.get("pr_number")
    description = _one_line(context.get("description"))
    repo = _as_str(context.get("repo")).strip()

    if pr_number and description:
        head = f"*PR #{pr_number}:* {description}"
        if repo:
            head += f" `{repo}`"
        parts.append(head)
    elif description:
        parts.append(description)

    action = _one_line(p.get("action_requested"))
    if action:
        parts.append(f"*Action:* {action}")

    criteria = _one_line(p.get("acceptance_criteria"))
    if criteria:
        parts.append(f"*Criteria:* {criteria}")

    return "\n".join(parts)


def _summary_task_result(p: Mapping[str, Any]) -> str:
    return _one_line(p.get("summary"))


def _summary_query(p: Mapping[str, Any]) -> str:
    return _one_line(p.get("question"))


def _summary_query_response(p: Mapping[str, Any]) -> str:
    return _truncate(_as_str(p.get("answer")).strip(), ANSWER_MAX)


def _summary_proposal(p: Mapping[str, Any]) -> str:
    title = _one_line(p.get("title"))
    rationale = _one_line(p.get("rationale"))
    parts: list[str] = []
    if title:
        parts.append(f"*{title}*")
    if rationale:
        parts.append(rationale)
    return "\n".join(parts)


def _summary_escalation(p: Mapping[str, Any]) -> str:
    parts: list[str] = []
    what = _one_line(p.get("what_i_need"))
    if what:
        parts.append(f"*Need:* {what}")
    tried = p.get("what_i_tried")
    first_tried = ""
    if isinstance(tried, list) and tried:
        first_tried = _one_line(tried[0])
    elif isinstance(tried, str):
        first_tried = _one_line(tried)
    if first_tried:
        parts.append(f"*Tried:* {first_tried}")
    return "\n".join(parts)


def _summary_notification(p: Mapping[str, Any]) -> str:
    parts: list[str] = []
    event = _one_line(p.get("event"))
    if event:
        parts.append(f"*Event:* {event}")
    details = p.get("details")
    if details:
        try:
            details_str = json.dumps(details, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            details_str = str(details)
        parts.append(f"`{_truncate(details_str, ONE_LINE_MAX)}`")
    return "\n".join(parts)


def _summary_approval_required(p: Mapping[str, Any]) -> str:
    pending = _one_line(p.get("action_pending"))
    summary = _one_line(p.get("summary"))
    parts: list[str] = []
    if pending:
        parts.append(f"*Pending:* {pending}")
    if summary:
        parts.append(summary)
    return "\n".join(parts)


def _summary_debug_diagnosis(p: Mapping[str, Any]) -> str:
    parts: list[str] = []
    tier = _one_line(p.get("risk_tier"))
    bug = _one_line(p.get("bug_summary"))
    cause = _one_line(p.get("root_cause"))
    if tier:
        parts.append(f"*Risk:* `{tier}`")
    if bug:
        parts.append(f"*Bug:* {bug}")
    if cause:
        parts.append(f"*Root cause:* {cause}")
    return "\n".join(parts)


def _summary_unknown(_p: Mapping[str, Any]) -> str:
    return ""


_SUMMARY_HANDLERS: dict[str, SummaryHandler] = {
    "task": _summary_task,
    "task_result": _summary_task_result,
    "query": _summary_query,
    "query_response": _summary_query_response,
    "proposal": _summary_proposal,
    "escalation": _summary_escalation,
    "notification": _summary_notification,
    "approval_required": _summary_approval_required,
    "debug_diagnosis": _summary_debug_diagnosis,
}


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _one_line(value: Any, max_chars: int = ONE_LINE_MAX) -> str:
    s = _as_str(value).strip()
    if not s:
        return ""
    return _truncate(s.splitlines()[0], max_chars)


def _truncate(s: str, max_chars: int) -> str:
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1] + "…"
