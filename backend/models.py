import datetime as dt
from enum import StrEnum

from sqlmodel import JSON, Column, Field, SQLModel


class Tier(StrEnum):
    free = "free"
    pro = "pro"
    vip = "vip"


class PaymentStatus(StrEnum):
    succeeded = "succeeded"
    failed = "failed"
    refunded = "refunded"
    pending = "pending"


class TicketStatus(StrEnum):
    open = "open"  # received, not processed yet
    processing = "processing"  # swarm is running
    escalated = "escalated"  # waiting for a human in Slack
    pending = "pending"
    resolved = "resolved"  # reply sent, by the AI or after human approval
    rejected = "rejected"  # a human rejected the AI's reply; needs manual handling
    closed = "closed"


class Customer(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    email: str = Field(unique=True, index=True)
    plan: str
    tier: Tier = Field(index=True)
    signup_date: dt.date
    lifetime_value: float = 0.0


class Payment(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id", index=True)
    amount: float
    date: dt.datetime
    status: PaymentStatus
    description: str


class Ticket(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id", index=True)
    subject: str
    body: str
    created_at: dt.datetime
    status: TicketStatus = TicketStatus.open
    updated_at: dt.datetime | None = None
    # Final swarm.state.TicketState as JSON, saved when processing finishes and on human actions.
    swarm_state: dict | None = Field(default=None, sa_column=Column(JSON))
    resolution: str | None = None  # auto_resolved | auto_refunded | approved | edited | rejected
    resolved_by: str | None = None  # "ai" or the Slack user who acted
    final_reply: str | None = None


class PastTicket(SQLModel, table=True):
    __tablename__ = "past_ticket"

    id: int | None = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id", index=True)
    summary: str
    resolution: str
    date: dt.date


class HelpDoc(SQLModel, table=True):
    __tablename__ = "help_doc"

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(unique=True)
    content: str
