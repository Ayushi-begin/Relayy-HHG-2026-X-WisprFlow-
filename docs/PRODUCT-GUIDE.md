# Relayy: Product Guide and Q&A

Plain-language guide for demos, interviews and judges. Everything here describes what the code does today.

---

## 1. What is Relayy? (30 seconds)

Relayy is a **team of AI agents that handles customer-support tickets**. A customer writes in. Three agents read the ticket, check the facts in the company's records, and propose a reply, and sometimes a refund. **A hard-coded safety layer and a human stay in control of anything that touches money or goes to a customer.**

The demo company is **Brightwave**, a fictional analytics product. All customers and payments are made-up fixtures.

**One-line pitch:** *AI does the reading and drafting. Rules and humans decide what actually happens.*

---

## 2. The problem it solves

Support teams lose time on tickets that are easy to answer but slow to research: "I was charged twice", "how do I export my data". Fully automatic AI is risky, because it can be tricked, can invent facts, and can refund the wrong money.

Relayy keeps the speed of AI and removes the risk by splitting the work:

| Job | Who does it |
|---|---|
| Read, classify, research, draft | AI agents |
| Decide what is allowed | Python rules (not AI) |
| Approve anything sensitive | A human |

---

## 3. How a ticket flows

```
Ticket arrives
   │
   ▼
[1] TRIAGE agent      → category, urgency, mood, language, "is this suspicious?"
   │
   ▼
[2] RESEARCH agent    → looks up the customer, payments, past tickets, help docs
   │                    (read-only tools, limited to THAT customer's data)
   ▼
[3] ACTION agent      → proposes: auto_resolve | auto_refund | escalate, plus a reply draft
   │
   ▼
[4] POLICY (Python)   → can override the AI. Forces escalation when risky.
   │
   ▼
[5] HUMAN REVIEW      → approve / edit / reject (web UI or Slack)
   │
   ▼
[6] DELIVERY          → email (Resend) and refund (Stripe), test mode only, recorded safely
```

### The three agents

- **Triage** is the fast reader. It sorts the ticket and flags manipulation ("ignore your rules", fake staff messages, odd refund demands).
- **Research** is the investigator. It calls tools to check the customer's plan, recent payments, history and help docs. It can only see the ticket sender's own records.
- **Action** is the writer. It proposes a decision and drafts the reply in the customer's language.

### The safety layer (the important part)

Policy rules in plain Python force a ticket to a human when, for example:
- the ticket was flagged suspicious,
- urgency is critical,
- an angry VIP is involved,
- the AI's confidence is low,
- a proposed refund amount is not safe.

**The AI's answer is a suggestion, never an authorization.** Money and emails only move after the rules pass and, where required, a human approves.

---

## 4. What you can see in the app

| Page | What it shows |
|---|---|
| **Mission control** | Ticket inbox, the three agents working live, the result, review buttons |
| **Impact** | Real numbers: tickets handled, how many were automated, escalation reasons, human review outcomes, estimated cost |
| **Live activity** | A real-time feed of every agent step, status change, review and delivery update. It catches up after a reconnect |
| **Agents** | Each agent's profile, real run statistics, recent executions, and an on/off switch for its real model calls |
| **Providers** | Which AI providers are set up, their health, and which agent uses which model |
| **Appearance** | 10 themes, accent color, density, font size, reduced motion, collapsible sidebar |

Keyboard: **Alt+1 to Alt+6** switch pages.

---

## 5. Under the hood

| Layer | Technology |
|---|---|
| Frontend | React, Vite, TypeScript, Tailwind, Recharts, Framer Motion |
| Backend | Python, FastAPI, SQLModel |
| Database | SQLite locally, **Postgres (Neon)** in production |
| AI | Gemini today; the code also supports Claude and OpenAI behind one interface |
| Live updates | Server-Sent Events (SSE) with replay |
| Email / refunds | Resend / Stripe, test mode only |
| Review | Web UI, and Slack with signed requests |
| Hosting | Vercel (frontend), Render (backend), Neon (database) |

### Design ideas worth mentioning

- **Provider independence.** Agents talk to one neutral interface. Switching Gemini, Claude or OpenAI is a settings change, with retries, timeouts and health tracking.
- **Honest labelling.** Every agent step is marked `real_model`, `simulated` or `failed`. Simulated output is never presented as real.
- **Durable events.** Every event is saved to the database first, then broadcast. A browser that reconnects asks "what did I miss?" and gets it.
- **Prompt-injection defence.** Ticket text is wrapped and labelled as untrusted data, sensitive patterns are redacted before reaching the model, and a pattern check flags manipulation.
- **Customer isolation.** Research tools are scoped to the ticket sender. They cannot read another customer's records.
- **Audit trail.** Every operator action (create, process, review, retry, agent switch) is logged with who did it.

---

## 6. Honest limits

- The demo data is **fictional**. Email and refunds run against **test accounts only**, and delivery is **disabled by default**.
- Costs show as **"unavailable"** unless you add verified prices. Relayy never invents a dollar figure.
- Live-update and rate-limit state is kept in one process, so run **one backend instance**. Scaling out needs a shared message store first.
- Free hosting sleeps when idle (see FAQ).
- There is **no agent marketplace or bidding**. Relayy is a support-ticket product.
- Login is a single shared operator token, not per-user accounts.

---

# Q&A: Likely Questions

## About the product

**Q: What does Relayy do?**
It reads support tickets with AI agents, checks the facts in company records, and proposes a reply or refund. Rules and humans approve anything sensitive.

**Q: Who is it for?**
Support teams that want AI speed without letting AI act on its own.

**Q: Is the AI making refund decisions?**
No. It proposes. Python rules can override it, and a refund only goes through after the rules pass and a human approves. Everything runs in Stripe **test mode**.

**Q: Why three agents instead of one?**
Each has one narrow job, a smaller prompt and its own tools. That makes behaviour easier to test, cheaper and safer. For example, only Research has tools, and they are read-only.

**Q: What happens if the AI is down or has no key?**
The agents fall back to clearly labelled **simulated** analysis. The ticket still flows, but it is marked simulated and can never trigger real email or refunds.

**Q: What is "simulated" mode?**
Deterministic local logic used when no real model is available. It exists so the app is demoable and testable without keys, and it is always labelled as such.

**Q: Can I turn an agent's AI off?**
Yes, on the **Agents** page. It needs the operator token, and the change is audit-logged.

## About AI and safety

**Q: Which AI models does it use?**
Gemini (free tier) in the current setup. The code also has Claude and OpenAI adapters, switched by environment variables.

**Q: How do you stop prompt injection?**
Four layers. (1) Ticket text is wrapped as untrusted data and the model is told to ignore instructions inside it. (2) Sensitive patterns like card and phone numbers are redacted first. (3) A pattern check flags manipulation attempts. (4) Even if the model is fooled, **policy and human review sit between it and any action**.

**Q: Can the AI see other customers' data?**
No. Research tools are locked to the ticket sender's customer id and fail closed if it is missing.

**Q: What if the AI returns garbage?**
Output is validated against a strict schema. Invalid or incomplete output becomes a labelled failure and the ticket goes to a human.

**Q: What if the AI refuses?**
It is recorded as a refusal and the ticket escalates to a human.

**Q: Are API keys exposed to the browser?**
No. Keys live only on the backend. The Providers page shows whether a provider is configured, never the key.

**Q: Can I trust the cost numbers?**
They appear only if you configure verified prices. Otherwise they show as unavailable.

## About money and delivery

**Q: Does it send real emails or real refunds?**
Only to test recipients and Stripe test mode, and only if you turn delivery on. It ships **disabled**.

**Q: What stops a double refund?**
Each delivery is saved with a unique key before anything is sent, and the database enforces one operation per payment. Retries reuse the same key.

**Q: What if a request times out and we don't know if it worked?**
The system **checks with the provider before ever retrying**. Unknown outcomes are never blindly repeated.

**Q: Does it guarantee exactly-once delivery?**
No. It is strongly duplicate-resistant, but providers keep idempotency keys for a limited time, so it makes no exactly-once claim.

## About the technology

**Q: Why SSE instead of WebSockets?**
The data only flows server to browser. SSE is simpler, reconnects automatically, and works well through hosting proxies.

**Q: What if the browser disconnects?**
It reconnects and sends the id of the last event it saw. The server replays what it missed from the database. Duplicates are dropped by id.

**Q: Why no Redis?**
It is not used today, and I did not add it just to tick a box. Live updates run in one process and are backed by the database for replay. Scaling to several instances would need a shared broker, and that is the main future-work item.

**Q: How does the database work in production?**
Neon Postgres, with a pooled connection for the app and a direct one for migrations. Locally it uses SQLite with no setup.

**Q: How are database changes handled?**
Versioned, additive migrations. They only add tables, columns and indexes, never drop data, and they refuse to guess if they find an unversioned database.

**Q: How is it tested?**
Backend: 99 tests passed locally (providers, retries, routing, policy, migrations, lifecycle, security, agents API). Frontend: 13 tests passed. 6 Postgres tests exist but were **skipped locally** because no Postgres was available; they are set to run in GitHub Actions. I have **not** run the app against real Gemini, Slack, Resend or Stripe credentials in this environment.

**Q: What does CI do?**
GitHub Actions runs the backend tests with a real Postgres service, then frontend lint, tests and build, then a Docker build. Deploys only run on `main` after all of those pass.

## About the design

**Q: How do the themes work?**
Every color comes from one set of theme values. Switching theme rewrites those values on the page, so every screen updates instantly. There are 10 themes plus "follow system". Your choice is saved in your browser.

**Q: Is it accessible?**
It has keyboard navigation, focus outlines, a skip link, labelled controls, a reduced-motion option and a High Contrast theme. It has not had a formal accessibility audit.

## About deployment

**Q: Why can't the whole thing run on Vercel?**
Vercel runs short-lived functions. The backend needs a process that stays alive for long-open live streams, background agent runs and the delivery worker. So: **Vercel = frontend, Render = backend, Neon = database.**

**Q: What are the free-tier catches?**
Render's free backend sleeps after 15 idle minutes and takes about a minute to wake, and Render says free instances are not for production. Neon's free database pauses after 5 idle minutes. Vercel Hobby is for non-commercial use. Wake up the demo before presenting.

**Q: Where do secrets go?**
`.env` locally (never committed), and the Render dashboard in production. `.env.example` is only a template with empty values.

**Q: What is the operator token?**
A shared password that unlocks actions like creating tickets, reviewing and switching agents. Reading pages needs no token. It is held in memory only and never stored in the browser.

**Q: Can several people use it?**
For reading, yes. Actions share one operator token, so audit entries show the same operator name. Per-user accounts are future work.

## Tough questions

**Q: What would you build next?**
1. A shared message broker (Redis) so the backend can run on several instances.
2. Per-user login and roles.
3. Verified model prices for real cost tracking.
4. Provider health that survives restarts.
5. A browser-based end-to-end test suite.

**Q: What is the biggest risk?**
Believing AI output. The design treats the model as an untrusted suggestion engine, and the rules and humans are the safety boundary.

**Q: What's the most interesting engineering problem?**
Making external actions safe when networks fail: saving a frozen request first, using idempotency keys, reconciling unknown outcomes, and never retrying blindly.

**Q: Is this an "agent marketplace with bidding"?**
No. It is a support-ticket agent swarm. There is no bidding system, and I would not claim one.

**Q: What is verified, and what isn't?**
Verified: the offline tests, the backend boot check and the frontend build. Not verified: live runs against Gemini, Slack, Resend and Stripe, the six Postgres tests, and an actual deployment to Render, Neon and Vercel.
