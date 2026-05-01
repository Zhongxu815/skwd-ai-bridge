# Skwd AI — Bridge Service Specification

**Status:** Approved for implementation
**Owner:** Jay
**Last updated:** Apr 30, 2026

---

## Purpose

The bridge service is the connector between Postgres (truth) and Slack (interface). It does two things:

1. **Postgres → Slack:** Polls `agent_messages` for unposted rows, routes each to the right channel, posts as the right bot, records the resulting Slack message timestamp.
2. **Slack → Postgres:** Receives Slack reaction events via webhook, finds the corresponding message by `slack_ts`, updates `human_action` (or `status`) accordingly.

It does **not** make routing decisions for agents, trigger agent runs, or interpret message payloads beyond what's needed for channel routing and Slack formatting.

## Non-goals

- The bridge does not run agents. Agents poll their own inbox and act on `human_action` themselves.
- The bridge does not retry failed agent work. It only retries failed Slack posts.
- The bridge does not maintain in-memory state between polls. Postgres is the only state.

---

## Architecture

```
┌──────────────────┐                     ┌────────────────┐
│   agent_messages │                     │     Slack      │
│   (Postgres)     │ ◄──── poll/update ──┤    Bridge      ├──── post ────► Slack channels
│                  │                     │   (FastAPI,    │
│                  │ ◄──── update ───────┤    Python)     │ ◄── webhook ── Slack Events API
└──────────────────┘                     └────────────────┘
```

One Python FastAPI service. Two concurrent loops: an async polling loop and an HTTP server for webhooks.

Deploy as a sibling Railway service in the `skwd-ai` project, sharing the same Postgres.

---

## Channel topology

Three job-specific channels plus per-agent direct channels. Each channel has a single, clear purpose:

| Channel | Purpose | Default destination? |
|---|---|---|
| `#squad-bus` | All inter-agent messages, threaded by conversation | Yes — default for agent-to-agent traffic |
| `#squad-escalations` | Things Jay must see | Only via Ollie or P0 backstop |
| `#squad-infra` | Argus production health monitoring | Argus only |
| `#agent-{name}` | 1:1 Jay ↔ that agent direct conversation | Only when Jay or that agent specifically addresses each other |

**Rationale:**

- `#squad-bus` is where agents talk to each other. It's a firehose by design — Jay can read it when curious but does not need to track it.
- `#squad-escalations` is the only channel that pings Jay. Ollie is the primary curator (she reads the bus and decides what crosses over). The bridge keeps a P0 backstop in case Ollie is offline or misses something.
- `#agent-{name}` channels exist for direct, intentional conversation between Jay and a specific agent — agents asking Jay non-urgent questions, sending him weekly summaries, or Jay initiating a chat with a specific agent. These are low-traffic by design.

This replaces the earlier "every agent posts to its own channel by default" model. That model was a firehose-per-agent that required Jay to track N channels; the new model centralizes inter-agent chatter in one place and reserves per-agent channels for direct conversation.

---

## Configuration

All config via environment variables. No config files.

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | Postgres connection string | (required) |
| `POLL_INTERVAL_SECONDS` | How often to poll `agent_unposted` | `5` |
| `POLL_BATCH_SIZE` | Max rows to fetch per poll | `10` |
| `SLACK_SIGNING_SECRET` | Verifies inbound webhooks | (required) |
| `SLACK_BOT_TOKEN_AUDRA` | OAuth token for Audra bot | (required) |
| `SLACK_BOT_TOKEN_JULES` | OAuth token for Jules bot | (required) |
| `SLACK_BOT_TOKEN_OLLIE` | OAuth token for Ollie bot | (required) |
| `MAX_POST_RETRIES` | Retry attempts before marking `failed` | `4` |
| `LOG_LEVEL` | Python logging level | `INFO` |

Tokens for agents not yet deployed (Cash, Lex, Harper, Atlas, Digby, Argus, Dex) are added as those agents come online. The bridge logs a warning and skips messages from agents whose token is missing — it doesn't crash.

---

## Decision 1 — Polling cadence

5 seconds. Configurable via `POLL_INTERVAL_SECONDS`.

Worst-case latency from row insert to Slack post: ~5s plus Slack API time.

---

## Decision 2 — Channel routing

Apply rules in order. First match wins.

```
1. IF slack_channel is explicitly set
   THEN channel = slack_channel              (override)

2. ELSE IF priority = 'p0'
   THEN channel = '#squad-escalations'        (P0 backstop — never miss a true emergency)

3. ELSE IF to_agent = 'jay'
        AND message_type IN ('escalation', 'approval_required')
   THEN channel = '#squad-escalations'

4. ELSE IF to_agent = 'jay'
   THEN channel = '#agent-{from_agent}'       (agent talking to Jay specifically, non-urgent)

5. ELSE IF from_agent = 'jay'
   THEN channel = '#agent-{to_agent}'         (Jay talking to a specific agent)

6. ELSE IF from_agent = 'argus'
   THEN channel = '#squad-infra'

7. ELSE
   channel = '#squad-bus'                     (default for agent-to-agent traffic)
```

**Rules explained:**

- **Rule 1 (override):** Lets a row deliberately target a specific channel (e.g., `#squad-ops`, `#squad-boardroom`).
- **Rule 2 (P0 backstop):** Bridge guarantees Jay sees true emergencies even if Ollie is offline. Any row marked P0 lands in escalations regardless of message_type or to_agent.
- **Rule 3 (escalation/approval to Jay):** Standard escalation path — Ollie or another agent explicitly raising something to Jay.
- **Rule 4 (non-urgent message to Jay):** Agent has something for Jay specifically that isn't urgent — weekly report, design question, FYI. Lands in that agent's direct channel where Jay can read at his own pace.
- **Rule 5 (Jay to a specific agent):** Jay-initiated direct message to an agent. Lands in that agent's channel.
- **Rule 6 (Argus):** Production health stays in one place regardless of message type.
- **Rule 7 (default):** Agent-to-agent traffic goes to the shared bus.

**Notes on what this means:**

- Audra's PR review (`from_agent='audra'`, `to_agent='ollie'`, `message_type='task_result'`, P2) → rule 7 → `#squad-bus`. Correct.
- Audra asking Jay "should I be stricter about X?" (`to_agent='jay'`, `message_type='query'`, P2) → rule 4 → `#agent-audra`. Direct conversation, not noise.
- Ollie escalating something Audra found (`from_agent='ollie'`, `to_agent='jay'`, `message_type='escalation'`, P1) → rule 3 → `#squad-escalations`. Standard path.
- A genuine P0 from any agent → rule 2 → `#squad-escalations`. Backstop.

---

## Decision 3 — Bot identity per channel

Each Slack post is sent **as the `from_agent` bot**. The bridge looks up the bot token by `from_agent` value (`audra` → `SLACK_BOT_TOKEN_AUDRA`, etc.).

This means: an Audra-authored task_result in `#squad-bus` is posted by the Audra bot. An Ollie-authored escalation in `#squad-escalations` is posted by the Ollie bot. Author identity is preserved across channels — important for `#squad-bus`, where many agents post into the same channel and the bot identity is the only signal of who's speaking.

If `from_agent` is `jay` (Jay-authored direction-setting), the message gets a generic bot identity (TBD — placeholder for now). This is rare and only relevant once Jay sends messages through the bus rather than just receiving them.

---

## Decision 4 — Slack post failure handling

When a Slack post fails:

1. Increment an in-memory retry counter for that row's id.
2. Sleep with exponential backoff: 1s, 2s, 4s, 8s. (Capped at 4 retries — `MAX_POST_RETRIES`.)
3. After 4 failed attempts, update the row: `status = 'failed'`. Log the last error. Move on to the next row.
4. Do not attempt that row again on subsequent polls.

The `agent_unposted` view filters by `status = 'pending'`, so once a row is marked `failed` it leaves the bridge's queue automatically.

A future Argus check can scan `WHERE status = 'failed'` and surface failures for human review. Out of scope for the bridge.

**What counts as a Slack post failure:** any non-2xx response from Slack, any exception during the request. The bridge does not distinguish 4xx from 5xx in v1 — both retry the same way. We can add smarter error classification later if it matters.

---

## Decision 5 — Slack → Postgres (reactions)

The bridge exposes one HTTP endpoint:

```
POST /slack/events
```

It receives Slack Events API webhooks. Verifies signing secret on every request (mandatory — non-negotiable security boundary).

Subscribes to two event types:

- `reaction_added` — Jay reacts with an emoji
- `message` (in thread) — Jay replies in thread with a free-text note

### `reaction_added` handler

1. Look up the message by `event.item.ts` (Slack message timestamp). Match against `agent_messages.slack_ts`.
2. If no match, log and ignore. (Could be a reaction on a non-bridge message.)
3. If the reactor is not Jay's Slack user ID, log and ignore. (Bots reacting to each other's posts is out of scope; only humans drive `human_action`.)
4. Map the emoji to an action via the table below.
5. Apply the update.

### Emoji mapping

| Emoji | Effect |
|---|---|
| 👍 `+1` | `human_action = 'approved'` |
| ❌ `x` | `human_action = 'rejected'` |
| 🛑 `octagonal_sign` | `human_action = 'held'` |
| 👁 `eye` | `human_action = 'flagged'` |
| 🔁 `repeat` | `human_action = 'redirected'` |
| ⬆️ `arrow_up` | `human_action = 'escalated'` |
| ⬇️ `arrow_down` | `human_action = 'de_escalated'` |
| ✅ `white_check_mark` | `status = 'closed'` (NOT a human_action) |
| 👀 `eyes` | `human_action = 'acknowledged'` |
| 🧠 `brain` | `human_action = 'annotated'` (Harper note) |

Emojis not in this table are ignored (logged, not errored).

The 👍/❌/🛑/etc. effects all go to `human_action`. The lifecycle-timestamp trigger on `agent_messages` does NOT fire for `human_action` updates alone — it only fires when `status` changes. So setting `human_action = 'approved'` does not auto-advance status. That's intentional: agents decide what to do with the approval on their next inbox read, and they update status themselves.

The ✅ emoji is the exception: it sets `status = 'closed'` directly, which fires the trigger and stamps `closed_at`. This is the human equivalent of "this thread is done, file it."

### `message` (thread reply) handler

1. Look up the parent message by `event.thread_ts`. Match against `agent_messages.slack_ts`.
2. If no match, ignore.
3. If the author is not Jay, ignore.
4. Append the message text to `human_note` (or set it if currently null). Use newlines to separate multiple notes.

`human_note` is free-form text. Length-bounded by Postgres TEXT (effectively unlimited) but the UI will truncate when displaying.

---

## Database access patterns

The bridge runs exactly four query shapes against Postgres. Listed here so the connection-pooling and transaction semantics are explicit.

### Query 1 — poll for unposted

```sql
SELECT id, thread_id, reply_to, from_agent, to_agent, message_type,
       priority, subject, payload, requires_response, slack_channel
FROM agent_unposted
ORDER BY priority_order, created_at
LIMIT $1;
```

Run every `POLL_INTERVAL_SECONDS`. `slack_channel` selected so the override is visible to the bridge.

### Query 2 — mark a row as posted

```sql
UPDATE agent_messages
SET slack_channel = $1,
    slack_ts = $2,
    slack_thread_ts = $3
WHERE id = $4;
```

Run once per successful Slack post. `slack_thread_ts` is set when the post is in a thread (e.g., a reply); same as `slack_ts` for top-level posts.

### Query 3 — mark a row as failed

```sql
UPDATE agent_messages
SET status = 'failed'
WHERE id = $1;
```

Run after `MAX_POST_RETRIES` exhausted. Triggers the lifecycle-timestamp logic, but `status='failed'` doesn't have a corresponding timestamp column — that's fine, the trigger only acts on `read`/`acted`/`closed`.

### Query 4 — apply human reaction

```sql
UPDATE agent_messages
SET human_action = $1
WHERE slack_ts = $2;
```

Or, for ✅:
```sql
UPDATE agent_messages
SET status = 'closed'
WHERE slack_ts = $1;
```

Or, for thread replies:
```sql
UPDATE agent_messages
SET human_note = COALESCE(human_note || E'\n', '') || $1
WHERE slack_ts = $2;
```

All four queries use the indexes defined in `001_initial_schema.sql`. No new indexes needed.

---

## Connection pooling

Use `asyncpg` with a pool of 5 connections. The polling loop and the webhook handler share the pool. Railway's hobby Postgres allows ~20 concurrent connections; 5 is well under that and leaves headroom for Legacy Ops, ad-hoc psql sessions, and future agent processes.

---

## Slack message format

For v1, post messages as Slack `blocks` with a consistent structure. Compact, scannable, debuggable.

Top-level layout per post:

```
[Bot name] sent at [time]

*[subject]*
[message_type] · [priority] · → [to_agent]

[short summary derived from payload]

[footer with row id and links if applicable]
```

Implementation: keep formatting in a single `format_message(row) -> List[Block]` function. Different message_types may use different payload fields for the summary line:

| message_type | summary source |
|---|---|
| `task` | `payload.action_requested` + `acceptance_criteria` (1 line each) |
| `task_result` | `payload.summary` |
| `query` | `payload.question` |
| `query_response` | `payload.answer` (truncate to 200 chars) |
| `proposal` | `payload.title` + `rationale` (1 line) |
| `escalation` | `payload.what_i_need` |
| `notification` | `payload.event` + key fields from `details` |
| `approval_required` | `payload.action_pending` + `payload.summary` |
| `debug_diagnosis` | `payload.bug_summary` + `root_cause` (1 line) |

Footer always shows: `id` (truncated to 8 chars), and a hint about how to react (one-line legend on first message of each thread, optional).

In `#squad-bus` specifically, where many agents post into the same channel, the `→ [to_agent]` indicator is essential — it's how a reader sees at a glance who a message is meant for.

---

## Threading

If `agent_messages.reply_to IS NOT NULL`, the bridge looks up the parent's `slack_ts` and posts the reply as a Slack thread reply (`thread_ts = parent.slack_ts`). The new message's own `slack_thread_ts` is set to the parent's `slack_ts`.

If the parent has no `slack_ts` yet (parent failed to post, or hasn't been posted yet in this poll), the reply is held back — the bridge skips it on this iteration and tries again next poll. This means replies can briefly lag their parents but will always land in-order.

After `MAX_POST_RETRIES` worth of waiting (i.e., a parent stuck in `failed`), the reply gets posted as a top-level message in its target channel with a note in the footer that the parent failed. This prevents a single bad row from blocking an entire conversation.

**Threading discipline matters more in `#squad-bus`.** A flat firehose of cross-agent messages would be unreadable. Agent prompts must enforce "always set `reply_to` when responding to a previous message in a chain" so that conversations stay grouped as Slack threads inside the bus channel.

---

## Logging

Structured JSON logs. One log line per:

- Poll cycle (debug level: how many rows fetched)
- Each row processed (info: id, from→to, channel, outcome)
- Each Slack post failure (warn: id, attempt #, error)
- Each row marked `failed` (error: id, last error)
- Each inbound webhook (info: event type, message id matched)
- Each ignored event (debug: reason)

No PII concerns — payloads are agent-to-agent, not user data. Full payloads safe to log.

---

## Local development

The bridge runs locally against the same Railway Postgres (no separate dev DB for v1). Slack events from a local run go nowhere (no public webhook URL). Use `pytest` with mocked Slack client for testing the polling loop in isolation.

For end-to-end testing: deploy to Railway as a draft service, point Slack Events API at the Railway URL, react in the real Slack workspace, watch the DB update.

---

## Testing checklist (post-deploy)

Manual end-to-end verification before declaring v1 done:

1. INSERT a new task_result from Audra to Ollie (P2). Wait <10s. Confirm it appears in `#squad-bus`.
2. Verify the row's `slack_ts` is now populated.
3. React with 👍 in Slack. Wait <5s. Confirm `human_action = 'approved'` in DB.
4. Reply to the Slack message in-thread with "looks good." Confirm `human_note` contains "looks good."
5. INSERT a P0 escalation from any agent to Jay. Confirm it lands in `#squad-escalations` (rule 2 backstop).
6. INSERT a P1 escalation from Ollie to Jay. Confirm it lands in `#squad-escalations` (rule 3).
7. INSERT a P2 query from Audra to Jay. Confirm it lands in `#agent-audra` (rule 4 — direct conversation).
8. INSERT a row from Argus. Confirm it lands in `#squad-infra`.
9. INSERT a row with `slack_channel = '#squad-boardroom'` set. Confirm the override wins.
10. Force a failure (set a bogus token for one bot temporarily, INSERT a row from that bot). Confirm 4 retries in logs, then `status = 'failed'` in DB. Confirm the queue keeps moving.
11. INSERT a reply (a row with `reply_to` set) where parent is in `#squad-bus`. Confirm it threads under the parent in `#squad-bus`.

---

## Out of scope for v1

- Smarter Slack error classification (4xx vs 5xx)
- Slack message edits when a row is updated post-post
- Bridging Slack edits/deletes back to the DB
- Multi-workspace support
- Metrics/observability beyond logs
- Authentication beyond Slack signing secret (no admin endpoints exposed)
- Backfill: if the bridge is down for hours, it catches up by polling normally on restart. No special replay logic.
- Ollie's escalation logic — Ollie's bus-watching and "does Jay need this?" decision-making is a separate agent, deferred to its own session. Until Ollie ships, the only path from agent → Jay is the P0 backstop (rule 2) plus explicit `to_agent='jay'` messages from agents (rules 3 and 4).

---

## Estimated implementation

- ~250 lines of Python (FastAPI + asyncpg + slack-sdk)
- 1 main module + 1 formatter module + 1 router module + tests
- Build time via Claude Code: 2-3 hours including local testing
- Deploy time: ~30 min (Railway service config, env vars, Slack webhook registration)
