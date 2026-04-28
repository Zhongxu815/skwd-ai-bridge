"""Tests for bridge.slack_client.

We don't hit real Slack. AsyncWebClient is replaced with a stub that
records calls and replays a scripted sequence of responses/exceptions.
asyncio.sleep is also stubbed so the test runs instantly.
"""

from __future__ import annotations

from typing import Any

import pytest
from slack_sdk.errors import SlackApiError

from bridge import slack_client


class _FakeResponse:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


class _FakeClient:
    def __init__(self, script: list[Any]) -> None:
        # script entries are either response dicts or BaseException instances.
        self.script = script
        self.calls: list[dict[str, Any]] = []

    async def chat_postMessage(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        idx = min(len(self.calls) - 1, len(self.script) - 1)
        item = self.script[idx]
        if isinstance(item, BaseException):
            raise item
        return _FakeResponse(item)


class _Stub:
    def __init__(self) -> None:
        self.client: _FakeClient | None = None
        self.sleeps: list[float] = []


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> _Stub:
    state = _Stub()

    async def fake_sleep(seconds: float) -> None:
        state.sleeps.append(seconds)

    monkeypatch.setattr(slack_client.asyncio, "sleep", fake_sleep)
    return state


def _install_client(
    monkeypatch: pytest.MonkeyPatch, stub: _Stub, script: list[Any]
) -> _FakeClient:
    client = _FakeClient(script)
    stub.client = client

    def constructor(token: str) -> _FakeClient:
        return client

    monkeypatch.setattr(slack_client, "AsyncWebClient", constructor)
    return client


def _slack_err(error: str = "ratelimited") -> SlackApiError:
    return SlackApiError(
        f"slack returned {error}",
        response=_FakeResponse({"error": error, "ok": False}),
    )


class TestPostMessage:
    async def test_success_first_try(
        self, stub: _Stub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _install_client(monkeypatch, stub, [{"ts": "111.222"}])
        ts = await slack_client.post_message(
            token="xoxb-A",
            channel="#chan",
            blocks=[],
            fallback_text="hi",
        )
        assert ts == "111.222"
        assert len(client.calls) == 1
        assert stub.sleeps == []
        assert client.calls[0]["channel"] == "#chan"
        assert client.calls[0]["text"] == "hi"
        assert client.calls[0]["thread_ts"] is None

    async def test_thread_ts_passed_through(
        self, stub: _Stub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _install_client(monkeypatch, stub, [{"ts": "1"}])
        await slack_client.post_message(
            token="xoxb-A",
            channel="#chan",
            blocks=[],
            fallback_text="hi",
            thread_ts="parent-ts",
        )
        assert client.calls[0]["thread_ts"] == "parent-ts"

    async def test_success_after_one_failure(
        self, stub: _Stub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _install_client(
            monkeypatch, stub, [_slack_err(), {"ts": "222.333"}]
        )
        ts = await slack_client.post_message(
            token="xoxb-A",
            channel="#chan",
            blocks=[],
            fallback_text="hi",
        )
        assert ts == "222.333"
        assert len(client.calls) == 2
        assert stub.sleeps == [1.0]

    async def test_all_attempts_fail_raises_last(
        self, stub: _Stub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _install_client(
            monkeypatch,
            stub,
            [_slack_err(), _slack_err(), _slack_err(), _slack_err()],
        )
        with pytest.raises(SlackApiError):
            await slack_client.post_message(
                token="xoxb-A",
                channel="#chan",
                blocks=[],
                fallback_text="hi",
                max_retries=4,
            )
        assert len(client.calls) == 4
        assert stub.sleeps == [1.0, 2.0, 4.0]

    async def test_generic_exception_retried_then_succeeds(
        self, stub: _Stub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _install_client(
            monkeypatch, stub, [RuntimeError("net blip"), {"ts": "ok"}]
        )
        ts = await slack_client.post_message(
            token="xoxb-A",
            channel="#chan",
            blocks=[],
            fallback_text="hi",
        )
        assert ts == "ok"
        assert len(client.calls) == 2

    async def test_no_ts_in_response_retries(
        self, stub: _Stub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Response is 200 OK but missing ts: treat as SlackPostError, retry.
        client = _install_client(monkeypatch, stub, [{}, {"ts": "999.0"}])
        ts = await slack_client.post_message(
            token="xoxb-A",
            channel="#chan",
            blocks=[],
            fallback_text="hi",
        )
        assert ts == "999.0"
        assert len(client.calls) == 2

    async def test_no_ts_exhausted_raises_slack_post_error(
        self, stub: _Stub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_client(monkeypatch, stub, [{}, {}, {}, {}])
        with pytest.raises(slack_client.SlackPostError):
            await slack_client.post_message(
                token="xoxb-A",
                channel="#chan",
                blocks=[],
                fallback_text="hi",
                max_retries=4,
            )

    async def test_max_retries_one_no_sleeps(
        self, stub: _Stub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Single attempt, no retries → no sleeps.
        client = _install_client(monkeypatch, stub, [RuntimeError("once")])
        with pytest.raises(RuntimeError):
            await slack_client.post_message(
                token="xoxb-A",
                channel="#chan",
                blocks=[],
                fallback_text="hi",
                max_retries=1,
            )
        assert len(client.calls) == 1
        assert stub.sleeps == []

    async def test_backoff_saturates_for_higher_max_retries(
        self, stub: _Stub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # max_retries=6 with a 4-entry backoff → last 2 sleeps repeat the
        # final entry.
        client = _install_client(
            monkeypatch,
            stub,
            [RuntimeError("net")] * 6,
        )
        with pytest.raises(RuntimeError):
            await slack_client.post_message(
                token="xoxb-A",
                channel="#chan",
                blocks=[],
                fallback_text="hi",
                max_retries=6,
                backoff=(1.0, 2.0, 4.0, 8.0),
            )
        assert len(client.calls) == 6
        assert stub.sleeps == [1.0, 2.0, 4.0, 8.0, 8.0]

    async def test_slack_error_with_no_response_attr_logged(
        self,
        stub: _Stub,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # SlackApiError with response=None should still log without crashing.
        err = SlackApiError("unknown", response=None)
        _install_client(monkeypatch, stub, [err, {"ts": "ok"}])
        import logging

        caplog.set_level(logging.WARNING)
        ts = await slack_client.post_message(
            token="xoxb-A",
            channel="#chan",
            blocks=[],
            fallback_text="hi",
        )
        assert ts == "ok"
        warns = [r for r in caplog.records if r.message == "slack_post_failed"]
        assert len(warns) == 1
        assert getattr(warns[0], "slack_error") is None
