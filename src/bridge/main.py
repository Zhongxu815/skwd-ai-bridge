"""FastAPI app entrypoint: lifespan-managed DB pool + /health.

For Milestone 1 the app only serves /health. Polling and webhook routes
are wired in later milestones.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pythonjsonlogger.json import JsonFormatter

from bridge import poller, webhook
from bridge.config import Config, load_config
from bridge.db import close_pool, create_pool, healthcheck

logger = logging.getLogger("bridge")


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"levelname": "level", "asctime": "ts"},
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = load_config()
    setup_logging(config.log_level)
    logger.info(
        "startup",
        extra={
            "poll_interval": config.poll_interval_seconds,
            "poll_batch_size": config.poll_batch_size,
            "bot_tokens_present": sorted(config.bot_tokens.keys()),
        },
    )
    pool = await create_pool(config.database_url)
    app.state.config = config
    app.state.db_pool = pool

    if not config.bot_tokens:
        logger.warning("no_bot_tokens_configured_poller_will_skip_all_rows")
    poller_task = asyncio.create_task(
        poller.run_forever(pool, config), name="bridge_poller"
    )
    app.state.poller_task = poller_task

    try:
        yield
    finally:
        logger.info("shutdown")
        poller_task.cancel()
        try:
            await poller_task
        except asyncio.CancelledError:
            pass
        await close_pool(pool)


app = FastAPI(lifespan=lifespan, title="Skwd AI Bridge")
app.include_router(webhook.router)


def _pool(app: FastAPI) -> asyncpg.Pool:
    pool: asyncpg.Pool = app.state.db_pool
    return pool


def _config(app: FastAPI) -> Config:
    config: Config = app.state.config
    return config


@app.get("/health")
async def health(request: Request) -> JSONResponse:
    db_ok = await healthcheck(_pool(request.app))
    return JSONResponse(
        status_code=200 if db_ok else 503,
        content={
            "status": "ok" if db_ok else "degraded",
            "db": "connected" if db_ok else "disconnected",
        },
    )
