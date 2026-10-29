# Relayy

FastAPI backend + React frontend.

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

The backend reads `.env` from the repo root. Everything except the AI agents works without keys. Without Slack settings, escalated tickets still wait for review; they just aren't posted to Slack.

## 2. Backend

```bash
cd backend
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
python seed.py            # load demo data (safe to re-run)
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

Every row has a fixed ID and the generator always uses the same random seed, so re-running `seed.py` overwrites the same rows rather than adding duplicates. Dates are calculated back from today, so the demo charges are always recent. To wipe everything and start fresh, run `python seed.py --reset`.

Tables: `customer`, `payment`, `ticket`, `past_ticket`, `help_doc` (see `models.py`).

### Agent swarm

`backend/swarm/` processes a ticket in three steps: **triage → research → action**. `orchestrator.run_pipeline(state)` is an async generator that yields a timestamped event as each step starts and finishes, and also for every tool call and policy override, so the UI can stream progress live.

| Step     | Model               | What it does |
| -------- | ------------------- | ------------ |
| Triage   | `claude-haiku-5-5`  | Classifies the ticket (category, urgency, sentiment, language, whether it's suspicious) |
| Research | `claude-sonnet-5-5` | Calls up to 6 rounds of read-only database tools (`swarm/tools.py`): customer lookup, payments, past tickets, and keyword (BM25) search over the help docs. Produces `key_findings` |
| Action   | `claude-sonnet-5-5` | Proposes a decision (`auto_resolve` / `auto_refund` / `escalate`) and drafts a reply. `swarm/policy.py` then applies hard rules in Python |

The policy rules force `escalate` when the ticket is flagged suspicious, its urgency is critical, the customer is a VIP and angry, either model's confidence is below 0.75, or the model proposes a refund over $50 or one that doesn't match a successful payment. Every rule that fired is recorded in `action.rules_fired`.

The research tools only return data for the customer who sent the ticket.

To try triage on 5 sample tickets (needs `ANTHROPIC_API_KEY` in `.env`):

```bash
cd backend
python -m swarm.test_triage
```

### API

| Endpoint | What it does |
| -------- | ------------ |
| `POST /tickets` | Create a ticket (`subject`, `body`, and `customer_email` or `customer_id`) and start the swarm in the background. Returns `202` with the ticket id and its `stream_url` |
| `POST /tickets/{id}/process` | Run (or re-run) the swarm on an existing ticket, such as the seeded demo tickets 1–3. Returns `409` if it's already running |
| `GET /tickets` | All tickets with their status, customer, category, urgency, decision and the policy rules that fired |
| `GET /tickets/{id}` | One ticket's status and resolution, plus its full `TicketState` (live while the swarm is running) |
| `GET /tickets/{id}/stream` | Server-Sent Events: the ticket's event history, then new events live (see below) |
| `GET /metrics` | Counts by status and decision, automation and escalation rates, escalations by rule, human review outcomes, average processing time |
| `POST /slack/interactions` | Slack button clicks and the Edit modal (see Slack setup) |

**Ticket statuses:**
- `open`: not processed yet.
- `processing`: the swarm is running.
- `escalated`: waiting for a human in Slack.
- `resolved`: the reply was sent, by the AI or after a human approved it.
- `rejected`: a human rejected the AI's reply, so the ticket needs manual handling.

When the swarm finishes, its final `TicketState` is saved as JSON on the ticket row (`ticket.swarm_state`).

**Streaming:** the stream sends two kinds of SSE messages. `event: event` messages each carry one agent event. Their `id` is the event's position in the ticket's list, so a reconnecting `EventSource` resumes from where it left off. `event: status` messages carry the ticket's status. The stream stays open after the swarm finishes, so a Slack approval shows up live; close it on the client when you're done. From the browser:

```js
const es = new EventSource("http://localhost:8000/tickets/4/stream");
es.addEventListener("event", (e) => console.log(JSON.parse(e.data)));   // {agent, status, message, timestamp}
es.addEventListener("status", (e) => console.log(JSON.parse(e.data)));  // {status, resolution}
```

CORS is open to the Vite dev server (`FRONTEND_ORIGINS`, default `http://localhost:5173`).

> Live events go through an in-memory bus, so run a single uvicorn process (the default).

### Slack setup

When the swarm escalates a ticket, it posts a message to Slack showing:
- the customer, their tier and lifetime value
- the triage summary and key findings
- the policy rule that forced escalation
- the AI's suggested reply

The message has **Approve & Send**, **Edit** and **Reject** buttons. A click updates the ticket, replaces the buttons with a note such as "✅ Approved by @alice", and pushes the event to the frontend's stream. **Edit** opens a modal prefilled with the draft.

> No email is actually sent yet. "Send" records the reply on the ticket (`final_reply`).

1. **Create the app.** Go to https://api.slack.com/apps → **Create New App** → **From scratch**, and pick your workspace.
2. **Add the bot scope.** Under **OAuth & Permissions → Bot Token Scopes**, add `chat:write`. Then **Install to Workspace** and copy the **Bot User OAuth Token** into `SLACK_BOT_TOKEN`.
3. **Copy the signing secret.** Under **Basic Information → App Credentials**, copy the **Signing Secret** into `SLACK_SIGNING_SECRET`.
4. **Pick the channel.** In Slack, invite the bot to your escalations channel (`/invite @YourApp`). Copy the channel ID from the channel's details into `SLACK_CHANNEL_ID`.
5. **Expose the backend with ngrok.** Slack needs a public HTTPS URL to reach your laptop. Install ngrok (https://ngrok.com/download), sign up, and run `ngrok config add-authtoken <token>` once. Then, with uvicorn running:

   ```bash
   ngrok http 8000
   ```

   Copy the `https://….ngrok-free.app` forwarding URL.
6. **Point Slack at it.** Under **Interactivity & Shortcuts**, turn **Interactivity** on and set the **Request URL** to `https://<your-ngrok-domain>/slack/interactions`. Save.
7. **Restart and test.** Restart uvicorn so it picks up the new `.env` values. `GET /api/health` should report `"slack_configured": true` and `"slack_interactive": true`. Then process a ticket that escalates, for example `POST /tickets/1/process` (Marcus's $450 double charge exceeds the $50 auto-refund limit).

On ngrok's free plan the URL changes every time ngrok restarts. When it does, update the Request URL in step 6. A free static domain (`ngrok http --url=<your-domain> 8000`) avoids this.

Requests to `/slack/interactions` are rejected with `401` unless they carry a valid Slack signature less than 5 minutes old, so the endpoint is safe to expose.

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
| 🔑 Password reset | Marcus Bell | Auto-resolved |
| 💸 $30 refund | Priya Nair (bought a $30 add-on two days ago) | Auto-refunded (under the $50 limit, matches a real payment) |
| 😡 Angry VIP double-charge | Eleanor Whitfield (VIP) | Escalated by the `vip_angry` rule |
| 🛡️ Prompt-injection attack | Jordan Lee | Flagged suspicious and escalated |

The presets rely on the seeded customers, so run `python seed.py` first.

**Impact** (the second tab, also reachable at `/#metrics`) shows:
- total tickets, % auto-resolved, % escalated, and the average time to decision
- estimated agent hours saved (`8 minutes` per automated ticket) and estimated cost per ticket
- tickets by category (bar chart) and decisions over time (cumulative step chart)
- why tickets were escalated, model spend by agent, and the human review outcomes from Slack

It refreshes on its own whenever a ticket is created or changes status (via `GET /events/stream`).

Cost per ticket comes from the token usage the backend records for every model call (`state.usage` on each ticket). It's an estimate based on list prices in `backend/swarm/llm.py`. "Auto-resolved" counts tickets the AI closed without a human, including auto-refunds. Tickets processed before token tracking was added show no cost.
