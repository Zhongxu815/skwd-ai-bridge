# Skwd AI Bridge

The connector between Postgres (truth) and Slack (interface) for Skwd AI's
agent message bus.

- **Postgres → Slack:** polls `agent_messages` for unposted rows, routes to
  the right channel, posts as the right bot, records the resulting Slack
  message timestamp.
- **Slack → Postgres:** receives Slack reaction and thread-reply events via
  webhook and updates `human_action`, `human_note`, or `status` accordingly.

The full design is in [`bridge-service-spec.md`](./bridge-service-spec.md).

## Stack

- Python 3.11+
- FastAPI for HTTP + webhook handler
- asyncpg for Postgres
- slack-sdk for Slack Web API + signature verification
- pytest for tests

## Run locally

```sh
# 1. Set up a virtual env and install dev deps
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# 2. Copy and fill in env vars
cp .env.example .env
# Edit .env — at minimum, set DATABASE_URL

# 3. Run the service
export $(grep -v '^#' .env | xargs)
uvicorn bridge.main:app --reload --port 8000
```

Then in another shell:

```sh
curl -s localhost:8000/health
# → {"status":"ok","db":"connected"}
```

## Env vars

See [`.env.example`](./.env.example). Required-at-startup is `DATABASE_URL`.
Slack-related vars (`SLACK_SIGNING_SECRET`, `SLACK_USER_ID_JAY`, bot tokens)
are required when their subsystem comes online (poller in M3, webhook in M4)
and the service will fail fast at that point if any are missing.

## Tests

```sh
pytest                    # run all
pytest --cov=bridge       # with coverage
ruff check                # lint
mypy                      # type-check
```

## Deploy to Railway

Documented at the end of the build (Milestone 5). The bridge runs as a
sibling Railway service in the `skwd-ai` project, sharing the same
Postgres database.
