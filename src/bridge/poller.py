"""The polling loop. Reads agent_unposted, posts to Slack, updates DB.

Per spec + clarifications:
  - Poll cadence: POLL_INTERVAL_SECONDS (default 5).
  - Per row: route_channel → format → bot_token_for → post → mark_posted
    (or mark_failed after retries exhausted inside slack_client).
  - Threading (clarifications 1-3):
      * If reply_to is set, look up parent (Q5).
      * Parent has slack_ts → post as thread reply.
      * Parent status='failed' → fall back immediately to top-level with
        footer note "parent failed."
      * Parent pending without slack_ts → skip this cycle and increment a
        per-row counter. Once it reaches MAX_POST_RETRIES, fall back to
        top-level with footer note "parent stuck."
      * Counter resets when parent eventually posts (entry removed on
        the cycle the row finally posts).
  - Missing bot token (incl. from_agent='jay') → log WARN, skip the row,
    do NOT mark failed; row stays in queue and retries each cycle.
  - One row's failure must not crash the loop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from bridge import db, formatter, router, slack_client
from bridge.config import Config

logger = logging.getLogger("bridge.poller")


async def run_forever(pool: Any, config: Config) -> None:
    """Main polling loop. Runs until cancelled."""
    skip_counts: dict[Any, int] = {}
    logger.info(
        "poller_started",
        extra={
            "interval_seconds": config.poll_interval_seconds,
            "batch_size": config.poll_batch_size,
        },
    )
    while True:
        try:
            await poll_once(pool, config, skip_counts)
        except asyncio.CancelledError:
            logger.info("poller_cancelled")
            raise
        except Exception:
            logger.exception("poller_cycle_error")
        try:
            await asyncio.sleep(config.poll_interval_seconds)
        except asyncio.CancelledError:
            logger.info("poller_cancelled")
            raise


async def poll_once(
    pool: Any, config: Config, skip_counts: dict[Any, int]
) -> None:
    """One poll cycle: fetch a batch, process each row."""
    rows = await db.poll_unposted(pool, config.poll_batch_size)
    if rows:
        logger.info("poll_cycle", extra={"row_count": len(rows)})
    else:
        logger.debug("poll_cycle_empty")

    for row in rows:
        try:
            await _process_row(row, pool, config, skip_counts)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "row_processing_error",
                extra={"row_id": str(row.get("id"))},
            )


async def _process_row(
    row: Mapping[str, Any],
    pool: Any,
    config: Config,
    skip_counts: dict[Any, int],
) -> None:
    row_id = row.get("id")
    from_agent = str(row.get("from_agent") or "")

    # 1. Threading: resolve parent (if reply) → thread_ts + footer note,
    #    or signal that we should hold this cycle.
    threading = await _resolve_threading(row, pool, config, skip_counts)
    if threading is None:
        return  # held this cycle; will retry next poll
    thread_ts, parent_status_note = threading

    # 2. Bot token lookup
    token = router.bot_token_for(from_agent, config.bot_tokens)
    if token is None:
        logger.warning(
            "missing_bot_token",
            extra={"row_id": str(row_id), "from_agent": from_agent},
        )
        return

    # 3. Format blocks (+ optional footer note for parent failed/stuck)
    blocks = formatter.format_message(row)
    if parent_status_note:
        _append_footer_note(blocks, f"parent {parent_status_note}")

    # 4. Channel
    channel = router.route_channel(row)

    # 5. Fallback text from subject
    subject = str(row.get("subject") or "(agent message)")
    fallback_text = subject[:200]

    # 6. Post to Slack (retries handled inside slack_client)
    try:
        slack_ts = await slack_client.post_message(
            token=token,
            channel=channel,
            blocks=blocks,
            fallback_text=fallback_text,
            thread_ts=thread_ts,
            max_retries=config.max_post_retries,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(
            "post_failed_marking_row_failed",
            extra={
                "row_id": str(row_id),
                "channel": channel,
                "from_agent": from_agent,
                "error": str(exc),
            },
        )
        await db.mark_failed(pool, row_id)
        return

    # 7. Mark posted. slack_thread_ts: parent's ts for replies, own ts
    #    for top-level (per spec).
    final_thread_ts = thread_ts if thread_ts else slack_ts
    await db.mark_posted(pool, row_id, channel, slack_ts, final_thread_ts)
    logger.info(
        "row_posted",
        extra={
            "row_id": str(row_id),
            "channel": channel,
            "from_agent": from_agent,
            "slack_ts": slack_ts,
            "threaded": thread_ts is not None,
        },
    )


async def _resolve_threading(
    row: Mapping[str, Any],
    pool: Any,
    config: Config,
    skip_counts: dict[Any, int],
) -> tuple[str | None, str] | None:
    """Decide thread_ts and (if falling back) a footer note.

    Returns:
      (thread_ts=None,  note="")        for top-level posts (no reply_to,
                                         orphaned parent, or threading not
                                         applicable).
      (thread_ts=<ts>,  note="")        for normal thread replies.
      (thread_ts=None,  note="failed")  fall back: parent status=failed.
      (thread_ts=None,  note="stuck")   fall back: parent has been pending
                                         too many cycles.
      None                               hold this cycle (caller skips).
    """
    parent_id = row.get("reply_to")
    if parent_id is None:
        return (None, "")

    row_id = row.get("id")
    parent = await db.get_parent_for_threading(pool, parent_id)

    if parent is None:
        logger.warning(
            "reply_parent_not_found",
            extra={"row_id": str(row_id), "parent_id": str(parent_id)},
        )
        skip_counts.pop(row_id, None)
        return (None, "")

    parent_slack_ts = parent.get("slack_ts")
    parent_status = parent.get("status")

    if parent_slack_ts:
        skip_counts.pop(row_id, None)
        return (str(parent_slack_ts), "")

    if parent_status == "failed":
        logger.warning(
            "reply_parent_failed_falling_back",
            extra={"row_id": str(row_id), "parent_id": str(parent_id)},
        )
        skip_counts.pop(row_id, None)
        return (None, "failed")

    # Parent pending and not yet posted. Increment skip count;
    # fall back to top-level once we hit MAX_POST_RETRIES.
    count = skip_counts.get(row_id, 0) + 1
    if count >= config.max_post_retries:
        logger.warning(
            "reply_parent_stuck_falling_back",
            extra={
                "row_id": str(row_id),
                "parent_id": str(parent_id),
                "skip_count": count,
            },
        )
        skip_counts.pop(row_id, None)
        return (None, "stuck")

    skip_counts[row_id] = count
    logger.info(
        "reply_held_for_parent",
        extra={
            "row_id": str(row_id),
            "parent_id": str(parent_id),
            "skip_count": count,
        },
    )
    return None


def _append_footer_note(blocks: list[dict[str, Any]], note: str) -> None:
    """Mutate the last context block to append `note` (italics)."""
    if not blocks:
        return
    last = blocks[-1]
    if last.get("type") != "context":
        return
    elements = last.setdefault("elements", [])
    elements.append({"type": "mrkdwn", "text": f"_{note}_"})
