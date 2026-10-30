import datetime as dt
from enum import StrEnum

from sqlalchemy import UniqueConstraint
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
    stripe_customer_id: str | None = None
    provider_account_id: str | None = None
    demo_recipient: str | None = None
    binding_verified_at: dt.datetime | None = None


class Payment(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("provider_account_id", "stripe_charge_id"),)
    id: int | None = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id", index=True)
    amount: float
    date: dt.datetime
    status: PaymentStatus
    description: str
    provider_account_id: str | None = None
    stripe_charge_id: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    livemode: bool | None = None
    binding_verified_at: dt.datetime | None = None
    eligibility_evidence: dict | None = Field(default=None, sa_column=Column(JSON))


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
    current_run_id: str | None = None
    version: int = 0
    legacy_local_only: bool = False
    manual_followup_required: bool = False
    fulfillment_state: str = "not_requested"


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


class TicketRun(SQLModel, table=True):
    __tablename__ = "ticket_run"
    __table_args__ = (UniqueConstraint("ticket_id", "run_number"),)
    id: str = Field(primary_key=True)
    ticket_id: int = Field(foreign_key="ticket.id", index=True)
    run_number: int
    state: str = "processing"
    input_snapshot: dict = Field(sa_column=Column(JSON))
    agent_state: dict | None = Field(default=None, sa_column=Column(JSON))
    execution_mode: str = "simulated"
    started_at: dt.datetime
    ended_at: dt.datetime | None = None
    review_revision: int = 0
    review_outcome: str | None = None
    review_channel: str | None = None
    review_actor: str | None = None
    reviewed_at: dt.datetime | None = None
    authorized_plan: dict | None = Field(default=None, sa_column=Column(JSON))
    slack_target: dict | None = Field(default=None, sa_column=Column(JSON))


class DeliveryOperation(SQLModel, table=True):
    __tablename__ = "delivery_operation"
    id: str = Field(primary_key=True)
    ticket_id: int = Field(foreign_key="ticket.id", index=True)
    run_id: str = Field(foreign_key="ticket_run.id", index=True)
    kind: str
    provider: str
    account_id: str = ""
    logical_key: str = Field(unique=True)
    request: dict = Field(sa_column=Column(JSON))
    request_hash: str
    idempotency_key: str = Field(unique=True)
    dependency_id: str | None = Field(default=None, foreign_key="delivery_operation.id")
    state: str = "queued"
    provider_status: str | None = None
    provider_id: str | None = None
    first_attempt_at: dt.datetime | None = None
    retry_deadline: dt.datetime | None = None
    attempt_count: int = 0
    send_attempt_count: int = 0
    check_attempt_count: int = 0
    manual_check_requested: bool = False
    claim_token: str | None = None
    claim_expires_at: dt.datetime | None = None
    next_check_at: dt.datetime | None = None
    version: int = 0
    error_code: str | None = None
    error_message: str | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class OperationAttempt(SQLModel, table=True):
    __tablename__ = "operation_attempt"
    __table_args__ = (UniqueConstraint("operation_id", "attempt_number"),)
    id: int | None = Field(default=None, primary_key=True)
    operation_id: str = Field(foreign_key="delivery_operation.id", index=True)
    attempt_number: int
    claim_token: str
    kind: str
    started_at: dt.datetime
    ended_at: dt.datetime | None = None
    request_hash: str
    result: str | None = None
    provider_id: str | None = None
    provider_request_id: str | None = None
    error_code: str | None = None


class TicketEvent(SQLModel, table=True):
    __tablename__ = "ticket_event"
    id: int | None = Field(default=None, primary_key=True)
    ticket_id: int = Field(foreign_key="ticket.id", index=True)
    run_id: str | None = Field(default=None, index=True)
    kind: str
    timestamp: dt.datetime
    data: dict = Field(sa_column=Column(JSON))


class AgentSetting(SQLModel, table=True):
    """Operator control over one swarm agent. A disabled agent runs labeled simulated analysis."""

    __tablename__ = "agent_setting"
    agent: str = Field(primary_key=True)
    model_enabled: bool = True
    updated_at: dt.datetime
    updated_by: str


class AuditLog(SQLModel, table=True):
    """Append-only record of operator mutations. Details never hold credentials or raw ticket text."""

    __tablename__ = "audit_log"
    id: int | None = Field(default=None, primary_key=True)
    at: dt.datetime = Field(index=True)
    actor: str
    action: str = Field(index=True)
    target: str
    detail: dict = Field(default_factory=dict, sa_column=Column(JSON))
