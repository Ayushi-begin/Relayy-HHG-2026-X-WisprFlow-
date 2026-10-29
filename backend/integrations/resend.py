"""Small, retry-free Resend adapter.

A Resend acceptance only proves that the provider accepted the submission. It
never claims inbox delivery.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

import config
from fulfillment.contracts import EmailRequest, ProviderResult

API_URL = "https://api.resend.com"
MAX_RESPONSE_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class _Response:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


def _safe_request_id(headers: Mapping[str, str]) -> str | None:
    for name in ("x-request-id", "request-id"):
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


class ResendAdapter:
    """Resend REST implementation using an injectable ``httpx.AsyncClient``."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: httpx.Timeout | float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = config.RESEND_API_KEY if api_key is None else api_key
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._timeout = httpx.Timeout(timeout)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str] | None:
        if not self._api_key:
            return None
        return {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}

    async def _request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> _Response | ProviderResult:
        request_headers = self._headers()
        if request_headers is None:
            return ProviderResult("blocked", "missing_credentials", error_code="missing_resend_api_key", error_message="Resend credentials are not configured")
        if headers:
            request_headers.update(headers)
        try:
            async with self._client.stream(
                method,
                f"{API_URL}{path}",
                headers=request_headers,
                json=json_body,
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

    async def send(self, request: EmailRequest, key: str) -> ProviderResult:
        if not isinstance(key, str) or not key.strip():
            return ProviderResult("blocked", "invalid_idempotency_key", error_code="invalid_idempotency_key", error_message="A stable idempotency key is required")
        response = await self._request(
            "POST",
            "/emails",
            json_body={"from": request.from_address, "to": [request.to], "subject": request.subject, "text": request.text},
            headers={"Idempotency-Key": key},
        )
        if isinstance(response, ProviderResult):
            return response
        if not 200 <= response.status_code < 300:
            return _error_result(response)
        body = _safe_json(response)
        provider_id = body.get("id") if body else None
        if not isinstance(provider_id, str) or not provider_id:
            return ProviderResult("unknown", "invalid_response", request_id=_safe_request_id(response.headers), error_code="invalid_response", error_message="Provider accepted a response without an email identifier")
        return ProviderResult("accepted", "accepted", provider_id=provider_id, request_id=_safe_request_id(response.headers))

    async def retrieve(self, provider_id: str) -> ProviderResult:
        if not isinstance(provider_id, str) or not provider_id.strip():
            return ProviderResult("blocked", "invalid_email_id", error_code="invalid_email_id", error_message="A provider email identifier is required")
        response = await self._request("GET", f"/emails/{quote(provider_id, safe='')}")
        if isinstance(response, ProviderResult):
            return response
        if not 200 <= response.status_code < 300:
            return _error_result(response)
        body = _safe_json(response)
        # Resend has returned both direct objects and a data envelope across API versions.
        data = body.get("data") if body and isinstance(body.get("data"), dict) else body
        returned_id = data.get("id") if isinstance(data, dict) else None
        if returned_id != provider_id:
            return ProviderResult("unknown", "invalid_response", request_id=_safe_request_id(response.headers), error_code="invalid_response", error_message="Provider response did not verify the requested email")
        status = data.get("last_event") if isinstance(data.get("last_event"), str) else "retrieved"
        return ProviderResult("accepted", status, provider_id=provider_id, request_id=_safe_request_id(response.headers))
