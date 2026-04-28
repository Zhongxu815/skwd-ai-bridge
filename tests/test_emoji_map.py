from __future__ import annotations

import pytest

from bridge.emoji_map import EMOJI_TO_ACTION, resolve_emoji


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
def test_human_action_emojis(emoji: str, expected_action: str) -> None:
    assert resolve_emoji(emoji) == ("human_action", expected_action)


def test_close_emoji_sets_status() -> None:
    assert resolve_emoji("white_check_mark") == ("status", "closed")


@pytest.mark.parametrize(
    "emoji",
    [
        "fire",
        "rocket",
        "tada",
        "thumbsup",  # Slack uses "+1" for 👍, not "thumbsup"
        "",
        "WHITE_CHECK_MARK",  # case-sensitive
    ],
)
def test_unknown_emoji_returns_none(emoji: str) -> None:
    assert resolve_emoji(emoji) is None


def test_emoji_table_matches_spec() -> None:
    expected_keys = {
        "+1",
        "x",
        "octagonal_sign",
        "eye",
        "repeat",
        "arrow_up",
        "arrow_down",
        "eyes",
        "brain",
    }
    assert set(EMOJI_TO_ACTION.keys()) == expected_keys
    # white_check_mark must NOT be in the human_action map
    assert "white_check_mark" not in EMOJI_TO_ACTION
