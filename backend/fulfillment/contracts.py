"""Immutable provider boundary types used by fulfillment.

Adapters return normalized results for operations whose response can safely be
classified. Methods that must return provider data raise :class:`ProviderError`
with an equally normalized, safe result instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

ProviderState = Literal["blocked", "accepted", "succeeded", "pending", "failed", "unknown"]


def _required_text(value: str, field: str, *, limit: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    if len(value) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    return value


@dataclass(frozen=True, slots=True)
class EmailRequest:
    from_address: str
    to: str
    subject: str
    text: str

    def __post_init__(self) -> None:
        _required_text(self.from_address, "from_address", limit=320)
        _required_text(self.to, "to", limit=320)
        _required_text(self.subject, "subject", limit=998)
        _required_text(self.text, "text", limit=100_000)


@dataclass(frozen=True, slots=True)
class RefundRequest:
    charge_id: str
    amount_minor: int
    currency: str
    account_id: str
    customer_id: str
    operation_id: str

    def __post_init__(self) -> None:
        _required_text(self.charge_id, "charge_id", limit=255)
        _required_text(self.account_id, "account_id", limit=255)
        _required_text(self.customer_id, "customer_id", limit=255)
        _required_text(self.operation_id, "operation_id", limit=255)
        if not isinstance(self.amount_minor, int) or isinstance(self.amount_minor, bool) or self.amount_minor <= 0:
            raise ValueError("amount_minor must be a positive integer")
        if not isinstance(self.currency, str) or len(self.currency) != 3 or not self.currency.isalpha():
            raise ValueError("currency must be a three-letter currency code")
        object.__setattr__(self, "currency", self.currency.lower())


@dataclass(frozen=True, slots=True)
class ProviderResult:
    state: ProviderState
    provider_status: str
    provider_id: str | None = None
    request_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.state not in {"blocked", "accepted", "succeeded", "pending", "failed", "unknown"}:
            raise ValueError(f"unsupported provider state: {self.state}")
        _required_text(self.provider_status, "provider_status", limit=128)
        for field in ("provider_id", "request_id", "error_code", "error_message"):
            value = getattr(self, field)
            if value is not None:
                _required_text(value, field, limit=512 if field == "error_message" else 255)


@dataclass(frozen=True, slots=True)
class ChargeInfo:
    id: str
    customer_id: str
    account_id: str
    amount_minor: int
    currency: str
    livemode: bool
    paid: bool
    captured: bool
    amount_refunded: int
    created_at: int | None = None

    def __post_init__(self) -> None:
        _required_text(self.id, "id", limit=255)
        _required_text(self.customer_id, "customer_id", limit=255)
        _required_text(self.account_id, "account_id", limit=255)
        if not isinstance(self.amount_minor, int) or isinstance(self.amount_minor, bool) or self.amount_minor < 0:
            raise ValueError("amount_minor must be a non-negative integer")
        if not isinstance(self.amount_refunded, int) or isinstance(self.amount_refunded, bool) or self.amount_refunded < 0:
            raise ValueError("amount_refunded must be a non-negative integer")
        if not isinstance(self.currency, str) or len(self.currency) != 3 or not self.currency.isalpha():
            raise ValueError("currency must be a three-letter currency code")
        if self.created_at is not None and (not isinstance(self.created_at, int) or isinstance(self.created_at, bool) or self.created_at < 0):
            raise ValueError("created_at must be a non-negative Unix timestamp or None")
        if not all(isinstance(value, bool) for value in (self.livemode, self.paid, self.captured)):
            raise ValueError("livemode, paid, and captured must be booleans")
        object.__setattr__(self, "currency", self.currency.lower())


class ProviderError(Exception):
    """A safe, classified provider failure for data-returning calls."""

    def __init__(self, result: ProviderResult):
        self.result = result
        super().__init__(f"{result.state}: {result.error_code or result.provider_status}")


@runtime_checkable
class EmailProvider(Protocol):
    async def send(self, request: EmailRequest, key: str) -> ProviderResult: ...

    async def retrieve(self, provider_id: str) -> ProviderResult: ...


@runtime_checkable
class RefundProvider(Protocol):
    async def refund(self, request: RefundRequest, key: str) -> ProviderResult: ...

    async def retrieve(self, provider_id: str) -> ProviderResult: ...

    async def inspect_charge(self, charge_id: str) -> ChargeInfo: ...

    async def list_refunds(self, charge_id: str) -> list[dict[str, Any]]: ...

    async def check_account(self) -> ProviderResult: ...
