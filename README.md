# Relayy

FastAPI backend + React frontend.

## Delivery analysis and design

Start with [the documentation index](docs/README.md) for the current-code assessment,
requirements gaps, happy paths, edge cases, architecture diagrams, and proposed
implementation plan. The assessment records the pre-implementation baseline.
Use [provider setup and recovery](docs/delivery-setup.md) for the current implementation.
Real-provider verification remains separate from offline test results.

```
backend/    Python 3.11 · FastAPI · uvicorn · Anthropic SDK · SQLModel (SQLite)
frontend/   React · Vite · TypeScript · Tailwind CSS
```

## Prerequisites

- Python 3.11+
- Node.js 20.19+ (or 22.12+) and npm

## 1. Environment variables

Copy the example file at the repo root and fill in your keys:

```bash
cp .env.example .env
```

| Variable               | Purpose                                     |
| ---------------------- | ------------------------------------------- |
| `ANTHROPIC_API_KEY`    | Claude API key                              |
| `SLACK_WEBHOOK_URL`    | Incoming webhook for posting to a channel   |
| `SLACK_SIGNING_SECRET` | Verifies requests sent by Slack             |
| `SLACK_BOT_TOKEN`      | Bot token (`xoxb-...`) for the Slack Web API |
| `SLACK_CHANNEL_ID`     | Channel to post escalations in (e.g. `C0123456789`) |

The backend reads `.env` from the repo root. Missing model credentials or explicit model IDs produce labeled simulated analysis, which cannot authorize automatic provider effects. Without Slack settings, Mission Control still supports review. External delivery defaults to disabled. See [provider setup](docs/delivery-setup.md) before enabling test delivery.

## 2. Backend

```bash
cd backend
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
python seed.py            # fictional fixtures; refuses overwriting provider-bound data
uvicorn main:app --reload
```

- API: http://localhost:8000/api/hello
- Health check: http://localhost:8000/api/health
- Interactive docs: http://localhost:8000/docs

On startup the backend creates its SQLite database at `backend/relayy.db`.

### Demo data

`seed.py` fills the database with data for **Brightwave**, a fictional SaaS company:

- 30 customers on the Free, Pro Monthly, Pro Annual and Enterprise plans (`free` / `pro` / `vip` tiers), with each one's full payment history (including some declined charges with retries, and some refunds)
- Past support tickets with resolutions, 10 help docs, and 3 open tickets for the demo
- **Customer 1, Eleanor Whitfield (VIP):** about $117k lifetime value, with an open, urgent ticket about 429 rate-limit errors
- **Customer 2, Marcus Bell:** double-charged $450 for his Pro Annual renewal, with an open ticket asking for a refund

Seed rows have fixed IDs. Reseeding an untouched offline database replaces those rows. Reseeding or resetting refuses databases with provider bindings or operation audit records. Never delete uncertain provider-operation evidence. Customer IDs differ from ticket IDs: Marcus is customer 2/ticket 1; Eleanor is customer 1/ticket 2.

Tables include `customer`, `payment`, `ticket`, `past_ticket`, `help_doc`, `ticket_run`, `ticket_event`, `delivery_operation`, and `operation_attempt`. Versioned SQLite migration preserves legacy local-only records.

### Agent swarm

`backend/swarm/` processes a ticket in three steps: **triage → research → action**. `orchestrator.run_pipeline(state)` is an async generator that yields a timestamped event as each step starts and finishes, and also for every tool call and policy override, so the UI can stream progress live.

| Step     | Model               | What it does |
| -------- | ------------------- | ------------ |
| Triage   | `ANTHROPIC_FAST_MODEL`  | Classifies the ticket (category, urgency, sentiment, language, whether it's suspicious) |
| Research | `ANTHROPIC_SMART_MODEL` | Calls up to 6 rounds of read-only database tools (`swarm/tools.py`): customer lookup, payments, past tickets, and keyword (BM25) search over the help docs. Produces `key_findings` |
| Action   | `ANTHROPIC_SMART_MODEL` | Proposes a decision (`auto_resolve` / `auto_refund` / `escalate`) and drafts a reply. `swarm/policy.py` then applies hard rules in Python |

The policy rules force `escalate` when the ticket is flagged suspicious, its urgency is critical, the customer is a VIP and angry, either model's confidence is below 0.75, or the model proposes a refund over $50 or one that doesn't match a successful payment. Every rule that fired is recorded in `action.rules_fired`.

The research tools require a trusted customer scope. Missing scope fails closed. Refunds additionally require one exact owned USD test charge, integer cents, verified eligibility evidence, and real-model provenance. Monthly charges and enterprise refunds do not auto-refund. Selected card/phone patterns are sanitized at every model boundary; this is not complete PII removal.

To try triage on 5 sample tickets (needs `ANTHROPIC_API_KEY` in `.env`):

```bash
cd backend
python -m swarm.test_triage
```

### API

| Endpoint | What it does |
| -------- | ------------ |
| `POST /tickets` | Create a ticket (`subject`, `body`, and `customer_email` or `customer_id`) and start the swarm in the background. Returns `202` with the ticket id and its `stream_url` |
| `POST /tickets/{id}/process` | Run (or re-run) the swarm on an existing ticket, such as the seeded demo tickets 1–3. Returns `409` for an active run or any existing delivery operation |
| `GET /tickets` | All tickets with their status, customer, category, urgency, decision and the policy rules that fired |
| `GET /tickets/{id}` | One ticket's status and resolution, plus its full `TicketState` (live while the swarm is running) |
| `POST /tickets/{id}/review` | Approve/edit a reply or reject with current `run_id` and `expected_version`. Requires operator authorization when configured. |
| `POST /tickets/{id}/operations/{operation_id}/{action}` | Use `retry` or `reconcile` only when the server permits the action. |
| `GET /tickets/{id}/runs` | Read immutable run history. |
| `GET /events/stream` | Advisory global status changes; refetch after reconnect. |
| `GET /tickets/{id}/stream` | Server-Sent Events: the ticket's event history, then new events live (see below) |
| `GET /metrics` | Counts by status and decision, automation and escalation rates, escalations by rule, human review outcomes, average processing time |
| `POST /slack/interactions` | Slack button clicks and the Edit modal (see Slack setup) |

**Ticket statuses:**
- `open`: not processed yet.
- `processing`: the swarm is running.
- `escalated`: waiting for review or operational attention. Check `can_review` and operation reasons.
- `pending`: authorized provider work or reconciliation remains incomplete.
- `resolved`: Resend accepted the authorized reply and any dependent Stripe test refund succeeded. This does not prove inbox delivery.
- `rejected`: a human rejected the AI's reply, so the ticket needs manual handling.

When the swarm finishes, its final `TicketState` is saved as JSON on the ticket row (`ticket.swarm_state`).

**Streaming:** the stream sends two kinds of SSE messages. `event: event` messages each carry one agent event. Their `id` is the event's position in the ticket's list, so a reconnecting `EventSource` resumes from where it left off. `event: status` messages carry the ticket's status. The stream stays open after the swarm finishes, so a Slack approval shows up live; close it on the client when you're done. From the browser:

```js
const es = new EventSource("http://localhost:8000/tickets/4/stream");
es.addEventListener("event", (e) => console.log(JSON.parse(e.data)));   // {agent, status, message, timestamp}
es.addEventListener("status", (e) => console.log(JSON.parse(e.data)));  // {status, resolution}
```

CORS is open to the Vite dev server (`FRONTEND_ORIGINS`, default `http://localhost:5173`).

> Events persist with monotonic IDs. A bounded in-memory bus notifies connected clients. Use one uvicorn process.

### Slack setup

When the swarm escalates a ticket, it posts a message to Slack showing:
- the customer, their tier and lifetime value
- the triage summary and key findings
- the policy rule that forced escalation
- the AI's suggested reply

The message has **Approve & Send**, **Edit** and **Reject** buttons. A click updates the ticket, replaces the buttons with a note such as "✅ Approved by @alice", and pushes the event to the frontend's stream. **Edit** opens a modal prefilled with the draft.

> Approval queues only the displayed reply. It never authorizes a refused refund. In test delivery mode, Resend sends to an explicitly approved test recipient. Disabled or unsafe delivery remains blocked; no false send claim appears.

1. **Create the app.** Go to https://api.slack.com/apps → **Create New App** → **From scratch**, and pick your workspace.
2. **Add the bot scope.** Under **OAuth & Permissions → Bot Token Scopes**, add `chat:write`. Then **Install to Workspace** and copy the **Bot User OAuth Token** into `SLACK_BOT_TOKEN`.
3. **Copy the signing secret.** Under **Basic Information → App Credentials**, copy the **Signing Secret** into `SLACK_SIGNING_SECRET`.
4. **Pick the channel.** In Slack, invite the bot to your escalations channel (`/invite @YourApp`). Copy the channel ID from the channel's details into `SLACK_CHANNEL_ID`.
5. **Expose only the interaction route.** Use a path-restricted HTTPS proxy for `/slack/interactions`. Keep ticket, metric, stream, and frontend routes local. Do not tunnel the whole backend. Configure `SLACK_ALLOWED_TEAM_ID` and `SLACK_ALLOWED_REVIEWERS`.
6. **Point Slack at it.** Under **Interactivity & Shortcuts**, turn **Interactivity** on and set the **Request URL** to `https://<your-ngrok-domain>/slack/interactions`. Save.
7. **Restart and test.** Restart uvicorn so it picks up the new `.env` values. `GET /api/health` should report `"slack_configured": true` and `"slack_interactive": true`. Then process a ticket that escalates, for example `POST /tickets/1/process` (Marcus's $450 double charge exceeds the $50 auto-refund limit).

If the restricted proxy URL changes, update the Slack interaction URL.

Slack interactions require a valid recent signature plus an allowed workspace and reviewer. Stale run/revision buttons cannot authorize a later run. Signature verification is not user authorization.

**Webhook-only alternative:** if you set only `SLACK_WEBHOOK_URL` (an incoming webhook from the same app), escalations are posted through the webhook and the buttons still work. **Edit** needs the bot token, though, because opening a modal requires the Web API.

## 3. Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The frontend talks to the backend at `http://localhost:8000` (override with `VITE_API_URL`).

**Mission control** (the default tab):
- **Left:** the ticket inbox, with urgency badges and a status for each ticket (processing, auto-resolved, auto-refunded, escalated, approved or rejected).
- **Center:** the selected ticket and its three agent cards (Triage 🧭, Research 🔎, Action ⚡), linked by animated arrows. The active card glows and streams its events live. Finished cards show their results, including the policy rule that forced an escalation.
- **Right:** a form to submit a ticket, with four one-click demo presets.

| Preset | Customer | Expected outcome |
| ------ | -------- | ---------------- |
| 🔑 Password reset | Marcus Bell | Answer proposal; email requires real-model provenance and approved recipient |
| 💸 $30 refund | Priya Nair | Full Stripe test refund only after exact fixture binding and eligibility checks |
| 😡 Angry VIP double-charge | Eleanor Whitfield (VIP) | Escalated by the `vip_angry` rule |
| 🛡️ Prompt-injection attack | Jordan Lee | Flagged suspicious and escalated |

The presets rely on the seeded customers, so run `python seed.py` first.

**Impact** (the second tab, also reachable at `/#metrics`) shows:
- total tickets, % auto-resolved, % escalated, and the average time to decision
- estimated agent hours saved (`8 minutes` per automated ticket) and estimated cost per ticket
- tickets by category (bar chart) and decisions over time (cumulative step chart)
- why tickets were escalated, model spend by agent, and the human review outcomes from web and Slack

It refreshes on its own whenever a ticket is created or changes status (via `GET /events/stream`).

Model usage persists per run. Unknown or unverified model pricing displays unavailable, not zero. Refund totals count confirmed Stripe test refunds, not proposals or legacy local bookkeeping. Auto-resolved rates count completed authorized flows; time-saved figures remain estimates.
