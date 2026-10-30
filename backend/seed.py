"""Seed the SQLite database with realistic demo data for "Brightwave", a fictional SaaS company.

Usage (from backend/):
    python seed.py           # upsert all seed rows; safe to run repeatedly
    python seed.py --reset   # drop and recreate every table first

Idempotency: every row has a fixed primary key and all randomness comes from a
fixed seed, so re-running overwrites the same rows instead of adding new ones.
Dates are relative to today, so runs on the same day produce identical data.

Demo scenarios baked in:
  * Customer 1 (Eleanor Whitfield) is a VIP with a very high lifetime value.
  * Customer 2 (Marcus Bell) was double-charged $450 for his Pro Annual renewal.
  * Customer 3 (Priya Nair) bought a $30 add-on by mistake two days ago (auto-refund demo).
  * Customer 4 (Jordan Lee) is a free-tier user (password reset / prompt-injection demos).
"""

import argparse
import datetime as dt
import os
import random
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlmodel import Session, SQLModel, func, select

from db import create_db_and_tables, engine
from models import (
    Customer,
    DeliveryOperation,
    HelpDoc,
    PastTicket,
    Payment,
    PaymentStatus,
    Ticket,
    TicketStatus,
    Tier,
)

RNG_SEED = 2026
NUM_CUSTOMERS = 30
VIP_CUSTOMER_ID = 1
DOUBLE_CHARGED_CUSTOMER_ID = 2
ACCIDENTAL_ADDON_CUSTOMER_ID = 3
ACCIDENTAL_ADDON_AMOUNT = 30.00
FREE_DEMO_CUSTOMER_ID = 4
DOUBLE_CHARGE_AMOUNT = 450.00


@dataclass(frozen=True)
class Plan:
    name: str
    tier: Tier
    price: float
    interval_days: int | None  # None = never billed


PLANS = {
    p.name: p
    for p in [
        Plan("Free", Tier.free, 0.00, None),
        Plan("Pro Monthly", Tier.pro, 49.00, 30),
        Plan("Pro Annual", Tier.pro, DOUBLE_CHARGE_AMOUNT, 365),
        Plan("Enterprise", Tier.vip, 2500.00, 30),
    ]
}

FIRST_NAMES = [
    "Aisha", "Ben", "Carmen", "Daniel", "Elif", "Felix", "Grace", "Hiro", "Ines",
    "Jonah", "Kavya", "Liam", "Maya", "Nikolai", "Olivia", "Pedro", "Quinn", "Rosa",
    "Samir", "Tessa", "Umar", "Valeria", "Wei", "Ximena", "Yusuf", "Zoe", "Arjun",
    "Brooke", "Chidi", "Dana",
]
LAST_NAMES = [
    "Okafor", "Lindqvist", "Alvarez", "Nakamura", "Kowalski", "Patel", "Brennan",
    "Haddad", "Moreau", "Svensson", "Adeyemi", "Castillo", "Fischer", "Gupta",
    "Hughes", "Ivanova", "Jensen", "Kim", "Larsen", "Mensah", "Novak", "O'Connell",
    "Petrov", "Rossi", "Schultz", "Tanaka", "Vargas", "Walsh", "Yilmaz", "Zhang",
]
COMPANY_DOMAINS = [
    "lumenworks.io", "cobaltgrid.com", "fernhilldata.co", "parcelpoint.io",
    "tidewaterlabs.com", "quillstack.dev", "orchardops.com", "brasskey.io",
    "northgatehealth.org", "sparrowfinance.com", "kilnstudio.co", "meridianfleet.com",
]
PERSONAL_DOMAINS = ["gmail.com", "outlook.com", "proton.me", "icloud.com"]

PAST_TICKET_TEMPLATES = [
    ("Customer could not log in after changing their email address.",
     "Re-verified the new email and cleared the stale session. Login confirmed working."),
    ("Password reset email was not arriving.",
     "Email was being filtered as spam by the company mail server. Customer whitelisted "
     "no-reply@brightwave.app and the reset succeeded."),
    ("Asked why the invoice amount differed from the plan price.",
     "Explained sales tax is added based on billing address. Sent a copy of the itemised invoice."),
    ("Dashboard charts were loading very slowly.",
     "Traced to a 90-day date range on a large dataset. Recommended enabling pre-aggregation; "
     "load time dropped from 40s to 3s."),
    ("Received 401 Unauthorized errors from the API.",
     "API key had been rotated by a teammate. Customer generated a new key and updated their integration."),
    ("Requested a refund for an accidental upgrade.",
     "Upgrade was made within 14 days. Refunded in full and moved back to the previous plan."),
    ("Card payment was declined at renewal.",
     "Card had expired. Customer updated payment method and the retry succeeded."),
    ("Wanted to add a teammate with read-only access.",
     "Walked the customer through Settings > Members and assigning the Viewer role."),
    ("CSV export was missing the last day of data.",
     "Export uses UTC; customer's data was in PST. Showed how to set the export timezone."),
    ("Asked how to downgrade from Pro to Free.",
     "Explained the downgrade takes effect at the end of the current billing period. Customer chose to stay on Pro."),
    ("Hit API rate limit (429) during a backfill job.",
     "Advised exponential backoff and batching requests. Backfill completed overnight."),
    ("Two-factor authentication codes were rejected.",
     "Phone clock was out of sync. Enabling automatic time fixed TOTP codes."),
]

HELP_DOCS = [
    ("Resetting your password", """\
If you have forgotten your password:

1. Go to https://app.brightwave.app/login and click "Forgot password?".
2. Enter the email address on your account. We send a reset link that is valid for 60 minutes.
3. Click the link and choose a new password (at least 12 characters).

Didn't get the email?
- Check your spam or quarantine folder and allow no-reply@brightwave.app.
- Make sure you are using the email address your account was created with.
- Reset links can only be requested 5 times per hour.

If your organisation uses SAML single sign-on, passwords are managed by your identity provider; contact your IT admin instead.

Support agents can trigger a reset from the admin console but can never see or set a customer's password."""),
    ("How billing cycles work", """\
Brightwave bills in advance for each billing period.

- Pro Monthly ($49/month) renews every 30 days from your signup date.
- Pro Annual ($450/year) renews every 365 days from your signup date.
- Enterprise plans are invoiced monthly per your order form.

You will receive an email receipt for every successful charge, and a reminder 7 days before an annual renewal.
Invoices are available under Settings > Billing > Invoices and can be downloaded as PDF.

If a payment fails we retry automatically after 3 days and again after 7 days. After the final failed retry the workspace is moved to the Free plan; no data is deleted."""),
    ("Refund policy", """\
- Monthly plans: charges are non-refundable, but you can cancel any time and keep access until the end of the period.
- Annual plans: you may request a full refund within 14 days of the initial charge or a renewal charge.
- Accidental upgrades and add-on purchases: refunded in full if reported within 14 days.
- Duplicate charges: always refunded in full, regardless of when they are reported.

Refunds are returned to the original payment method and take 5-10 business days to appear on your statement.
Refunds of $1,000 or more are reviewed by the billing team before they are issued.
Enterprise refunds are handled according to the customer's order form and must go through the account manager."""),
    ("Duplicate or unexpected charges", """\
If you see two identical charges from Brightwave:

1. Check whether one is a pending authorisation. Card authorisations usually drop off within 3 business days.
2. Check whether you have more than one workspace; each workspace is billed separately.
3. If both charges have settled for the same workspace and billing period, it is a duplicate charge.

Duplicate charges are always refunded in full (see "Refund policy"). Support agents should confirm both payments in the billing system, refund the newer charge, and reply with the refund amount and expected timeline (5-10 business days).

Unrecognised charges that do not match any workspace should be escalated to billing@brightwave.app."""),
    ("API error codes", """\
The Brightwave REST API returns standard HTTP status codes:

- 400 Bad Request: the request body is invalid. The response includes an `errors` array describing each field.
- 401 Unauthorized: the API key is missing, revoked or rotated. Generate a new key under Settings > API.
- 403 Forbidden: the key is valid but lacks permission. Keys inherit the role of the user who created them.
- 404 Not Found: the resource does not exist or belongs to another workspace.
- 409 Conflict: a resource with the same unique field already exists.
- 422 Unprocessable Entity: the request is well-formed but fails validation (for example, an end date before a start date).
- 429 Too Many Requests: you have exceeded your rate limit. See "API rate limits".
- 500 / 503: a problem on our side. Retry with exponential backoff and check https://status.brightwave.app.

Every response includes an `X-Request-Id` header. Include it when contacting support."""),
    ("API rate limits", """\
Rate limits are applied per workspace:

- Free: 60 requests/minute
- Pro: 600 requests/minute
- Enterprise: 2,000 requests/minute by default; can be raised on request

Every response includes `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset` headers.
When you exceed the limit the API responds with 429 and a `Retry-After` header (in seconds).

Best practices: batch writes using the /bulk endpoints (up to 500 records per call), cache read results, and use exponential backoff with jitter on 429 responses."""),
    ("Upgrading, downgrading or cancelling your plan", """\
Change your plan under Settings > Billing > Plan.

- Upgrades take effect immediately. You are charged a prorated amount for the rest of the current period.
- Downgrades take effect at the end of the current billing period. No partial refunds are issued for downgrades.
- Switching from monthly to annual starts a new annual period immediately, with unused monthly time credited.
- Cancelling keeps your paid features until the end of the period, then moves the workspace to Free.

Enterprise plan changes are handled by your account manager."""),
    ("Setting up two-factor authentication", """\
Two-factor authentication (2FA) adds a one-time code to your login.

1. Go to Profile > Security > Two-factor authentication.
2. Scan the QR code with an authenticator app (1Password, Authy, Google Authenticator).
3. Enter the 6-digit code to confirm, then save your 10 backup codes somewhere safe.

If codes are rejected, make sure your phone's clock is set automatically. TOTP codes depend on accurate time.
Lost your device? Use a backup code to log in. If you have no backup codes, support must verify your identity before 2FA can be removed."""),
    ("Configuring SAML single sign-on (Enterprise)", """\
SAML SSO is available on Enterprise plans and supports Okta, Azure AD / Entra ID, Google Workspace and OneLogin.

1. In Brightwave, open Settings > Security > SSO and copy the ACS URL and Entity ID.
2. Create a SAML app in your identity provider using those values.
3. Upload your IdP metadata XML to Brightwave.
4. Test with a single user, then enable "Require SSO" for the workspace.

Users are matched on email address. Just-in-time provisioning creates accounts on first login with the Viewer role.
Workspace owners can always log in with a password as a break-glass fallback."""),
    ("Exporting your data and deleting your account", """\
Export: go to Settings > Data > Export. Choose CSV or Parquet and a date range. Large exports are prepared in the background and emailed as a download link valid for 7 days.

Delete: workspace owners can delete a workspace under Settings > Danger zone. Deletion is permanent after a 30-day grace period, during which the workspace can be restored by contacting support.

Under GDPR and CCPA, customers can request a copy or deletion of their personal data at privacy@brightwave.app. We respond within 30 days."""),
]


@dataclass
class SeedData:
    customers: list[Customer] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    tickets: list[Ticket] = field(default_factory=list)
    past_tickets: list[PastTicket] = field(default_factory=list)
    help_docs: list[HelpDoc] = field(default_factory=list)


def _at(day: dt.date, rng: random.Random) -> dt.datetime:
    """A plausible business-hours UTC timestamp on the given day."""
    hms = dt.time(rng.randint(8, 19), rng.randint(0, 59), rng.randint(0, 59), tzinfo=dt.timezone.utc)
    return dt.datetime.combine(day, hms)


def _period_label(plan: Plan, day: dt.date, index: int) -> str:
    if plan.name == "Pro Annual":
        return "Pro Annual subscription" if index == 0 else "Pro Annual renewal"
    return f"{plan.name} subscription ({day:%b %Y})"


def build(today: dt.date) -> SeedData:
    rng = random.Random(RNG_SEED)
    data = SeedData()

    # --- Customers -------------------------------------------------------
    # (name, email, plan, days since signup); ids 1 and 2 are the fixed demo scenarios.
    specs: list[tuple[str, str, str, int]] = [
        ("Eleanor Whitfield", "eleanor.whitfield@halcyonfreight.com", "Enterprise", 1040),
        ("Marcus Bell", "marcus.bell@cedarlinepress.com", "Pro Annual", 365 * 2 + 2),
        ("Priya Nair", "priya.nair@lumenworks.io", "Pro Monthly", 412),
        ("Jordan Lee", "jordan.lee@gmail.com", "Free", 95),
    ]
    other_plans = ["Enterprise"] * 2 + ["Pro Monthly"] * 9 + ["Pro Annual"] * 4 + ["Free"] * 11
    rng.shuffle(other_plans)
    firsts = rng.sample(FIRST_NAMES, len(other_plans))
    lasts = rng.sample(LAST_NAMES, len(other_plans))
    for first, last, plan_name in zip(firsts, lasts, other_plans):
        slug = f"{first}.{last}".lower().replace("'", "")
        domain = rng.choice(PERSONAL_DOMAINS if plan_name == "Free" else COMPANY_DOMAINS)
        days_ago = rng.randint(200, 720) if plan_name == "Enterprise" else rng.randint(20, 1100)
        specs.append((f"{first} {last}", f"{slug}@{domain}", plan_name, days_ago))

    payment_id = 1
    for customer_id, (name, email, plan_name, days_ago) in enumerate(specs, start=1):
        plan = PLANS[plan_name]
        signup = today - dt.timedelta(days=days_ago)
        payments: list[Payment] = []

        def add(amount: float, when: dt.datetime, status: PaymentStatus, description: str) -> Payment:
            nonlocal payment_id
            p = Payment(id=payment_id, customer_id=customer_id, amount=amount, date=when,
                        status=status, description=description)
            payment_id += 1
            payments.append(p)
            return p

        if plan.interval_days:
            is_demo = customer_id in (VIP_CUSTOMER_ID, DOUBLE_CHARGED_CUSTOMER_ID, ACCIDENTAL_ADDON_CUSTOMER_ID)
            for i in range(days_ago // plan.interval_days + 1):
                day = signup + dt.timedelta(days=i * plan.interval_days)
                label = _period_label(plan, day, i)
                roll = rng.random()
                if not is_demo and i > 0 and roll < 0.05:
                    # Failed charge followed by an automatic retry 3 days later.
                    add(plan.price, _at(day, rng), PaymentStatus.failed, f"{label} - card declined")
                    retry_day = day + dt.timedelta(days=3)
                    status = PaymentStatus.succeeded if retry_day <= today else PaymentStatus.pending
                    add(plan.price, _at(min(retry_day, today), rng), status, f"{label} - retry")
                elif not is_demo and roll > 0.985:
                    add(plan.price, _at(day, rng), PaymentStatus.refunded, f"{label} - refunded")
                else:
                    add(plan.price, _at(day, rng), PaymentStatus.succeeded, label)

        if customer_id == VIP_CUSTOMER_ID:
            add(12000.00, _at(signup + dt.timedelta(days=9), rng), PaymentStatus.succeeded,
                "Premium onboarding and SSO setup (one-time)")
            for year in range(days_ago // 365 + 1):
                add(6000.00, _at(signup + dt.timedelta(days=14 + 365 * year), rng),
                    PaymentStatus.succeeded, "EU data residency add-on (annual)")

        if customer_id == DOUBLE_CHARGED_CUSTOMER_ID:
            # Renewal was processed twice, four minutes apart.
            renewal = payments[-1]
            add(DOUBLE_CHARGE_AMOUNT, renewal.date + dt.timedelta(minutes=4),
                PaymentStatus.succeeded, renewal.description)

        if customer_id == ACCIDENTAL_ADDON_CUSTOMER_ID:
            add(ACCIDENTAL_ADDON_AMOUNT, _at(today - dt.timedelta(days=2), rng), PaymentStatus.succeeded,
                "Advanced Reports add-on (one-time)")

        data.payments.extend(payments)
        data.customers.append(Customer(
            id=customer_id,
            name=name,
            email=email,
            plan=plan.name,
            tier=plan.tier,
            signup_date=signup,
            lifetime_value=round(sum(p.amount for p in payments if p.status == PaymentStatus.succeeded), 2),
        ))

    # --- Past tickets ----------------------------------------------------
    fixed_history = {
        VIP_CUSTOMER_ID: [
            (900, "Requested help configuring SAML SSO with Okta.",
             "Solutions engineer joined a call; SSO enabled with JIT provisioning for 140 users."),
            (420, "Nightly sync job hitting 429 rate limit errors.",
             "Raised workspace rate limit from 2,000 to 5,000 requests/minute as a VIP accommodation."),
            (60, "Asked to consolidate monthly invoices into a single quarterly invoice.",
             "Billing team set up quarterly invoicing starting next quarter; account manager confirmed by email."),
        ],
        DOUBLE_CHARGED_CUSTOMER_ID: [
            (210, "Password reset email was not arriving.",
             "Email was in the spam folder. Customer reset their password successfully."),
        ],
    }
    past_id = 1
    for customer in data.customers:
        if customer.id in fixed_history:
            entries = [(today - dt.timedelta(days=d), s, r) for d, s, r in fixed_history[customer.id]]
        else:
            tenure = (today - customer.signup_date).days
            count = rng.choices([0, 1, 2, 3], weights=[3, 4, 2, 1])[0]
            entries = [
                (customer.signup_date + dt.timedelta(days=rng.randint(1, max(tenure - 1, 1))), s, r)
                for s, r in rng.sample(PAST_TICKET_TEMPLATES, count)
            ]
        for day, summary, resolution in sorted(entries):
            data.past_tickets.append(PastTicket(
                id=past_id, customer_id=customer.id, summary=summary, resolution=resolution, date=day,
            ))
            past_id += 1

    # --- Open tickets for the demo ---------------------------------------
    renewal_time = max(p.date for p in data.payments if p.customer_id == DOUBLE_CHARGED_CUSTOMER_ID)
    free_customer = next(c for c in data.customers if c.id == FREE_DEMO_CUSTOMER_ID)
    data.tickets = [
        Ticket(
            id=1,
            customer_id=DOUBLE_CHARGED_CUSTOMER_ID,
            subject="Charged twice for my annual renewal",
            body=(
                "Hi,\n\nI just checked my card statement and there are two charges of $450.00 from "
                f"Brightwave on {renewal_time:%B} {renewal_time.day}, {renewal_time:%Y}. I only have one "
                "workspace, so I think my renewal went through twice. Can you refund the duplicate?"
                "\n\nThanks,\nMarcus"
            ),
            created_at=renewal_time + dt.timedelta(hours=20),
            status=TicketStatus.open,
        ),
        Ticket(
            id=2,
            customer_id=VIP_CUSTOMER_ID,
            subject="URGENT: API returning 429 errors, nightly sync failing",
            body=(
                "Our nightly warehouse sync has failed two nights in a row with 429 Too Many Requests. "
                "We were told our limit was raised to 5,000/min last year but the X-RateLimit-Limit header "
                "now says 2000. Our exec dashboards are stale. Please escalate.\n\nEleanor Whitfield\n"
                "Head of Data, Halcyon Freight"
            ),
            created_at=_at(today, rng).replace(hour=7),
            status=TicketStatus.open,
        ),
        Ticket(
            id=3,
            customer_id=free_customer.id,
            subject="Password reset email never arrives",
            body=(
                "I've clicked 'Forgot password' three times in the last hour and haven't received any email. "
                f"My login is {free_customer.email}. How do I get back into my account?"
            ),
            created_at=_at(today - dt.timedelta(days=1), rng),
            status=TicketStatus.open,
        ),
    ]

    data.help_docs = [HelpDoc(id=i, title=t, content=c) for i, (t, c) in enumerate(HELP_DOCS, start=1)]
    return data


def seed(reset: bool = False) -> None:
    create_db_and_tables()
    with Session(engine) as session:
        if session.exec(select(DeliveryOperation).limit(1)).first() or session.exec(
            select(Payment).where(Payment.stripe_charge_id.is_not(None)).limit(1)
        ).first() or session.exec(select(Customer).where(Customer.demo_recipient.is_not(None)).limit(1)).first():
            raise RuntimeError("Refuse reseeding or reset: provider bindings or operation audit records exist.")
        remote = engine.dialect.name != "sqlite"
        if remote and reset and os.getenv("RELAYY_ALLOW_REMOTE_RESET") != "1":
            raise RuntimeError("Refuse resetting a remote database. Set RELAYY_ALLOW_REMOTE_RESET=1 to confirm.")
        if remote and not reset and session.exec(select(Customer.id).limit(1)).first() is not None:
            # Fixed seed ids would overwrite existing rows; only seed an empty remote database.
            raise RuntimeError("Refuse seeding a remote database that already contains customers.")
    if reset:
        SQLModel.metadata.drop_all(engine)
        create_db_and_tables()

    data = build(dt.date.today())
    with Session(engine) as session:
        # Parents before children so foreign keys resolve.
        for row in [*data.customers, *data.help_docs, *data.payments, *data.past_tickets, *data.tickets]:
            session.merge(row)
        session.commit()
        if engine.dialect.name == "postgresql":
            # Explicit seed ids do not advance Postgres sequences; move them past the seeded rows.
            for model in (Customer, Payment, Ticket, PastTicket, HelpDoc):
                table = model.__tablename__
                session.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM \"{table}\"), 0) + 1, false)"
                ))
            session.commit()

        for model in (Customer, Payment, Ticket, PastTicket, HelpDoc):
            count = session.exec(select(func.count()).select_from(model)).one()
            print(f"{model.__name__:<11} {count:>4} rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="drop and recreate all tables before seeding")
    seed(reset=parser.parse_args().reset)
