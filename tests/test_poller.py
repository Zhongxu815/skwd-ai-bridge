"""Tests for bridge.poller.

Per the spec's testing rules: do not mock asyncpg. Mock at the function
level (db.poll_unposted, db.mark_posted, etc.) and at slack_client.post_message.
The pool argument is just an opaque marker — None passes through fine.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from bridge import poller
from bridge.config import Config


def _config(**overrides: Any) -> Config:
    base: dict[str, Any] = {
        "database_url": "postgres://fake",
        "poll_interval_seconds": 5,
        "poll_batch_size": 10,
        "max_post_retries": 4,
        "log_level": "INFO",
        "slack_signing_secret": None,
        "slack_user_id_jay": None,
        "bot_tokens": {"audra": "xoxb-A", "jules": "xoxb-J", "ollie": "xoxb-O"},
    }
    base.update(overrides)
    return Config(**base)


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "r1",
        "thread_id": None,
        "reply_to": None,
        "from_agent": "audra",
        "to_agent": "ollie",
        "message_type": "task_result",
        "priority": "p2",
        "subject": "Test subject",
        "payload": {"summary": "ok"},
        "requires_response": False,
        "slack_channel": None,
    }
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────────────────
# Fixtures: monkeypatch db + slack_client at the module level (per spec rule)
# ──────────────────────────────────────────────────────────────────────────


class _DbState:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.parents: dict[Any, dict[str, Any]] = {}
        self.posted: list[tuple[Any, str, str, str]] = []
        self.failed: list[Any] = []


@pytest.fixture
def db_state(monkeypatch: pytest.MonkeyPatch) -> _DbState:
    state = _DbState()

    async def poll_unposted(pool: Any, limit: int) -> list[dict[str, Any]]:
        rows = state.rows[:limit]
        state.rows = state.rows[limit:]
        return rows

    async def mark_posted(
        pool: Any, row_id: Any, channel: str, slack_ts: str, slack_thread_ts: str
    ) -> None:
        state.posted.append((row_id, channel, slack_ts, slack_thread_ts))

    async def mark_failed(pool: Any, row_id: Any) -> None:
        state.failed.append(row_id)

    async def get_parent_for_threading(
        pool: Any, parent_id: Any
    ) -> dict[str, Any] | None:
        return state.parents.get(parent_id)

    monkeypatch.setattr(poller.db, "poll_unposted", poll_unposted)
    monkeypatch.setattr(poller.db, "mark_posted", mark_posted)
    monkeypatch.setattr(poller.db, "mark_failed", mark_failed)
    monkeypatch.setattr(poller.db, "get_parent_for_threading", get_parent_for_threading)
    return state


class _SlackState:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.fail_with: BaseException | None = None


@pytest.fixture
def slack_state(monkeypatch: pytest.MonkeyPatch) -> _SlackState:
    state = _SlackState()

    async def post_message(
        token: str,
        channel: str,
        blocks: list[dict[str, Any]],
        fallback_text: str,
        *,
        thread_ts: str | None = None,
        max_retries: int = 4,
        backoff: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0),
    ) -> str:
        if state.fail_with is not None:
            raise state.fail_with
        ts = f"ts-{len(state.posts) + 1}"
        state.posts.append(
            {
                "token": token,
                "channel": channel,
                "blocks": blocks,
                "fallback_text": fallback_text,
                "thread_ts": thread_ts,
                "max_retries": max_retries,
                "ts": ts,
            }
        )
        return ts

    monkeypatch.setattr(poller.slack_client, "post_message", post_message)
    return state


# ──────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────


class TestHappyPath:
    async def test_single_row_posts_and_marks(
        self, db_state: _DbState, slack_state: _SlackState
    ) -> None:
        db_state.rows = [_row(id="r1", from_agent="audra")]
        await poller.poll_once(None, _config(), {})
        assert len(slack_state.posts) == 1
        post = slack_state.posts[0]
        assert post["token"] == "xoxb-A"
        assert post["channel"] == "#agent-audra"
        assert post["thread_ts"] is None
        assert post["fallback_text"] == "Test subject"
        assert db_state.posted == [("r1", "#agent-audra", "ts-1", "ts-1")]
        assert db_state.failed == []

    async def test_routing_override_respected(
        self, db_state: _DbState, slack_state: _SlackState
    ) -> None:
        db_state.rows = [
            _row(id="r1", from_agent="audra", slack_channel="#squad-boardroom")
        ]
        await poller.poll_once(None, _config(), {})
        assert slack_state.posts[0]["channel"] == "#squad-boardroom"
        assert db_state.posted[0][1] == "#squad-boardroom"

    async def test_escalation_routing(
        self, db_state: _DbState, slack_state: _SlackState
    ) -> None:
        db_state.rows = [
            _row(
                id="r1",
                from_agent="ollie",
                to_agent="jay",
                priority="p1",
                message_type="escalation",
            )
        ]
        await poller.poll_once(None, _config(), {})
        assert slack_state.posts[0]["channel"] == "#squad-escalations"

    async def test_empty_poll_does_nothing(
        self, db_state: _DbState, slack_state: _SlackState
    ) -> None:
        db_state.rows = []
        await poller.poll_once(None, _config(), {})
        assert slack_state.posts == []
        assert db_state.posted == []


class TestMissingToken:
    async def test_skip_with_warn_no_db_writes(
        self,
        db_state: _DbState,
        slack_state: _SlackState,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.WARNING)
        db_state.rows = [_row(id="r1", from_agent="cash")]
        await poller.poll_once(None, _config(), {})
        assert slack_state.posts == []
        assert db_state.posted == []
        assert db_state.failed == []
        warns = [r for r in caplog.records if r.message == "missing_bot_token"]
        assert len(warns) == 1
        assert getattr(warns[0], "from_agent") == "cash"

    async def test_jay_from_agent_skipped(
        self,
        db_state: _DbState,
        slack_state: _SlackState,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Per clarification: from_agent='jay' has no token, treated same
        # as missing-token.
        caplog.set_level(logging.WARNING)
        db_state.rows = [_row(id="r1", from_agent="jay")]
        await poller.poll_once(None, _config(), {})
        assert slack_state.posts == []
        warns = [r for r in caplog.records if r.message == "missing_bot_token"]
        assert len(warns) == 1
        assert getattr(warns[0], "from_agent") == "jay"


class TestSlackFailure:
    async def test_exhausted_retries_marks_row_failed(
        self, db_state: _DbState, slack_state: _SlackState
    ) -> None:
        db_state.rows = [_row(id="r1", from_agent="audra")]
        slack_state.fail_with = RuntimeError("simulated final failure")
        await poller.poll_once(None, _config(), {})
        assert db_state.posted == []
        assert db_state.failed == ["r1"]

    async def test_other_rows_keep_processing_after_failure(
        self, db_state: _DbState, slack_state: _SlackState
    ) -> None:
        # Slack fails for ALL rows here, but the loop should still call
        # mark_failed on each one and not crash.
        db_state.rows = [
            _row(id="r1", from_agent="audra"),
            _row(id="r2", from_agent="jules"),
        ]
        slack_state.fail_with = RuntimeError("boom")
        await poller.poll_once(None, _config(), {})
        assert db_state.failed == ["r1", "r2"]


class TestThreading:
    async def test_parent_posted_threads_correctly(
        self, db_state: _DbState, slack_state: _SlackState
    ) -> None:
        db_state.rows = [_row(id="r2", from_agent="audra", reply_to="r1")]
        db_state.parents["r1"] = {
            "id": "r1",
            "slack_ts": "1234.5678",
            "status": "pending",
        }
        await poller.poll_once(None, _config(), {})
        assert slack_state.posts[0]["thread_ts"] == "1234.5678"
        # mark_posted records slack_thread_ts = parent's ts (not the new ts)
        assert db_state.posted == [("r2", "#agent-audra", "ts-1", "1234.5678")]

    async def test_parent_unposted_held_back(
        self, db_state: _DbState, slack_state: _SlackState
    ) -> None:
        db_state.rows = [_row(id="r2", from_agent="audra", reply_to="r1")]
        db_state.parents["r1"] = {
            "id": "r1",
            "slack_ts": None,
            "status": "pending",
        }
        skip_counts: dict[Any, int] = {}
        await poller.poll_once(None, _config(), skip_counts)
        assert slack_state.posts == []
        assert db_state.posted == []
        assert db_state.failed == []
        assert skip_counts == {"r2": 1}

    async def test_skip_count_grows_then_falls_back(
        self,
        db_state: _DbState,
        slack_state: _SlackState,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        db_state.parents["r1"] = {
            "id": "r1",
            "slack_ts": None,
            "status": "pending",
        }
        skip_counts: dict[Any, int] = {}
        config = _config()  # max_post_retries=4
        caplog.set_level(logging.WARNING)

        for cycle in range(1, 5):  # cycles 1..4
            db_state.rows = [_row(id="r2", from_agent="audra", reply_to="r1")]
            await poller.poll_once(None, config, skip_counts)

        # Cycles 1-3: held. Cycle 4: count reaches 4, falls back.
        assert len(slack_state.posts) == 1
        post = slack_state.posts[0]
        assert post["thread_ts"] is None
        # Footer should contain "parent stuck"
        last_block = post["blocks"][-1]
        last_text = " ".join(
            str(e.get("text", "")) for e in last_block["elements"]
        )
        assert "stuck" in last_text
        # skip_count should be cleared after fallback
        assert "r2" not in skip_counts
        # Should have logged the fall-back warn
        warns = [
            r
            for r in caplog.records
            if r.message == "reply_parent_stuck_falling_back"
        ]
        assert len(warns) == 1

    async def test_parent_failed_immediate_fallback(
        self,
        db_state: _DbState,
        slack_state: _SlackState,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        db_state.rows = [_row(id="r2", from_agent="audra", reply_to="r1")]
        db_state.parents["r1"] = {
            "id": "r1",
            "slack_ts": None,
            "status": "failed",
        }
        caplog.set_level(logging.WARNING)
        skip_counts: dict[Any, int] = {}
        await poller.poll_once(None, _config(), skip_counts)
        assert len(slack_state.posts) == 1
        post = slack_state.posts[0]
        assert post["thread_ts"] is None
        # Footer marker distinguishes failed from stuck
        last_block = post["blocks"][-1]
        last_text = " ".join(
            str(e.get("text", "")) for e in last_block["elements"]
        )
        assert "failed" in last_text
        assert "stuck" not in last_text
        assert skip_counts == {}
        warns = [
            r
            for r in caplog.records
            if r.message == "reply_parent_failed_falling_back"
        ]
        assert len(warns) == 1

    async def test_parent_not_found_falls_through(
        self,
        db_state: _DbState,
        slack_state: _SlackState,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # reply_to set but parent missing from DB → orphaned, post top-level
        # with no special note.
        db_state.rows = [_row(id="r2", from_agent="audra", reply_to="missing")]
        db_state.parents = {}
        caplog.set_level(logging.WARNING)
        await poller.poll_once(None, _config(), {})
        assert len(slack_state.posts) == 1
        assert slack_state.posts[0]["thread_ts"] is None
        warns = [
            r for r in caplog.records if r.message == "reply_parent_not_found"
        ]
        assert len(warns) == 1

    async def test_skip_count_resets_when_parent_finally_posts(
        self, db_state: _DbState, slack_state: _SlackState
    ) -> None:
        # Cycle 1: parent unposted → skip_count goes to 1.
        # Cycle 2: parent now has slack_ts → row posts as thread reply,
        # skip_count entry is removed.
        skip_counts: dict[Any, int] = {}

        db_state.parents["r1"] = {
            "id": "r1",
            "slack_ts": None,
            "status": "pending",
        }
        db_state.rows = [_row(id="r2", from_agent="audra", reply_to="r1")]
        await poller.poll_once(None, _config(), skip_counts)
        assert skip_counts == {"r2": 1}
        assert slack_state.posts == []

        # Parent posts in some other cycle.
        db_state.parents["r1"]["slack_ts"] = "9999.0001"
        db_state.rows = [_row(id="r2", from_agent="audra", reply_to="r1")]
        await poller.poll_once(None, _config(), skip_counts)
        assert skip_counts == {}
        assert len(slack_state.posts) == 1
        assert slack_state.posts[0]["thread_ts"] == "9999.0001"


class TestLoopResilience:
    async def test_unexpected_error_in_format_does_not_crash_loop(
        self,
        db_state: _DbState,
        slack_state: _SlackState,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Sabotage format_message for the first row only; the second row
        # should still post normally.
        db_state.rows = [
            _row(id="r1", from_agent="audra"),
            _row(id="r2", from_agent="jules"),
        ]
        call_count = {"n": 0}
        original_format = poller.formatter.format_message

        def flaky_format(row: Any) -> Any:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("boom in format_message")
            return original_format(row)

        monkeypatch.setattr(poller.formatter, "format_message", flaky_format)
        caplog.set_level(logging.ERROR)
        await poller.poll_once(None, _config(), {})

        # Second row posted successfully
        assert len(slack_state.posts) == 1
        assert slack_state.posts[0]["token"] == "xoxb-J"
        # Error logged for the first row
        errs = [r for r in caplog.records if r.message == "row_processing_error"]
        assert len(errs) == 1
        assert getattr(errs[0], "row_id") == "r1"


class TestRunForever:
    async def test_run_forever_can_be_cancelled(
        self, db_state: _DbState, slack_state: _SlackState
    ) -> None:
        import asyncio

        config = _config(poll_interval_seconds=0)
        task = asyncio.create_task(poller.run_forever(None, config))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.cancelled() or task.done()

    async def test_run_forever_logs_and_continues_on_cycle_error(
        self,
        db_state: _DbState,
        slack_state: _SlackState,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Sabotage poll_unposted on the first call only. run_forever should
        # log poller_cycle_error and continue spinning.
        import asyncio

        call_count = {"n": 0}
        original = poller.db.poll_unposted

        async def flaky_poll(pool: Any, limit: int) -> list[dict[str, Any]]:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("transient db blip")
            return await original(pool, limit)

        monkeypatch.setattr(poller.db, "poll_unposted", flaky_poll)
        caplog.set_level(logging.ERROR)

        config = _config(poll_interval_seconds=0)
        task = asyncio.create_task(poller.run_forever(None, config))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        errs = [r for r in caplog.records if r.message == "poller_cycle_error"]
        assert len(errs) >= 1
        assert call_count["n"] >= 2  # crashed once, recovered, kept polling


class TestAppendFooterNote:
    def test_empty_blocks_unchanged(self) -> None:
        blocks: list[dict[str, Any]] = []
        poller._append_footer_note(blocks, "test")
        assert blocks == []

    def test_last_block_not_context_unchanged(self) -> None:
        blocks: list[dict[str, Any]] = [
            {"type": "section", "text": {"type": "mrkdwn", "text": "x"}}
        ]
        original = list(blocks)
        poller._append_footer_note(blocks, "test")
        assert blocks == original

    def test_appends_to_context_elements(self) -> None:
        blocks: list[dict[str, Any]] = [
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "id"}]}
        ]
        poller._append_footer_note(blocks, "parent failed")
        assert len(blocks[0]["elements"]) == 2
        assert "_parent failed_" in blocks[0]["elements"][1]["text"]
