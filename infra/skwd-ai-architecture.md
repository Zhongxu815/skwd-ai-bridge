# Skwd AI — Architecture & Strategic Decisions

## NAME CLARIFICATION (read this first)

**Skwd AI** and **Jay's AI Squad** are the SAME THING. Not two systems, not parent/child, not a rename-that-changed-anything. Same project, same architecture, same nine agents.

**Origin:** Throughout most of the design phase, the working title was "Jay's AI Squad" because no business name had been chosen. On April 28, 2026, Jay registered the domain `skwd-ai.com` and adopted **Skwd AI** as the official business name. "Skwd" is a phonetic spelling of "squad." The name change is brand-only — every architectural, strategic, and naming decision made under "Jay's AI Squad" applies unchanged to "Skwd AI."

**If you encounter older documents using "Jay's AI Squad" — read them as "Skwd AI."** They're describing the exact same thing.

The Squad itself (the team of 9 AI agents) does not have a different name. It's just "the Squad" — the collective noun for the agents that run inside Skwd AI.

---

## What Skwd AI is

Skwd AI is a 9-agent AI team (10 with Dex deferred to Month 6-7) that helps Jay BUILD software systems. Today, the system they're building is Legacy Pilates Ops (the operating system for his wife Nicole's Pilates studio). In the future, it could be other systems for other customers.

**Key relationship to remember:**

```
Jay (CEO) → manages → Skwd AI Squad → builds → Legacy Pilates Ops → serves → Nicole
```

The Squad does NOT serve Nicole directly. Nicole interacts only with Legacy Pilates Ops via Telegram. The Squad is Jay's IT/exec team — they build, review, monitor, and operate the software product Nicole uses.

---

## The Squad (the 9 agents)

Two-tier organization:

```
EXECUTIVES (report to Jay, can message Jay directly)
├── Ollie    — COO. Coordinates execution agents, routes messages, proposes refinements.
├── Lex      — CSO. Prioritizes Legacy Ops roadmap based on signals from Nicole + data.
├── Cash     — CFO. Tracks Squad spend + Legacy Ops costs/revenue. Weekly burn reports.
└── Harper   — CHRO. Reviews agent performance, proposes prompt tuning. Quarterly reviews.

EXECUTION (report to Ollie, must escalate through Ollie)
├── Audra    — Reviewer. Reviews every Legacy Ops PR. (LIVE since April)
├── Jules    — Debug. Triages bugs, diagnoses (does not write code). (NEXT TO DEPLOY)
├── Atlas    — Planner. Turns Lex's priorities into sprint work.
├── Digby    — Digest. Generates Jay's weekly briefing.
└── Argus    — Monitor. Watches Legacy Ops production health.

DEFERRED to Month 6-7
└── Dex      — Coder. Executes Jules's diagnoses. Writes code, opens PRs.
              Deferred because autonomous coding is the highest-risk agent.
```

---

## The five-layer architecture

Skwd AI sits in this stack:

```
LAYER 1 — Coordination & messaging
  Postgres `agent_messages` table (source of truth)
  + Slack workspace (skwd-ai.slack.com) (interface)
  + Bridge service (Postgres ↔ Slack glue)

LAYER 2 — Agent runtimes
  Per-agent: system prompt + memory + LLM call
  Each agent picks its own model based on cost/quality
  Audra → Claude Sonnet, Cash → Claude Haiku (cheap), etc.

LAYER 3 — Tools
  MCP servers + custom tools per agent
  Some tools wrap non-Claude APIs:
    - Image generation → Gemini Nano Banana
    - Voice/TTS → ElevenLabs
    - GitHub access → MCP GitHub server
    - Etc.

LAYER 4 — Human interface
  Slack (live coordination, ambient + escalations)
  Custom dashboard (data visualizations: Ops view, HR view, Board Room, Agent Detail)

LAYER 5 — End user (Jay only)
  Reads Slack on phone/desktop
  Reads dashboard on web
  Manages the Squad as CEO
```

Layer 1 (coordination) is the keystone. Build this right and everything else can be added incrementally.

---

## The 13 strategic decisions already made

These are settled. The next chat should not re-debate any of these without strong reason to revisit:

### 1. Squad serves Jay, not Nicole
The Squad is Jay's IT/build organization. Nicole interacts only with Legacy Pilates Ops. Don't conflate.

### 2. Postgres = truth, Slack = interface
The `agent_messages` table in Postgres is where all coordination data lives. Slack is a view on top — agents post to Slack as a side-effect of writing to Postgres. Slack can be swapped out (Discord, custom UI, etc.) without losing history. Postgres is non-negotiable.

### 3. NOT custom UI (for messaging)
We considered building a custom Message Feed surface in the dashboard. Rejected — Slack is already excellent at chat, free, mobile-friendly, and saves ~3 weeks of UI development. Custom dashboard is reserved for data-heavy surfaces (Ops view, HR view, Board Room, Agent Detail) where Slack would be inadequate.

### 4. NOT A2A protocol
A2A (Linux Foundation standard, originally Google) is for cross-vendor, cross-organization agent communication. Skwd AI is single-tenant, single-organization, single-vendor (mostly). A2A is overhead without value here. Reconsider in Year 2 when commercializing.

### 5. NOT Google ADK or OpenAI Agents SDK
Both lock the Squad into vendor ecosystems. Skwd AI is built on a thin custom runtime so each agent can use the right model. ADK would be heavyweight; OpenAI SDK assumes embedding in OpenAI stack.

### 6. Single bridge service for all agents (not one per agent)
One Python FastAPI service polls Postgres and posts to Slack on behalf of all agents. Simpler to deploy and maintain than 9 separate bridges.

### 7. Polling, not LISTEN/NOTIFY
Bridge polls `agent_messages` table every 3-5 seconds for new messages. Postgres LISTEN/NOTIFY would be slightly faster but more complex. Polling is fine for 9 agents and ~50-200 messages/day.

### 8. Raw SQL files for migrations (no Alembic)
Numbered SQL files in a `migrations/` folder, run in order via psql or Railway query UI. Add Alembic in Year 2 when commercializing and the schema gets more complex. Solo-dev simplicity wins for now.

### 9. Model-agnostic by design
Each agent picks its own LLM provider. Audra on Claude, Cash on Haiku (cheap), Lex possibly on a stronger reasoning model. Image-generation needs (e.g., for content) might use Gemini Nano Banana via tool. Optimize per-agent over time as cost/quality data accumulates. Do NOT pick models upfront.

### 10. Escalation discipline (peer-first → Ollie → Jay)
Agents try to solve themselves first → query a peer → escalate to Ollie → only Ollie escalates to Jay. Execution agents CANNOT message Jay directly. The bridge enforces this. Ollie absorbs complexity that would otherwise hit Jay's attention.

### 11. Merge authority (Ollie executes, Jay strategic)
Ollie merges execution-layer PRs (bug fixes, planned sprint work, prompt refinements approved by Jay). Jay merges strategic/architectural changes (new features, agent add/remove, schema changes, anything touching auth). Enforced via PR labels.

### 12. Dex (the coder) deferred to Month 6-7
The 10th agent is the only one who writes code autonomously. He's the highest-risk agent. Deploy after Ollie coordinates well, Audra is reliable, Argus is watching for incidents. Until then, Jay (using Claude Code) is the "hands" for code execution.

### 13. 8-month timeline (Dec 23, 2026), aggressive 6-month privately
Publicly committed to 8 months. Privately planning for 6 months and shipping what's real in month 8. Buffer is for unknowns (messaging bus complexity, agent prompt iteration, real-world surprises). No external customers in first 8 months — Legacy Ops is the lab.

---

## The data model (in one diagram)

```
┌─────────────────────────────────────────────────────┐
│ POSTGRES — agent_messages table                     │
│                                                     │
│ Every row = one agent-to-agent communication       │
│                                                     │
│ Key columns:                                        │
│  id, thread_id, reply_to                            │
│  from_agent, to_agent                               │
│  message_type, priority, subject, payload (JSONB)   │
│  slack_channel, slack_ts (mirror tracking)          │
│  status (pending → read → acted → closed)           │
│  human_action (Jay's reactions: approved, held, …)  │
└────────────────────┬────────────────────────────────┘
                     │
                     │ Bridge polls every 3-5 sec
                     ▼
┌─────────────────────────────────────────────────────┐
│ BRIDGE SERVICE (Python FastAPI on Railway)          │
│                                                     │
│ Direction 1: Postgres → Slack                       │
│   1. Find rows where slack_ts IS NULL               │
│   2. Determine target channel via routing rules     │
│   3. Post to Slack as the right bot                 │
│   4. Save returned slack_ts back to row             │
│                                                     │
│ Direction 2: Slack → Postgres                       │
│   1. Receive Slack Events webhook                   │
│   2. On reaction_added: find msg by slack_ts,       │
│      update human_action field                      │
│   3. On thread reply: insert new agent_messages     │
│      row with reply_to = parent                     │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│ SLACK WORKSPACE — skwd-ai.slack.com                 │
│                                                     │
│ Squad-level channels (4):                           │
│   #squad-ops          — routine coordination        │
│   #squad-escalations  — needs Jay (only one with    │
│                         notifications on)          │
│   #squad-boardroom    — strategic threaded debates  │
│   #squad-infra        — Argus production alerts     │
│                                                     │
│ Per-agent channels (3 active, 6 more as deployed):  │
│   #agent-audra, #agent-jules, #agent-ollie (live)   │
│   #agent-cash, #agent-lex, #agent-harper,           │
│   #agent-atlas, #agent-digby, #agent-argus,         │
│   #agent-dex (deploy as agents come online)         │
│                                                     │
│ Bot identities:                                     │
│   Each agent = one Slack bot user with distinct     │
│   name, avatar, color, OAuth tokens                 │
└─────────────────────────────────────────────────────┘
                     │
                     ▼
                   JAY
              (reads/reacts via
              Slack desktop + mobile)
```

---

## Message types (9 types, formal contract)

The Squad uses exactly 9 message types. Adding new ones requires a proposal-approve cycle:

| Type | Sender | Recipient | Purpose |
|---|---|---|---|
| `task` | Mainly Ollie | Anyone | Delegate work |
| `task_result` | Recipient of task | Original sender | Report completion |
| `query` | Anyone | Anyone | Ask a peer a question (the workhorse — peer-first problem solving) |
| `query_response` | Anyone | Querier | Answer a query |
| `proposal` | Mostly executives | Whoever can approve | Suggest a change/decision |
| `escalation` | Anyone | Higher authority | Flag stuck-needing-help (must include `what_i_tried` field) |
| `notification` | Anyone | Anyone | FYI, no response needed |
| `approval_required` | Mostly Ollie | Jay | Needs explicit yes/no |
| `debug_diagnosis` | Jules ONLY | Dex / Ollie / Jay (by risk tier) | Structured bug analysis, includes risk_tier (simple/ambiguous/architectural) |

Three priorities: P0 (interrupt), P1 (today), P2 (ambient).

---

## Reaction emoji legend (Jay's intervention language)

When Jay reacts in Slack, the bridge updates the `human_action` field on the corresponding Postgres row. This is the primary way Jay intervenes without typing.

| Emoji | Action |
|---|---|
| 👍 | Approve / proceed |
| 🛑 | Hold / pause |
| 👁 | Flag for later review |
| 🔁 | Redirect (wrong recipient) |
| ⬆️ | Promote priority |
| ⬇️ | Lower priority |
| ✅ | Resolved / close thread |
| ❌ | Reject / don't proceed |
| 👀 | I'm looking at this |
| 🧠 | Good insight (Harper note) |

---

## Infrastructure (where things live)

| Component | Location | Status |
|---|---|---|
| Domain | `skwd-ai.com` | Registered Apr 28 |
| Slack workspace | `skwd-ai.slack.com` | Created, 7 channels, 3 bots installed |
| Railway project | `skwd-ai` | Created |
| Postgres | Railway (in skwd-ai project) | Provisioned |
| `agent_messages` table | Postgres | NOT YET — migrations failed silently in Railway UI |
| Bridge service | Will live in Railway (skwd-ai project) | NOT BUILT YET |
| GitHub repo | TBD | NOT CREATED YET — should be `skwd-ai` or similar |
| Bot tokens | Jay's local text file | Saved (Audra, Jules, Ollie) |

---

## What's been deferred (intentionally)

These are decisions explicitly DELAYED, not forgotten:

- **Custom Message Feed in dashboard** — Slack handles this. Add only if Slack proves inadequate.
- **Multi-tenancy** — Single tenant for now. Add `tenant_id` column when commercializing in Year 2.
- **A2A protocol** — Year 2 consideration when external customers integrate their agents.
- **Per-agent model optimization** — Defaults are fine. Optimize when real cost/quality data exists (Month 4-5).
- **Self-serve onboarding** — Year 2 commercialization concern.
- **Cross-Squad coordination** — Only matters with multiple customers.
- **Cost controls / hard caps** — Cash will track spend; explicit caps designed when needed.
- **Slack-to-Telegram fallback** — Nice-to-have for P0 escalations when Slack is down. Not Month 1.

---

## Where the build is right now (Apr 28, 2026)

**Foundation deployed:**
- ✅ Strategy + roadmap (8-month plan, 4 commitments, scope dial)
- ✅ Message schema (9 types, escalation rules, merge authority)
- ✅ Slack layout (12 channels, posting conventions, emoji legend)
- ✅ Domain registered
- ✅ Slack workspace + 3 bots installed
- ✅ Railway project + Postgres provisioned

**Blocker (current):**
- ⚠️ Postgres migrations were pasted into Railway's query UI three times. Railway said "Query ran successfully" each time. But the Data tab shows zero tables exist (only a leftover diagnostic table). Migrations silently failed.

**Resolution path:**
- Run migrations through Claude Code (which uses psql and shows real errors). The migration SQL itself is correct.

**Then immediately:**
- Build the bridge service (~250 lines Python, ~2-3 hours via Claude Code)
- End-to-end test (manual INSERT → Slack → emoji reaction → DB update)
- Retrofit Audra to use the new messaging table
- Deploy Jules natively
- Then Ollie (the keystone agent prompt)

---

## How to use this doc in the new chat

Paste this entire document at the top of your message. Then say:

> Read this and confirm you understand:
> - Skwd AI = Jay's AI Squad (same project, just renamed)
> - The 5-layer architecture (Postgres truth, Slack interface, per-agent runtimes, tools, human layer)
> - The 13 strategic decisions are settled — don't re-debate
> - I'm currently blocked on Postgres migrations failing silently in Railway
> - Help me draft the Claude Code prompt to run the migrations properly

The other Claude should be at full architecture context in 60 seconds.
