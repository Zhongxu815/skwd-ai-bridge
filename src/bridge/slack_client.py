"""Thin wrapper around slack-sdk's AsyncWebClient with retries.

Per spec Decision 4 — Slack post failure handling:
  - Total attempts: MAX_POST_RETRIES (default 4)
  - Sleep schedule between attempts: 1s, 2s, 4s (8s reserved if max bumped)
  - On final failure, raise the last exception so the caller can mark the
    row failed in DB.

The retry counter is "in-memory" per spec — meaning local to this call,
not persisted across polls. After the final failure the caller (poller)
mark_failed's the row, which removes it from agent_unposted; it will not
be retried by subsequent polls.

What counts as a failure (spec): any exception raised by slack-sdk
(SlackApiError covers ok=false responses + non-2xx) and any other
exception. v1 does not distinguish 4xx from 5xx.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger("bridge.slack_client")

DEFAULT_BACKOFF: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)


class SlackPostError(RuntimeError):
    """chat.postMessage returned without a `ts` (unexpected)."""


async def post_message(
    token: str,
    channel: str,
    blocks: list[dict[str, Any]],
    fallback_text: str,
    *,
    thread_ts: str | None = None,
    max_retries: int = 4,
    backoff: tuple[float, ...] = DEFAULT_BACKOFF,
) -> str:
    """Post `blocks` to `channel` as the bot owning `token`. Return ts.

    Sleeps backoff[i] before attempt i+2 (so attempt 1 has no sleep,
    attempt 2 sleeps backoff[0]=1s, attempt 3 sleeps 2s, attempt 4 sleeps
    4s — using 3 entries from a 4-entry schedule). Raises the last
    exception if all attempts fail.
    """
    client = AsyncWebClient(token=token)
    last_exc: BaseException | None = None

    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            idx = min(attempt - 2, len(backoff) - 1)
            await asyncio.sleep(backoff[idx])

        try:
            response = await client.chat_postMessage(
                channel=channel,
                blocks=blocks,
                text=fallback_text,
                thread_ts=thread_ts,
            )
        except SlackApiError as exc:
            last_exc = exc
            slack_err = (
                exc.response.get("error") if exc.response is not None else None
            )
            logger.warning(
                "slack_post_failed",
                extra={
                    "attempt": attempt,
                    "max_attempts": max_retries,
                    "channel": channel,
                    "thread_ts": thread_ts,
                    "slack_error": slack_err,
                    "error": str(exc),
                },
            )
            continue
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "slack_post_failed",
                extra={
                    "attempt": attempt,
                    "max_attempts": max_retries,
                    "channel": channel,
                    "thread_ts": thread_ts,
                    "error": str(exc),
                },
            )
            continue

        ts = response.get("ts")
        if not ts:
            last_exc = SlackPostError(
                f"chat.postMessage returned no ts: {response.data!r}"
            )
            logger.warning(
                "slack_post_no_ts",
                extra={"attempt": attempt, "channel": channel},
            )
            continue
        return str(ts)

    assert last_exc is not None
    raise last_exc
