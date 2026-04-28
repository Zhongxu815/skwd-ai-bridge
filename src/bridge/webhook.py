"""Slack Events API webhook handler.

Endpoint:
  POST /slack/events — receives reaction_added and message events.

Security:
  - Signature is verified for every request EXCEPT URL verification
    challenges. (Echoing back the challenge is harmless, and verifying
    challenges would block re-registering the URL whenever the signing
    secret rotates.)
  - Reactor / replier identity is checked against SLACK_USER_ID_JAY;
    bots reacting to each other's posts are ignored.

Slack contract:
  - Slack expects a 2xx response within 3 seconds for every event,
    including types we don't handle. We always return 200 unless the
    signature check fails (401), so Slack doesn't retry-storm us.
  - Subtype'd messages (edits/deletes, bot_message) are filtered out at
    dispatch — bridging Slack edits back to DB is out of scope per spec.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from slack_sdk.signature import SignatureVerifier

from bridge import db
from bridge.config import Config
from bridge.emoji_map import resolve_emoji

logger = logging.getLogger("bridge.webhook")

router = APIRouter()


@router.post("/slack/events")
async def slack_events(request: Request) -> Any:
    raw_body = await request.body()

    # 1. URL-verification short-circuit: if the body parses as a URL
    #    verification challenge, echo back the challenge value verbatim
    #    BEFORE signature verification. This lets the URL be (re-)registered
    #    even if the signing secret hasn't been wired up on the deploy
    #    target yet.
    challenge = _maybe_url_verification_challenge(raw_body)
    if challenge is not None:
        logger.info("webhook_url_verification")
        return PlainTextResponse(content=challenge)

    # 2. Signature verification for everything else.
    config = _config(request)
    if not config.slack_signing_secret:
        logger.error("webhook_signing_secret_missing")
        raise HTTPException(status_code=401, detail="signing secret not configured")

    verifier = SignatureVerifier(signing_secret=config.slack_signing_secret)
    timestamp = request.headers.get("x-slack-request-timestamp", "")
    signature = request.headers.get("x-slack-signature", "")
    # Guard against missing/non-numeric timestamp before handing to slack-sdk
    # (its is_valid() crashes with ValueError on empty timestamp instead of
    # returning False).
    if not timestamp or not signature or not timestamp.isdigit():
        logger.warning("webhook_signature_missing_or_malformed")
        raise HTTPException(status_code=401, detail="invalid signature")
    if not verifier.is_valid(body=raw_body, timestamp=timestamp, signature=signature):
        logger.warning("webhook_signature_invalid")
        raise HTTPException(status_code=401, detail="invalid signature")

    # 3. Parse and dispatch.
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning("webhook_invalid_json")
        return JSONResponse({"ok": False, "reason": "invalid_json"})

    if not isinstance(payload, dict):
        logger.warning("webhook_payload_not_dict")
        return JSONResponse({"ok": False})

    event_envelope = payload.get("event")
    if not isinstance(event_envelope, dict):
        logger.debug(
            "webhook_unhandled_envelope", extra={"type": payload.get("type")}
        )
        return JSONResponse({"ok": True})

    event_type = event_envelope.get("type")
    pool = request.app.state.db_pool
    jay_id = config.slack_user_id_jay

    if event_type == "reaction_added":
        await _handle_reaction(event_envelope, pool, jay_id)
    elif (
        event_type == "message"
        and event_envelope.get("thread_ts")
        and not event_envelope.get("subtype")
        and not event_envelope.get("bot_id")
    ):
        await _handle_thread_reply(event_envelope, pool, jay_id)
    else:
        logger.debug(
            "webhook_unhandled_event_type",
            extra={
                "event_type": event_type,
                "has_thread_ts": bool(event_envelope.get("thread_ts")),
                "subtype": event_envelope.get("subtype"),
            },
        )

    return JSONResponse({"ok": True})


def _maybe_url_verification_challenge(raw_body: bytes) -> str | None:
    """Return the challenge string if this is a URL verification, else None."""
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("type") != "url_verification":
        return None
    challenge = payload.get("challenge")
    if not isinstance(challenge, str):
        return None
    return challenge


def _config(request: Request) -> Config:
    config: Config = request.app.state.config
    return config


async def _handle_reaction(
    event: dict[str, Any], pool: Any, jay_id: str | None
) -> None:
    item = event.get("item")
    if not isinstance(item, dict):
        logger.warning("reaction_missing_item")
        return

    slack_ts = item.get("ts")
    reactor = event.get("user")
    emoji = event.get("reaction")

    if not slack_ts or not emoji:
        logger.warning(
            "reaction_malformed",
            extra={"slack_ts": slack_ts, "reactor": reactor, "emoji": emoji},
        )
        return

    # b/c. Look up the row first; if not ours, ignore.
    row_id = await db.find_id_by_slack_ts(pool, slack_ts)
    if row_id is None:
        logger.debug("reaction_no_matching_row", extra={"slack_ts": slack_ts})
        return

    # d. Identity check.
    if jay_id is None or reactor != jay_id:
        logger.debug(
            "reaction_from_non_jay_user",
            extra={"reactor": reactor, "row_id": str(row_id)},
        )
        return

    # e/f. Resolve emoji.
    resolved = resolve_emoji(emoji)
    if resolved is None:
        logger.debug(
            "reaction_unknown_emoji",
            extra={"emoji": emoji, "row_id": str(row_id)},
        )
        return

    # g. Apply.
    target, value = resolved
    if target == "human_action":
        await db.apply_human_action(pool, slack_ts, value)
        logger.info(
            "reaction_human_action_applied",
            extra={
                "row_id": str(row_id),
                "slack_ts": slack_ts,
                "emoji": emoji,
                "human_action": value,
            },
        )
    else:  # ("status", "closed")
        await db.set_status_closed(pool, slack_ts)
        logger.info(
            "reaction_status_closed",
            extra={"row_id": str(row_id), "slack_ts": slack_ts, "emoji": emoji},
        )


async def _handle_thread_reply(
    event: dict[str, Any], pool: Any, jay_id: str | None
) -> None:
    parent_ts = event.get("thread_ts")
    author = event.get("user")
    text = event.get("text", "")

    if not isinstance(parent_ts, str) or not parent_ts:
        return

    if not isinstance(text, str) or not text:
        logger.debug("thread_reply_empty_text", extra={"parent_ts": parent_ts})
        return

    parent_id = await db.find_id_by_slack_ts(pool, parent_ts)
    if parent_id is None:
        logger.debug(
            "thread_reply_no_matching_parent", extra={"parent_ts": parent_ts}
        )
        return

    if jay_id is None or author != jay_id:
        logger.debug(
            "thread_reply_from_non_jay_user",
            extra={"author": author, "parent_id": str(parent_id)},
        )
        return

    await db.append_human_note(pool, parent_ts, text)
    logger.info(
        "thread_reply_appended",
        extra={
            "parent_id": str(parent_id),
            "parent_ts": parent_ts,
            "text_length": len(text),
        },
    )
