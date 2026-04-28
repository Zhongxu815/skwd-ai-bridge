"""asyncpg pool plus the five query shapes the bridge runs against Postgres.

Query inventory (per spec + clarification):
  Q1 poll_unposted              — drives the poller loop
  Q2 mark_posted                — after each successful Slack post
  Q3 mark_failed                — after MAX_POST_RETRIES exhausted
  Q4a apply_human_action        — human reaction emoji → human_action
  Q4b set_status_closed         — ✅ emoji → status='closed'
  Q4c append_human_note         — Jay's thread-reply text
  Q5 get_parent_for_threading   — added per clarification: lookup parent
                                  slack_ts/status when reply_to is set
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

logger = logging.getLogger("bridge.db")

POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 5  # spec: "pool of 5 connections"


async def create_pool(dsn: str) -> asyncpg.Pool:
    """Create the asyncpg pool and verify connectivity with SELECT 1."""
    pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
    )
    if pool is None:
        raise RuntimeError("asyncpg.create_pool returned None")
    async with pool.acquire() as conn:
        await conn.execute("SELECT 1")
    logger.info(
        "db_pool_ready",
        extra={"min_size": POOL_MIN_SIZE, "max_size": POOL_MAX_SIZE},
    )
    return pool


async def close_pool(pool: asyncpg.Pool) -> None:
    await pool.close()


async def healthcheck(pool: asyncpg.Pool) -> bool:
    """Return True if a SELECT 1 round-trip succeeds."""
    try:
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
    except Exception as exc:
        logger.warning("db_healthcheck_failed", extra={"error": str(exc)})
        return False
    return True


# ──────────────────────────────────────────────────────────────────────────
# Q1 — poll for unposted rows
# ──────────────────────────────────────────────────────────────────────────

_POLL_SQL = """
    SELECT id, thread_id, reply_to, from_agent, to_agent, message_type,
           priority, subject, payload, requires_response, slack_channel
    FROM agent_unposted
    ORDER BY priority_order, created_at
    LIMIT $1
"""


async def poll_unposted(pool: asyncpg.Pool, limit: int) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_POLL_SQL, limit)
    return list(rows)


# ──────────────────────────────────────────────────────────────────────────
# Q2 — mark posted
# ──────────────────────────────────────────────────────────────────────────

_MARK_POSTED_SQL = """
    UPDATE agent_messages
    SET slack_channel = $1,
        slack_ts = $2,
        slack_thread_ts = $3
    WHERE id = $4
"""


async def mark_posted(
    pool: asyncpg.Pool,
    row_id: Any,
    slack_channel: str,
    slack_ts: str,
    slack_thread_ts: str,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            _MARK_POSTED_SQL, slack_channel, slack_ts, slack_thread_ts, row_id
        )


# ──────────────────────────────────────────────────────────────────────────
# Q3 — mark failed
# ──────────────────────────────────────────────────────────────────────────

_MARK_FAILED_SQL = "UPDATE agent_messages SET status = 'failed' WHERE id = $1"


async def mark_failed(pool: asyncpg.Pool, row_id: Any) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_MARK_FAILED_SQL, row_id)


# ──────────────────────────────────────────────────────────────────────────
# Q4a — apply human_action via emoji
# ──────────────────────────────────────────────────────────────────────────

_APPLY_HUMAN_ACTION_SQL = (
    "UPDATE agent_messages SET human_action = $1 WHERE slack_ts = $2"
)


async def apply_human_action(pool: asyncpg.Pool, slack_ts: str, action: str) -> int:
    """Set human_action on the row matching slack_ts. Returns rows updated."""
    async with pool.acquire() as conn:
        result = await conn.execute(_APPLY_HUMAN_ACTION_SQL, action, slack_ts)
    return _rowcount(result)


# ──────────────────────────────────────────────────────────────────────────
# Q4b — close (✅ emoji)
# ──────────────────────────────────────────────────────────────────────────

_SET_STATUS_CLOSED_SQL = (
    "UPDATE agent_messages SET status = 'closed' WHERE slack_ts = $1"
)


async def set_status_closed(pool: asyncpg.Pool, slack_ts: str) -> int:
    async with pool.acquire() as conn:
        result = await conn.execute(_SET_STATUS_CLOSED_SQL, slack_ts)
    return _rowcount(result)


# ──────────────────────────────────────────────────────────────────────────
# Q4c — append human_note from thread reply
# ──────────────────────────────────────────────────────────────────────────

_APPEND_HUMAN_NOTE_SQL = r"""
    UPDATE agent_messages
    SET human_note = COALESCE(human_note || E'\n', '') || $1
    WHERE slack_ts = $2
"""


async def append_human_note(pool: asyncpg.Pool, slack_ts: str, note: str) -> int:
    async with pool.acquire() as conn:
        result = await conn.execute(_APPEND_HUMAN_NOTE_SQL, note, slack_ts)
    return _rowcount(result)


# ──────────────────────────────────────────────────────────────────────────
# Q5 — parent lookup for threading
# ──────────────────────────────────────────────────────────────────────────

_PARENT_LOOKUP_SQL = (
    "SELECT id, slack_ts, status FROM agent_messages WHERE id = $1"
)


async def get_parent_for_threading(
    pool: asyncpg.Pool, parent_id: Any
) -> asyncpg.Record | None:
    """Look up parent's slack_ts and status. Returns None if parent not found."""
    async with pool.acquire() as conn:
        return await conn.fetchrow(_PARENT_LOOKUP_SQL, parent_id)


def _rowcount(execute_result: str) -> int:
    # asyncpg's execute() returns the command tag, e.g. 'UPDATE 1' or 'UPDATE 0'.
    parts = execute_result.split()
    if len(parts) < 2:
        return 0
    try:
        return int(parts[-1])
    except ValueError:
        return 0
