# Provider-backed demo setup and recovery

The runtime remains a single-process, trusted-local hackathon demonstration. Do not expose the frontend or read/SSE API publicly. CORS and the demo operator token do not provide production user isolation.

## Modes

- `DELIVERY_MODE=disabled` is the default. Agent analysis and human review can run without external effects. Missing or failed model calls produce explicitly simulated analysis. Provider operations remain blocked.
- `DELIVERY_MODE=test` enables the configured real-provider adapters after safety checks. Stripe must use a sandbox/test account. Resend sends real email, so approve each recipient explicitly.
- No live payment mode exists. No automatic provider action trusts simulated model output.
- `operator_required=false` permits the offline demo without an unlock token. Provider-enabled mode requires a token of at least 32 characters.

## Install and start locally

From the repository root:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements-dev.txt
cp .env.example .env
cd backend
python seed.py
uvicorn main:app --host 127.0.0.1 --port 8000
```

Start a second shell:

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5173
```

Open `http://localhost:5173` on the same machine. The backend listens at `http://127.0.0.1:8000`. Use one uvicorn worker. Do not substitute `0.0.0.0` or publish the whole API through a tunnel.

If Python cannot create a virtual environment because `ensurepip` is absent, install the operating system's Python venv package. An isolated `virtualenv` installation is an alternative. Do not replace system Python packages.

## Secure prerequisites

Store credentials through secure secret configuration or a local ignored `.env`. Never put credentials in `VITE_*`, URLs, screenshots, source files, or browser storage.

| Setting | Requirement |
| --- | --- |
| `ANTHROPIC_API_KEY` | Account access and credits for a real-model demonstration. |
| `ANTHROPIC_FAST_MODEL`, `ANTHROPIC_SMART_MODEL` | Explicit account-supported model IDs. There is no guessed default. |
| `DELIVERY_MODE` | Set to `test` only after fixture setup and safe configuration. |
| `DEMO_OPERATOR_TOKEN` | A dedicated random token with at least 32 characters. |
| `RESEND_API_KEY` | A dedicated demo-account key with send and email-retrieval permissions. |
| `RESEND_FROM` | A sender permitted by Resend. Domain verification or test-sender restrictions apply. |
| `RESEND_ALLOWED_RECIPIENTS` | Comma-separated approved test inboxes or documented Resend simulator addresses. |
| `STRIPE_SECRET_KEY` | `sk_test_` or `rk_test_` key. Live-mode keys are forbidden. |
| `STRIPE_ACCOUNT_ID` | Exact expected account ID returned by Stripe. |
| `STRIPE_API_VERSION` | Explicit API version. The default adapter contract uses `2025-02-24.acacia`; verify against the configured account. |
| `FRONTEND_ORIGINS` | Exact permitted local frontend origins. |

Health reports configuration separately from verified access. A configured API key is not proof of a successful provider call. Unknown model pricing remains unavailable rather than a fabricated zero-cost estimate.

## Bind approved fixtures explicitly

Never use the fictional seeded customer email as a destination. Never guess a Stripe charge from an amount or description.

For email-only scenarios, map the local customer to one approved test recipient:

```bash
cd backend
python demo_setup.py --customer-id 4 --recipient delivered@resend.dev
```

The recipient must already appear in `RESEND_ALLOWED_RECIPIENTS`. Confirm that the selected sender permits that recipient.

For the $30 add-on scenario, first create an owned, captured, unrefunded USD 30 Stripe test charge. The Stripe customer must represent the intended test identity. Obtain its `ch_...` and `cus_...` IDs from Stripe. Use explicit values, not the placeholders below:

```bash
python demo_setup.py \
  --customer-id 3 \
  --recipient delivered@resend.dev \
  --payment-id <local-payment-id> \
  --charge-id <stripe-test-charge-id> \
  --stripe-customer-id <expected-stripe-customer-id> \
  --purchase-type addon \
  --workspace-id demo-workspace \
  --billing-period 2026-10 \
  --product-id demo-addon
```

Inspect the local customer payments before choosing the exact local payment ID. The command checks local ownership, exact amount, provider account/customer, USD, capture state, test mode, no existing refund, and provider charge creation time. The command does not send email or issue a refund.

Purchase type and workspace/product/billing evidence are trusted operator fixture declarations. The model cannot create that evidence. Use only synthetic, owned test data. The fixture command does not provision a duplicate-charge exception.

Create a new ticket after binding. Existing seeded tickets predate fixture verification and cannot establish new refund authorization. A charge supports only one full refund operation. Use a new owned test charge and a fresh scenario for a second refund demonstration.

Binding changes and reseeding are refused when operation audit records exist. Back up the SQLite database before upgrades. Do not delete a database that contains uncertain or completed provider operations.

## Demonstrate the flows

1. Check health and configuration readiness.
2. Unlock mutations with the configured operator token when required.
3. Select one of the four existing presets.
4. Read per-stage model/simulation provenance and policy rules.
5. Approve or edit a safe displayed holding reply when review is required.
6. Inspect the provider operation rows and recorded provider IDs.

Password reset can demonstrate an answer through Resend. The $30 add-on requires a valid exact fixture and real-model provenance for automatic Stripe execution. Angry VIP and prompt-injection scenarios require review. Model responses can vary; a preset name does not guarantee a specific classification.

Approve/Edit on an escalation authorizes the displayed reply only. It never overrides a denied refund. The $450 scenario remains a holding-reply flow, not a financial override.

## Interpret results correctly

- Email `accepted` means Resend accepted a message. It does not prove inbox delivery.
- Refund `succeeded` means a retrieved/confirmed Stripe test refund succeeded. This demo moves no real money.
- Refund `pending`, `unknown`, `failed`, or `canceled` is not a successful refund.
- A confirmed refund plus failed email is partial success. Recover email only.
- A holding reply can be accepted while the original issue still needs manual follow-up.
- Legacy resolved/refunded records represent local-only outcomes and do not count as provider success.

## Recovery rules

Use only server-authorized Retry or Reconcile controls. Recovery keeps the original operation, request payload, and idempotency key. It never changes recipient, payment, or refund amount.

A timeout may follow a successful provider action. Reconcile before any new attempt. Unknown email requests without a provider ID stop automatic resends after a conservative 23-hour window. Provider key retention is finite, so the system does not promise exactly-once effects.

Startup preserves accepted/succeeded operations and marks interrupted sends unknown. A bounded worker resumes queued operations and checks pending/unknown results. The runtime separates a six-submission budget from twelve scheduled read checks. A transient read failure does not erase a confirmed refund. An operator can request a read-only reconciliation after scheduled checks stop. Manual investigation remains necessary when evidence is ambiguous or the send budget expires.

Analysis reruns stop after any delivery operation exists. Earlier runs and monotonic persisted events remain available through `GET /tickets/{id}/runs`. A browser reconnect retrieves authoritative state rather than assuming all SSE messages arrived.

## Optional Slack review

Configure the existing bot token, signing secret, channel, `SLACK_ALLOWED_TEAM_ID`, and `SLACK_ALLOWED_REVIEWERS`. The reviewer setting is a comma-separated Slack user-ID allowlist. A valid signature alone does not authorize a reviewer.

Expose only `/slack/interactions` through a path-restricted HTTPS proxy. Do not expose `/tickets`, `/metrics`, SSE, or the whole application. Buttons and edit modals carry run/revision identity. Stale clicks cannot approve a later run. Web review remains available if Slack setup is absent.

## Verification commands

```bash
python -m pytest -q
cd frontend
npm test
npm run build
npm run lint
```

Offline tests use fake provider transports and temporary databases. A passing offline suite does not prove provider access. Real-provider verification requires an approved recipient, a mapped test charge, correct account permissions, and actual provider IDs/results. Report unavailable provider or model checks as blocked.
