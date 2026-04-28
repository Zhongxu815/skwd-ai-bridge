"""Environment-driven configuration loaded once at startup."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Agents whose Slack bot tokens may appear in env. The first three are the
# currently-deployed bots; the rest come online over time. Missing tokens
# are not a startup failure — the poller logs a warning and skips messages
# from agents whose token is absent.
KNOWN_AGENTS: tuple[str, ...] = (
    "audra",
    "jules",
    "ollie",
    "cash",
    "lex",
    "harper",
    "atlas",
    "digby",
    "argus",
    "dex",
)


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    database_url: str
    poll_interval_seconds: int
    poll_batch_size: int
    max_post_retries: int
    log_level: str
    slack_signing_secret: str | None
    slack_user_id_jay: str | None
    bot_tokens: dict[str, str] = field(default_factory=dict)


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def load_config() -> Config:
    """Load and validate config from environment variables.

    For Milestone 1, only DATABASE_URL is required at startup. Slack-related
    requirements are validated by their respective subsystems (poller, webhook)
    when they start.
    """
    bot_tokens: dict[str, str] = {}
    for agent in KNOWN_AGENTS:
        token = os.environ.get(f"SLACK_BOT_TOKEN_{agent.upper()}")
        if token:
            bot_tokens[agent] = token

    return Config(
        database_url=_required("DATABASE_URL"),
        poll_interval_seconds=_int("POLL_INTERVAL_SECONDS", 5),
        poll_batch_size=_int("POLL_BATCH_SIZE", 10),
        max_post_retries=_int("MAX_POST_RETRIES", 4),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        slack_signing_secret=os.environ.get("SLACK_SIGNING_SECRET") or None,
        slack_user_id_jay=os.environ.get("SLACK_USER_ID_JAY") or None,
        bot_tokens=bot_tokens,
    )
