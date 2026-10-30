"""Safety-first Stripe test-mode adapter with no implicit POST retries."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

import config
from fulfillment.contracts import ChargeInfo, ProviderError, ProviderResult, RefundRequest

API_URL = "https://api.stripe.com"
MAX_RESPONSE_BYTES = 64 * 1024
MAX_REFUND_PAGES = 10
MAX_REFUNDS = 1_000
DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class _Response:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


def _safe_request_id(headers: Mapping[str, str]) -> str | None:
    for name in ("request-id", "x-request-id"):
        value = headers.get(name)
        if value and len(value) <= 255 and value.isprintable():
            return value
    return None


def _safe_json(response: _Response) -> dict[str, Any] | None:
    if not response.body:
        return {}
    try:
        parsed = json.loads(response.body)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _error_result(response: _Response) -> ProviderResult:
    request_id = _safe_request_id(response.headers)
    if response.status_code == 429:
        return ProviderResult("pending", "rate_limited_retry_safe", request_id=request_id, error_code="rate_limited", error_message="Provider rate limited the request")
    if response.status_code >= 500:
        return ProviderResult("unknown", f"http_{response.status_code}", request_id=request_id, error_code="provider_unavailable", error_message="Provider response was unavailable")
    return ProviderResult("failed", f"http_{response.status_code}", request_id=request_id, error_code="provider_rejected", error_message=f"Provider rejected the request (HTTP {response.status_code})")


def _created_refund_matches(data: dict[str, Any], request: RefundRequest) -> bool:
    """Require the create response to prove it represents the frozen intent.

    Stripe Refund objects do not have a documented ``livemode`` property. Test
    mode is established by the configured non-live credential/account and the
    inspected source charge. If a response does include a true value, fail
    closed rather than accepting an unexpected live-mode object.
    """
    metadata = data.get("metadata")
    return (
        isinstance(data.get("id"), str)
        and data.get("charge") == request.charge_id
        and data.get("amount") == request.amount_minor
        and data.get("currency") == request.currency
        and data.get("livemode") is not True
        and isinstance(metadata, dict)
        and metadata.get("relayy_operation_id") == request.operation_id
    )


def _status_result(data: dict[str, Any], headers: Mapping[str, str]) -> ProviderResult:
    provider_id = data.get("id")
    if not isinstance(provider_id, str) or not provider_id:
        return ProviderResult("unknown", "invalid_response", request_id=_safe_request_id(headers), error_code="invalid_response", error_message="Provider response omitted the refund identifier")
    # Refunds do not document this property, but an explicitly live object is
    # always rejected if a future API response supplies it.
    if data.get("livemode") is True:
        return ProviderResult("blocked", "live_mode_refund", provider_id=provider_id, request_id=_safe_request_id(headers), error_code="live_mode_refund", error_message="Live-mode Stripe objects are not permitted")
    status = data.get("status")
    if not isinstance(status, str):
        return ProviderResult("unknown", "invalid_response", provider_id=provider_id, request_id=_safe_request_id(headers), error_code="invalid_response", error_message="Provider response omitted the refund status")
    if status == "succeeded":
        return ProviderResult("succeeded", status, provider_id=provider_id, request_id=_safe_request_id(headers))
    if status in {"pending", "requires_action"}:
        return ProviderResult("pending", status, provider_id=provider_id, request_id=_safe_request_id(headers))
    if status in {"failed", "canceled"}:
        return ProviderResult("failed", status, provider_id=provider_id, request_id=_safe_request_id(headers), error_code="refund_failed", error_message="Stripe reported that the refund did not complete")
    return ProviderResult("unknown", status, provider_id=provider_id, request_id=_safe_request_id(headers), error_code="unrecognized_refund_status", error_message="Stripe returned an unrecognized refund status")


class StripeAdapter:
    """Stripe REST implementation constrained to the configured test account."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        account_id: str | None = None,
        api_version: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: httpx.Timeout | float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = config.STRIPE_SECRET_KEY if api_key is None else api_key
        self._account_id = config.STRIPE_ACCOUNT_ID if account_id is None else account_id
        self._api_version = config.STRIPE_API_VERSION if api_version is None else api_version
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._timeout = httpx.Timeout(timeout)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _preflight(self) -> ProviderResult | None:
        if not self._api_key:
            return ProviderResult("blocked", "missing_credentials", error_code="missing_stripe_secret_key", error_message="Stripe credentials are not configured")
        # Restricted live keys use rk_live_, so checking only sk_live_ is unsafe.
        if "_live_" in self._api_key:
            return ProviderResult("blocked", "live_credentials", error_code="live_stripe_credentials", error_message="Live Stripe credentials are not permitted")
        if not self._account_id:
            return ProviderResult("blocked", "missing_account", error_code="missing_stripe_account_id", error_message="The expected Stripe account is not configured")
        if not self._api_version:
            return ProviderResult("blocked", "missing_api_version", error_code="missing_stripe_api_version", error_message="The Stripe API version is not configured")
        return None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "Stripe-Version": self._api_version,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _Response | ProviderResult:
        request_headers = self._headers()
        if headers:
            request_headers.update(headers)
        try:
            async with self._client.stream(
                method,
                f"{API_URL}{path}",
                headers=request_headers,
                data=data,
                params=params,
                timeout=self._timeout,
            ) as response:
                declared_size = response.headers.get("content-length")
                if declared_size and declared_size.isdigit() and int(declared_size) > MAX_RESPONSE_BYTES:
                    return ProviderResult("unknown", "response_too_large", request_id=_safe_request_id(response.headers), error_code="response_too_large", error_message="Provider response exceeded the safety limit")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        return ProviderResult("unknown", "response_too_large", request_id=_safe_request_id(response.headers), error_code="response_too_large", error_message="Provider response exceeded the safety limit")
                    chunks.append(chunk)
                return _Response(response.status_code, response.headers, b"".join(chunks))
        except httpx.TimeoutException:
            return ProviderResult("unknown", "timeout", error_code="provider_timeout", error_message="Provider request timed out")
        except httpx.HTTPError:
            return ProviderResult("unknown", "transport_error", error_code="provider_unreachable", error_message="Provider could not be reached")

    async def check_account(self) -> ProviderResult:
        preflight = self._preflight()
        if preflight:
            return preflight
        response = await self._request("GET", "/v1/account")
        if isinstance(response, ProviderResult):
            return response
        if not 200 <= response.status_code < 300:
            return _error_result(response)
        data = _safe_json(response)
        account_id = data.get("id") if data else None
        if account_id != self._account_id:
            return ProviderResult("blocked", "account_mismatch", request_id=_safe_request_id(response.headers), error_code="stripe_account_mismatch", error_message="Retrieved Stripe account does not match the configured account")
        return ProviderResult("succeeded", "account_verified", provider_id=account_id, request_id=_safe_request_id(response.headers))

    async def inspect_charge(self, charge_id: str) -> ChargeInfo:
        if not isinstance(charge_id, str) or not charge_id.strip():
            raise ProviderError(ProviderResult("blocked", "invalid_charge_id", error_code="invalid_charge_id", error_message="A Stripe charge identifier is required"))
        account = await self.check_account()
        if account.state != "succeeded":
            raise ProviderError(account)
        response = await self._request("GET", f"/v1/charges/{quote(charge_id, safe='')}")
        if isinstance(response, ProviderResult):
            raise ProviderError(response)
        if not 200 <= response.status_code < 300:
            raise ProviderError(_error_result(response))
        data = _safe_json(response)
        if not data:
            raise ProviderError(ProviderResult("unknown", "invalid_response", request_id=_safe_request_id(response.headers), error_code="invalid_response", error_message="Stripe returned an invalid charge response"))
        try:
            returned_id = data["id"]
            customer_id = data["customer"]
            amount_minor = data.get("amount_captured", data["amount"])
            currency = data["currency"]
            livemode = data["livemode"]
            paid = data["paid"]
            captured = data["captured"]
            amount_refunded = data.get("amount_refunded", 0)
            created_at = data.get("created")
            if returned_id != charge_id or not isinstance(customer_id, str) or (created_at is not None and (not isinstance(created_at, int) or isinstance(created_at, bool))):
                raise ValueError
            info = ChargeInfo(returned_id, customer_id, self._account_id, amount_minor, currency, livemode, paid, captured, amount_refunded, created_at)
        except (KeyError, TypeError, ValueError):
            raise ProviderError(ProviderResult("unknown", "invalid_response", request_id=_safe_request_id(response.headers), error_code="invalid_response", error_message="Stripe returned an incomplete charge response")) from None
        if info.livemode:
            raise ProviderError(ProviderResult("blocked", "live_mode_charge", provider_id=info.id, request_id=_safe_request_id(response.headers), error_code="live_mode_charge", error_message="Live-mode Stripe charges are not permitted"))
        return info

    async def refund(self, request: RefundRequest, key: str) -> ProviderResult:
        if not isinstance(key, str) or not key.strip():
            return ProviderResult("blocked", "invalid_idempotency_key", error_code="invalid_idempotency_key", error_message="A stable idempotency key is required")
        preflight = self._preflight()
        if preflight:
            return preflight
        if request.account_id != self._account_id:
            return ProviderResult("blocked", "account_mismatch", error_code="stripe_account_mismatch", error_message="Refund request account does not match the configured account")
        charge = await self.inspect_charge(request.charge_id)
        if charge.customer_id != request.customer_id:
            return ProviderResult("blocked", "customer_mismatch", error_code="stripe_customer_mismatch", error_message="Charge customer does not match the frozen refund request")
        if not charge.paid or not charge.captured:
            return ProviderResult("blocked", "charge_not_captured", error_code="charge_not_captured", error_message="Charge is not paid and captured")
        if charge.currency != request.currency or request.amount_minor != charge.amount_minor:
            return ProviderResult("blocked", "amount_or_currency_mismatch", error_code="charge_amount_or_currency_mismatch", error_message="Refund must match the captured charge amount and currency")
        if charge.amount_refunded:
            return ProviderResult("blocked", "charge_already_refunded", error_code="charge_already_refunded", error_message="Charge has already been refunded")
        response = await self._request(
            "POST",
            "/v1/refunds",
            data={"charge": request.charge_id, "amount": str(request.amount_minor), "metadata[relayy_operation_id]": request.operation_id},
            headers={"Idempotency-Key": key},
        )
        if isinstance(response, ProviderResult):
            return response
        if not 200 <= response.status_code < 300:
            return _error_result(response)
        data = _safe_json(response)
        if not data:
            return ProviderResult("unknown", "invalid_response", request_id=_safe_request_id(response.headers), error_code="invalid_response", error_message="Stripe returned an invalid refund response")
        if not _created_refund_matches(data, request):
            return ProviderResult("unknown", "response_mismatch", request_id=_safe_request_id(response.headers), error_code="refund_response_mismatch", error_message="Stripe response did not verify the frozen refund request")
        return _status_result(data, response.headers)

    async def retrieve(self, provider_id: str) -> ProviderResult:
        if not isinstance(provider_id, str) or not provider_id.strip():
            return ProviderResult("blocked", "invalid_refund_id", error_code="invalid_refund_id", error_message="A Stripe refund identifier is required")
        account = await self.check_account()
        if account.state != "succeeded":
            return account
        response = await self._request("GET", f"/v1/refunds/{quote(provider_id, safe='')}")
        if isinstance(response, ProviderResult):
            return response
        if not 200 <= response.status_code < 300:
            return _error_result(response)
        data = _safe_json(response)
        if not data:
            return ProviderResult("unknown", "invalid_response", request_id=_safe_request_id(response.headers), error_code="invalid_response", error_message="Stripe returned an invalid refund response")
        if data.get("id") != provider_id:
            return ProviderResult("unknown", "response_mismatch", request_id=_safe_request_id(response.headers), error_code="refund_response_mismatch", error_message="Stripe response did not verify the requested refund")
        charge_id = data.get("charge")
        if not isinstance(charge_id, str) or not charge_id:
            return ProviderResult("unknown", "invalid_response", request_id=_safe_request_id(response.headers), error_code="invalid_response", error_message="Stripe refund response omitted the source charge")
        try:
            # This verifies the retrieved refund remains attached to a test
            # charge in the configured account before exposing success.
            await self.inspect_charge(charge_id)
        except ProviderError as error:
            return error.result
        return _status_result(data, response.headers)

    async def list_refunds(self, charge_id: str) -> list[dict[str, Any]]:
        if not isinstance(charge_id, str) or not charge_id.strip():
            raise ProviderError(ProviderResult("blocked", "invalid_charge_id", error_code="invalid_charge_id", error_message="A Stripe charge identifier is required"))
        account = await self.check_account()
        if account.state != "succeeded":
            raise ProviderError(account)
        refunds: list[dict[str, Any]] = []
        starting_after: str | None = None
        for _ in range(MAX_REFUND_PAGES):
            params = {"charge": charge_id, "limit": "100"}
            if starting_after:
                params["starting_after"] = starting_after
            response = await self._request("GET", "/v1/refunds", params=params)
            if isinstance(response, ProviderResult):
                raise ProviderError(response)
            if not 200 <= response.status_code < 300:
                raise ProviderError(_error_result(response))
            payload = _safe_json(response)
            data = payload.get("data") if payload else None
            has_more = payload.get("has_more") if payload else None
            if not isinstance(data, list):
                raise ProviderError(ProviderResult("unknown", "invalid_response", request_id=_safe_request_id(response.headers), error_code="invalid_response", error_message="Stripe returned an invalid refunds response"))
            if not isinstance(has_more, bool):
                raise ProviderError(ProviderResult("unknown", "invalid_response", request_id=_safe_request_id(response.headers), error_code="invalid_response", error_message="Stripe refunds pagination state was invalid"))
            for item in data:
                if not isinstance(item, dict):
                    raise ProviderError(ProviderResult("unknown", "invalid_response", request_id=_safe_request_id(response.headers), error_code="invalid_response", error_message="Stripe returned an invalid refund record"))
                refund_id = item.get("id")
                amount = item.get("amount")
                currency = item.get("currency")
                status = item.get("status")
                item_charge = item.get("charge")
                if not all((
                    isinstance(refund_id, str) and bool(refund_id),
                    isinstance(amount, int) and not isinstance(amount, bool) and amount > 0,
                    isinstance(currency, str) and len(currency) == 3 and currency.isalpha(),
                    isinstance(status, str) and bool(status),
                    isinstance(item_charge, str) and bool(item_charge),
                )):
                    raise ProviderError(ProviderResult("unknown", "invalid_response", request_id=_safe_request_id(response.headers), error_code="invalid_response", error_message="Stripe returned an incomplete refund record"))
                raw_metadata = item.get("metadata")
                if raw_metadata is not None and not isinstance(raw_metadata, dict):
                    raise ProviderError(ProviderResult("unknown", "invalid_response", request_id=_safe_request_id(response.headers), error_code="invalid_response", error_message="Stripe returned invalid refund metadata"))
                item_livemode = item.get("livemode")
                if item_livemode is True:
                    raise ProviderError(ProviderResult("blocked", "live_mode_refund", provider_id=refund_id, request_id=_safe_request_id(response.headers), error_code="live_mode_refund", error_message="Live-mode Stripe objects are not permitted"))
                if item_livemode is not None and not isinstance(item_livemode, bool):
                    raise ProviderError(ProviderResult("unknown", "invalid_response", provider_id=refund_id, request_id=_safe_request_id(response.headers), error_code="invalid_response", error_message="Stripe returned an invalid refund mode"))
                metadata = {str(name)[:128]: str(value)[:512] for name, value in (raw_metadata or {}).items()}
                refunds.append({
                    "id": refund_id,
                    "charge_id": item_charge,
                    "customer_id": item.get("customer") if isinstance(item.get("customer"), str) else None,
                    "amount": amount,
                    "currency": currency.lower(),
                    "status": status,
                    "metadata": metadata,
                    "livemode": item_livemode,
                })
                if len(refunds) >= MAX_REFUNDS:
                    raise ProviderError(ProviderResult("unknown", "pagination_limit", error_code="pagination_limit", error_message="Stripe refunds exceeded the safety limit"))
            if not has_more:
                return refunds
            last_id = data[-1].get("id") if data and isinstance(data[-1], dict) else None
            if not isinstance(last_id, str) or not last_id:
                raise ProviderError(ProviderResult("unknown", "invalid_response", request_id=_safe_request_id(response.headers), error_code="invalid_response", error_message="Stripe pagination could not continue safely"))
            starting_after = last_id
        raise ProviderError(ProviderResult("unknown", "pagination_limit", error_code="pagination_limit", error_message="Stripe refunds exceeded the page safety limit"))
