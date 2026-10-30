"""Read-only, SQLite-backed tools for the research agent.

Every tool is scoped to the customer who sent the ticket (when known): asking for any
other customer's data returns an error. This is enforced here in Python, so a ticket
that mentions someone else's email cannot pull their records into the reply.
"""

import datetime as dt
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session, select

from db import engine
from models import Customer, HelpDoc, PastTicket, Payment, PaymentStatus
from swarm import llm
from swarm.state import CustomerProfile, DocSnippet, PastTicketRecord, PaymentRecord

MAX_PAYMENT_DAYS = 3 * 365
DOC_RESULTS = 3


class ToolError(Exception):
    """Raised for bad tool input; reported to the model as an is_error tool result."""


@dataclass
class ToolContext:
    """Per-ticket scope for the tools, plus everything they returned (the research record)."""

    allowed_customer_id: int | None
    customer: CustomerProfile | None = None
    payments: dict[int, PaymentRecord] = field(default_factory=dict)
    past_tickets: list[PastTicketRecord] = field(default_factory=list)
    docs: dict[str, DocSnippet] = field(default_factory=dict)

    def check_customer(self, customer_id: int) -> None:
        if self.allowed_customer_id is None:
            raise ToolError("Access denied: customer scope is required before records can be looked up.")
        if customer_id != self.allowed_customer_id:
            raise ToolError(
                f"Access denied: only the customer who sent this ticket (id {self.allowed_customer_id}) can be looked up."
            )


# --- Tool implementations ------------------------------------------------


def lookup_customer(ctx: ToolContext, email_or_id: str) -> dict[str, Any]:
    # Do not even reveal whether an arbitrary customer exists without ticket scope.
    if ctx.allowed_customer_id is None:
        raise ToolError("Access denied: customer scope is required before records can be looked up.")
    key = email_or_id.strip()
    with Session(engine) as session:
        if key.isdigit():
            customer = session.get(Customer, int(key))
        else:
            customer = session.exec(select(Customer).where(Customer.email == key.lower())).first()
    if customer is None:
        raise ToolError(f"No customer found for {key!r}.")
    ctx.check_customer(customer.id)

    profile = CustomerProfile.model_validate(customer.model_dump())
    ctx.customer = profile
    return {**profile.model_dump(mode="json"), "tenure_days": (dt.date.today() - profile.signup_date).days}


def get_payments(ctx: ToolContext, customer_id: int, days: int) -> dict[str, Any]:
    ctx.check_customer(customer_id)
    days = max(1, min(days, MAX_PAYMENT_DAYS))
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    with Session(engine) as session:
        rows = session.exec(
            select(Payment)
            .where(Payment.customer_id == customer_id, Payment.date >= since)
            .order_by(Payment.date.desc())
        ).all()

    records = [PaymentRecord.model_validate(p.model_dump()) for p in rows]
    ctx.payments.update({r.id: r for r in records})
    return {
        "customer_id": customer_id,
        "days": days,
        "payments": [r.model_dump(mode="json") for r in records],
        "possible_duplicate_charges": find_duplicates(records),
    }


def get_past_tickets(ctx: ToolContext, customer_id: int) -> dict[str, Any]:
    ctx.check_customer(customer_id)
    with Session(engine) as session:
        rows = session.exec(
            select(PastTicket).where(PastTicket.customer_id == customer_id).order_by(PastTicket.date.desc())
        ).all()

    records = [PastTicketRecord.model_validate(t.model_dump()) for t in rows]
    ctx.past_tickets = records
    return {"customer_id": customer_id, "past_tickets": [r.model_dump(mode="json") for r in records]}


def search_help_docs(ctx: ToolContext, query: str) -> dict[str, Any]:
    with Session(engine) as session:
        docs = session.exec(select(HelpDoc)).all()

    hits = _bm25_rank(query, [(d.title, d.content) for d in docs])[:DOC_RESULTS]
    results = []
    for score, (title, content) in hits:
        ctx.docs[title] = DocSnippet(title=title, content=content)
        results.append({"title": title, "score": round(score, 2), "content": content})
    return {"query": query, "results": results}


def find_duplicates(payments: list[PaymentRecord]) -> list[dict[str, Any]]:
    """Successful charges with the same amount and description within 24 hours of each other."""
    ok = sorted((p for p in payments if p.status == PaymentStatus.succeeded), key=lambda p: p.date)
    dupes = []
    for a, b in zip(ok, ok[1:]):
        if a.amount == b.amount and a.description == b.description and b.date - a.date <= dt.timedelta(hours=24):
            dupes.append({"payment_ids": [a.id, b.id], "amount": a.amount, "date": a.date.date().isoformat()})
    return dupes


# --- BM25 ----------------------------------------------------------------

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _bm25_rank(query: str, docs: list[tuple[str, str]], k1: float = 1.5, b: float = 0.75):
    # Titles are repeated so a title match outweighs a passing mention in the body.
    corpus = [_tokens(f"{title} {title} {content}") for title, content in docs]
    if not corpus:
        return []
    avg_len = sum(map(len, corpus)) / len(corpus)
    doc_freq = Counter(term for doc in corpus for term in set(doc))
    n = len(corpus)

    scored = []
    for doc, original in zip(corpus, docs):
        tf = Counter(doc)
        score = 0.0
        for term in set(_tokens(query)):
            if term not in tf:
                continue
            idf = math.log(1 + (n - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
            score += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(doc) / avg_len))
        if score > 0:
            scored.append((score, original))
    return sorted(scored, key=lambda s: s[0], reverse=True)


# --- Tool definitions and dispatch ----------------------------------------


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


TOOLS = [
    {
        "name": "lookup_customer",
        "description": "Look up a customer's profile (name, email, plan, tier, signup date, lifetime value) "
        "by email address or numeric customer id.",
        "strict": True,
        "input_schema": _schema({"email_or_id": {"type": "string", "description": "Email address or customer id."}}),
    },
    {
        "name": "get_payments",
        "description": "List a customer's payments from the last N days, newest first, with status "
        "(succeeded, failed, refunded, pending). Also flags possible duplicate charges.",
        "strict": True,
        "input_schema": _schema({
            "customer_id": {"type": "integer"},
            "days": {"type": "integer", "description": f"How many days back to look (1-{MAX_PAYMENT_DAYS})."},
        }),
    },
    {
        "name": "get_past_tickets",
        "description": "List a customer's previously resolved support tickets with their resolutions.",
        "strict": True,
        "input_schema": _schema({"customer_id": {"type": "integer"}}),
    },
    {
        "name": "search_help_docs",
        "description": "Keyword search over Brightwave's help center and policy docs (refunds, billing, "
        "API errors, rate limits, passwords, SSO, ...). Returns the best-matching articles in full.",
        "strict": True,
        "input_schema": _schema({"query": {"type": "string", "description": "A few keywords."}}),
    },
]

_HANDLERS = {
    "lookup_customer": lookup_customer,
    "get_payments": get_payments,
    "get_past_tickets": get_past_tickets,
    "search_help_docs": search_help_docs,
}


def execute(ctx: ToolContext, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
    """Run a tool call and return (JSON result, is_error)."""
    # Accept a name that differs only in letter case.
    handler = _HANDLERS.get(name) or {k.lower(): v for k, v in _HANDLERS.items()}.get(name.lower())
    if handler is None:
        return f"Unknown tool {name!r}. Available tools: {', '.join(_HANDLERS)}.", True
    try:
        return json.dumps(llm.sanitize_model_value(handler(ctx, **tool_input))), False
    except ToolError as exc:
        return str(exc), True
    except TypeError as exc:
        return f"Invalid arguments for {name}: {exc}", True


def describe_call(name: str, tool_input: dict[str, Any]) -> str:
    """Short, human-friendly label for streaming a tool call to the UI."""
    match name:
        case "lookup_customer":
            return f"🔎 Looking up customer {tool_input.get('email_or_id', '')}..."
        case "get_payments":
            return f"💳 Checking payments (last {tool_input.get('days', '?')} days)..."
        case "get_past_tickets":
            return "📂 Reviewing past tickets..."
        case "search_help_docs":
            return f"📚 Searching help docs for \"{tool_input.get('query', '')}\"..."
    return f"🔧 Calling {name}..."
