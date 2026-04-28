"""Tests for bridge.webhook.

Signatures are computed with real slack-sdk SignatureVerifier (per the
spec rule: do not mock the verifier — that defeats the point of the
security test). DB is mocked at the function level on bridge.webhook.db.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slack_sdk.signature import SignatureVerifier

from bridge import webhook
from bridge.config import Config

TEST_SECRET = "test-signing-secret"
JAY = "U_JAY_TEST"
NOT_JAY = "U_OTHER"
ROW_ID = "row-uuid-1234"
PARENT_TS = "1700000000.000100"


def _make_config(
    secret: str | None = TEST_SECRET, jay_id: str | None = JAY
) -> Config:
    return Config(
        database_url="postgres://fake",
        poll_interval_seconds=5,
        poll_batch_size=10,
        max_post_retries=4,
        log_level="INFO",
        slack_signing_secret=secret,
        slack_user_id_jay=jay_id,
        bot_tokens={},
    )


# ──────────────────────────────────────────────────────────────────────────
# Fakes for db functions (monkeypatched at module attribute level)
# ──────────────────────────────────────────────────────────────────────────


class _DbState:
    def __init__(self) -> None:
        self.matching_ids: dict[str, Any] = {}
        self.human_action_calls: list[tuple[str, str]] = []
        self.set_closed_calls: list[str] = []
        self.append_note_calls: list[tuple[str, str]] = []

    async def find_id_by_slack_ts(self, pool: Any, slack_ts: str) -> Any | None:
        return self.matching_ids.get(slack_ts)

    async def apply_human_action(
        self, pool: Any, slack_ts: str, action: str
    ) -> int:
        self.human_action_calls.append((slack_ts, action))
        return 1

    async def set_status_closed(self, pool: Any, slack_ts: str) -> int:
        self.set_closed_calls.append(slack_ts)
        return 1

    async def append_human_note(
        self, pool: Any, slack_ts: str, note: str
    ) -> int:
        self.append_note_calls.append((slack_ts, note))
        return 1


@pytest.fixture
def db_state(monkeypatch: pytest.MonkeyPatch) -> _DbState:
    state = _DbState()
    monkeypatch.setattr(webhook.db, "find_id_by_slack_ts", state.find_id_by_slack_ts)
    monkeypatch.setattr(webhook.db, "apply_human_action", state.apply_human_action)
    monkeypatch.setattr(webhook.db, "set_status_closed", state.set_status_closed)
    monkeypatch.setattr(webhook.db, "append_human_note", state.append_human_note)
    return state


@pytest.fixture
def client(db_state: _DbState) -> TestClient:
    app = FastAPI()
    app.include_router(webhook.router)
    app.state.config = _make_config()
    app.state.db_pool = None
    return TestClient(app)


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _signed_post(
    client: TestClient,
    payload: dict[str, Any],
    *,
    secret: str = TEST_SECRET,
    timestamp: str | None = None,
    bad_signature: bool = False,
    omit_signature: bool = False,
) -> Any:
    body = json.dumps(payload).encode()
    headers: dict[str, str] = {"content-type": "application/json"}

    if not omit_signature:
        ts = timestamp if timestamp is not None else str(int(time.time()))
        verifier = SignatureVerifier(signing_secret=secret)
        sig = verifier.generate_signature(timestamp=ts, body=body.decode())
        if bad_signature:
            sig = "v0=deadbeef" + ("0" * 56)
        headers["x-slack-request-timestamp"] = ts
        headers["x-slack-signature"] = sig

    return client.post("/slack/events", content=body, headers=headers)


def _reaction_event(
    *, emoji: str, user: str = JAY, item_ts: str = PARENT_TS
) -> dict[str, Any]:
    return {
        "type": "event_callback",
        "team_id": "T0001",
        "event": {
            "type": "reaction_added",
            "user": user,
            "reaction": emoji,
            "item": {
                "type": "message",
                "channel": "C0001",
                "ts": item_ts,
            },
            "item_user": "U_BOT_AUDRA",
            "event_ts": "1700000001.000200",
        },
    }


def _thread_reply_event(
    *,
    text: str = "looks good",
    user: str = JAY,
    parent_ts: str = PARENT_TS,
    subtype: str | None = None,
    bot_id: str | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "message",
        "channel": "C0001",
        "user": user,
        "text": text,
        "ts": "1700000002.000100",
        "thread_ts": parent_ts,
        "parent_user_id": "U_BOT_AUDRA",
    }
    if subtype is not None:
        event["subtype"] = subtype
    if bot_id is not None:
        event["bot_id"] = bot_id
    return {
        "type": "event_callback",
        "team_id": "T0001",
        "event": event,
    }


# ──────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────


class TestUrlVerification:
    def test_returns_challenge_verbatim_without_signature(
        self, client: TestClient
    ) -> None:
        # Test (a): URL verification short-circuits BEFORE signature check.
        body = json.dumps(
            {"token": "x", "challenge": "abc123xyz", "type": "url_verification"}
        ).encode()
        response = client.post(
            "/slack/events",
            content=body,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 200
        assert response.text == "abc123xyz"

    def test_url_verification_with_signature_also_works(
        self, client: TestClient
    ) -> None:
        response = _signed_post(
            client,
            {"token": "x", "challenge": "hello", "type": "url_verification"},
        )
        assert response.status_code == 200
        assert response.text == "hello"

    def test_url_verification_missing_challenge_field(
        self, client: TestClient
    ) -> None:
        # Defensive: malformed url_verification (no challenge) → still 200,
        # empty body. Slack would never send this, but we don't crash.
        response = _signed_post(client, {"type": "url_verification"})
        assert response.status_code == 200


class TestSignatureVerification:
    def test_bad_signature_returns_401(
        self, client: TestClient, db_state: _DbState
    ) -> None:
        response = _signed_post(
            client, _reaction_event(emoji="+1"), bad_signature=True
        )
        assert response.status_code == 401
        assert db_state.human_action_calls == []

    def test_missing_signature_returns_401(
        self, client: TestClient, db_state: _DbState
    ) -> None:
        response = _signed_post(
            client, _reaction_event(emoji="+1"), omit_signature=True
        )
        assert response.status_code == 401
        assert db_state.human_action_calls == []

    def test_old_timestamp_rejected(
        self, client: TestClient, db_state: _DbState
    ) -> None:
        # slack-sdk's verifier rejects timestamps older than ~5 min.
        old_ts = str(int(time.time()) - 60 * 60)
        response = _signed_post(
            client, _reaction_event(emoji="+1"), timestamp=old_ts
        )
        assert response.status_code == 401
        assert db_state.human_action_calls == []

    def test_secret_not_configured_returns_401(
        self, db_state: _DbState
    ) -> None:
        # If SLACK_SIGNING_SECRET is missing, every signed request → 401
        # (URL verification still works, tested separately).
        app = FastAPI()
        app.include_router(webhook.router)
        app.state.config = _make_config(secret=None)
        app.state.db_pool = None
        client = TestClient(app)
        response = _signed_post(client, _reaction_event(emoji="+1"))
        assert response.status_code == 401


class TestReactionAdded:
    @pytest.mark.parametrize(
        "emoji,expected_action",
        [
            ("+1", "approved"),
            ("x", "rejected"),
            ("octagonal_sign", "held"),
            ("eye", "flagged"),
            ("repeat", "redirected"),
            ("arrow_up", "escalated"),
            ("arrow_down", "de_escalated"),
            ("eyes", "acknowledged"),
            ("brain", "annotated"),
        ],
    )
    def test_known_emoji_applies_human_action(
        self,
        client: TestClient,
        db_state: _DbState,
        emoji: str,
        expected_action: str,
    ) -> None:
        db_state.matching_ids[PARENT_TS] = ROW_ID
        response = _signed_post(client, _reaction_event(emoji=emoji))
        assert response.status_code == 200
        assert db_state.human_action_calls == [(PARENT_TS, expected_action)]
        assert db_state.set_closed_calls == []

    def test_white_check_mark_sets_status_closed(
        self, client: TestClient, db_state: _DbState
    ) -> None:
        db_state.matching_ids[PARENT_TS] = ROW_ID
        response = _signed_post(
            client, _reaction_event(emoji="white_check_mark")
        )
        assert response.status_code == 200
        assert db_state.set_closed_calls == [PARENT_TS]
        assert db_state.human_action_calls == []

    def test_non_jay_reactor_ignored(
        self,
        client: TestClient,
        db_state: _DbState,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        db_state.matching_ids[PARENT_TS] = ROW_ID
        caplog.set_level(logging.DEBUG, logger="bridge.webhook")
        response = _signed_post(
            client, _reaction_event(emoji="+1", user=NOT_JAY)
        )
        assert response.status_code == 200
        assert db_state.human_action_calls == []
        assert any(
            r.message == "reaction_from_non_jay_user" for r in caplog.records
        )

    def test_no_matching_row_ignored(
        self,
        client: TestClient,
        db_state: _DbState,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Row lookup returns None → quiet ignore, even if reactor is Jay.
        caplog.set_level(logging.DEBUG, logger="bridge.webhook")
        response = _signed_post(client, _reaction_event(emoji="+1"))
        assert response.status_code == 200
        assert db_state.human_action_calls == []
        assert any(
            r.message == "reaction_no_matching_row" for r in caplog.records
        )

    def test_unknown_emoji_ignored(
        self,
        client: TestClient,
        db_state: _DbState,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        db_state.matching_ids[PARENT_TS] = ROW_ID
        caplog.set_level(logging.DEBUG, logger="bridge.webhook")
        response = _signed_post(client, _reaction_event(emoji="taco"))
        assert response.status_code == 200
        assert db_state.human_action_calls == []
        assert db_state.set_closed_calls == []
        assert any(
            r.message == "reaction_unknown_emoji" for r in caplog.records
        )

    def test_reaction_missing_item_field(
        self, client: TestClient, db_state: _DbState
    ) -> None:
        # Defensive: malformed reaction event (no item) → 200, no DB calls.
        payload = {
            "type": "event_callback",
            "event": {
                "type": "reaction_added",
                "user": JAY,
                "reaction": "+1",
            },
        }
        response = _signed_post(client, payload)
        assert response.status_code == 200
        assert db_state.human_action_calls == []

    def test_reaction_missing_required_subfields(
        self, client: TestClient, db_state: _DbState
    ) -> None:
        # item present but ts/reaction missing
        payload = {
            "type": "event_callback",
            "event": {
                "type": "reaction_added",
                "user": JAY,
                "item": {"type": "message"},  # no ts
            },
        }
        response = _signed_post(client, payload)
        assert response.status_code == 200
        assert db_state.human_action_calls == []


class TestThreadReply:
    def test_jay_thread_reply_appends_human_note(
        self, client: TestClient, db_state: _DbState
    ) -> None:
        db_state.matching_ids[PARENT_TS] = ROW_ID
        response = _signed_post(
            client, _thread_reply_event(text="looks good to me")
        )
        assert response.status_code == 200
        assert db_state.append_note_calls == [(PARENT_TS, "looks good to me")]

    def test_non_jay_thread_reply_ignored(
        self, client: TestClient, db_state: _DbState
    ) -> None:
        db_state.matching_ids[PARENT_TS] = ROW_ID
        response = _signed_post(
            client, _thread_reply_event(text="hi", user=NOT_JAY)
        )
        assert response.status_code == 200
        assert db_state.append_note_calls == []

    def test_top_level_message_ignored(
        self, client: TestClient, db_state: _DbState
    ) -> None:
        # Message event with NO thread_ts → not a reply, ignored.
        payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel": "C0001",
                "user": JAY,
                "text": "just chatting",
                "ts": "1700000003.000100",
            },
        }
        response = _signed_post(client, payload)
        assert response.status_code == 200
        assert db_state.append_note_calls == []

    def test_message_with_subtype_ignored(
        self, client: TestClient, db_state: _DbState
    ) -> None:
        # Edited / deleted messages have a subtype; bridging those back is
        # out of scope per spec.
        db_state.matching_ids[PARENT_TS] = ROW_ID
        response = _signed_post(
            client,
            _thread_reply_event(text="edited", subtype="message_changed"),
        )
        assert response.status_code == 200
        assert db_state.append_note_calls == []

    def test_bot_message_ignored(
        self, client: TestClient, db_state: _DbState
    ) -> None:
        # Our own bot's thread replies (when the bridge posts a reply on
        # an agent's behalf) come back through this webhook with bot_id.
        db_state.matching_ids[PARENT_TS] = ROW_ID
        response = _signed_post(
            client, _thread_reply_event(text="bot reply", bot_id="B0001")
        )
        assert response.status_code == 200
        assert db_state.append_note_calls == []

    def test_thread_reply_no_matching_parent(
        self, client: TestClient, db_state: _DbState
    ) -> None:
        # Parent ts is not in our DB (reply on someone else's thread).
        response = _signed_post(client, _thread_reply_event(text="hi"))
        assert response.status_code == 200
        assert db_state.append_note_calls == []

    def test_thread_reply_empty_text(
        self, client: TestClient, db_state: _DbState
    ) -> None:
        db_state.matching_ids[PARENT_TS] = ROW_ID
        response = _signed_post(client, _thread_reply_event(text=""))
        assert response.status_code == 200
        assert db_state.append_note_calls == []


class TestMalformedPayload:
    def test_invalid_json_returns_200_no_crash(
        self, client: TestClient, db_state: _DbState
    ) -> None:
        body = b"not valid json {"
        ts = str(int(time.time()))
        verifier = SignatureVerifier(signing_secret=TEST_SECRET)
        sig = verifier.generate_signature(timestamp=ts, body=body.decode())
        response = client.post(
            "/slack/events",
            content=body,
            headers={
                "content-type": "application/json",
                "x-slack-request-timestamp": ts,
                "x-slack-signature": sig,
            },
        )
        assert response.status_code == 200
        assert db_state.human_action_calls == []

    def test_payload_not_a_dict(
        self, client: TestClient, db_state: _DbState
    ) -> None:
        # JSON parses but isn't an object — should not crash.
        response = _signed_post(client, ["unexpected", "list"])  # type: ignore[arg-type]
        assert response.status_code == 200

    def test_event_envelope_missing(
        self, client: TestClient, db_state: _DbState
    ) -> None:
        # event_callback shape but no event object.
        response = _signed_post(client, {"type": "event_callback"})
        assert response.status_code == 200
        assert db_state.human_action_calls == []

    def test_event_envelope_not_a_dict(
        self, client: TestClient, db_state: _DbState
    ) -> None:
        response = _signed_post(
            client, {"type": "event_callback", "event": "not-a-dict"}
        )
        assert response.status_code == 200

    def test_unhandled_event_type(
        self, client: TestClient, db_state: _DbState
    ) -> None:
        # Some random event type we never subscribe to but Slack delivers.
        response = _signed_post(
            client,
            {
                "type": "event_callback",
                "event": {"type": "team_join", "user": {"id": "U_NEW"}},
            },
        )
        assert response.status_code == 200
        assert db_state.human_action_calls == []
