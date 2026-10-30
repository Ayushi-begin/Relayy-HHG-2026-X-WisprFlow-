"""Deterministic provider adapter tests using httpx.MockTransport only."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import parse_qs

import httpx

# The application is normally launched with backend/ as the working directory.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fulfillment.contracts import ChargeInfo, EmailRequest, ProviderError, RefundRequest
from integrations.resend import ResendAdapter
from integrations.stripe import StripeAdapter


def run(coro):
    return asyncio.run(coro)


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_contracts_are_frozen_and_validate_money() -> None:
    request = RefundRequest("ch_1", 5000, "USD", "acct_1", "cus_1", "op_1")
    assert request.currency == "usd"
    try:
        request.amount_minor = 1  # type: ignore[misc]
    except (AttributeError, TypeError):
        pass
    else:
        raise AssertionError("requests must be immutable")

    for invalid in (0, -1, True):
        try:
            RefundRequest("ch_1", invalid, "usd", "acct_1", "cus_1", "op_1")
        except ValueError:
            pass
        else:
            raise AssertionError("invalid amount accepted")

    info = ChargeInfo("ch_1", "cus_1", "acct_1", 5000, "USD", False, True, True, 0)
    assert info.currency == "usd"


def test_resend_sends_one_post_with_stable_key_and_reports_acceptance() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "email_123"}, headers={"x-request-id": "resend-request"})

    client = client_for(handler)
    adapter = ResendAdapter(api_key="re_test", client=client)
    result = run(adapter.send(EmailRequest("Demo <demo@example.com>", "approved@example.com", "Subject", "Body"), "email:operation-1"))
    run(client.aclose())

    assert result.state == "accepted"
    assert result.provider_id == "email_123"
    assert result.request_id == "resend-request"
    assert len(requests) == 1
    assert requests[0].headers["idempotency-key"] == "email:operation-1"
    assert requests[0].url == httpx.URL("https://api.resend.com/emails")


def test_resend_retrieval_requires_matching_provider_evidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "different", "last_event": "sent"})

    client = client_for(handler)
    result = run(ResendAdapter(api_key="re_test", client=client).retrieve("email_expected"))
    run(client.aclose())

    assert result.state == "unknown"
    assert result.error_code == "invalid_response"


def test_resend_retrieval_reports_provider_failure_event() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "email_expected", "last_event": "email.bounced"})

    client = client_for(handler)
    result = run(ResendAdapter(api_key="re_test", client=client).retrieve("email_expected"))
    run(client.aclose())

    assert result.state == "failed"
    assert result.provider_status == "email.bounced"
    assert result.error_code == "email_failed"


def test_resend_timeout_is_unknown_without_a_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout", request=request)

    client = client_for(handler)
    result = run(ResendAdapter(api_key="re_test", client=client).send(EmailRequest("a@example.com", "b@example.com", "S", "B"), "key"))
    run(client.aclose())

    assert result.state == "unknown"
    assert result.error_code == "provider_timeout"
    assert calls == 1


def test_stripe_blocks_restricted_live_keys_before_any_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("adapter made a network request with a live key")

    client = client_for(handler)
    result = run(StripeAdapter(api_key="rk_live_not_allowed", account_id="acct_test", client=client).check_account())
    run(client.aclose())

    assert result.state == "blocked"
    assert result.error_code == "live_stripe_credentials"


def test_stripe_refund_verifies_account_and_test_charge_before_posting() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/account":
            return httpx.Response(200, json={"id": "acct_test"})
        if request.url.path == "/v1/charges/ch_test":
            return httpx.Response(200, json={
                "id": "ch_test", "customer": "cus_test", "amount": 5000, "amount_captured": 5000,
                "amount_refunded": 0, "currency": "usd", "livemode": False, "paid": True, "captured": True,
            })
        if request.url.path == "/v1/refunds":
            form = parse_qs(request.content.decode())
            assert form == {"charge": ["ch_test"], "amount": ["5000"], "metadata[relayy_operation_id]": ["op_1"]}
            assert request.headers["idempotency-key"] == "refund:operation-1"
            # Stripe's Refund schema has no livemode property.
            return httpx.Response(200, json={"id": "re_test", "charge": "ch_test", "amount": 5000, "currency": "usd", "status": "succeeded", "metadata": {"relayy_operation_id": "op_1"}}, headers={"request-id": "stripe-request"})
        raise AssertionError(f"Unexpected {request.method} {request.url}")

    client = client_for(handler)
    adapter = StripeAdapter(api_key="sk_test_key", account_id="acct_test", api_version="2025-02-24.acacia", client=client)
    result = run(adapter.refund(RefundRequest("ch_test", 5000, "usd", "acct_test", "cus_test", "op_1"), "refund:operation-1"))
    run(client.aclose())

    assert result.state == "succeeded"
    assert result.provider_id == "re_test"
    assert result.request_id == "stripe-request"
    assert [request.url.path for request in requests] == ["/v1/account", "/v1/charges/ch_test", "/v1/refunds"]


def test_stripe_inspection_rejects_live_charge_with_safe_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/account":
            return httpx.Response(200, json={"id": "acct_test"})
        return httpx.Response(200, json={
            "id": "ch_live", "customer": "cus_test", "amount": 100, "currency": "usd", "livemode": True,
            "paid": True, "captured": True, "amount_refunded": 0,
        })

    client = client_for(handler)
    try:
        run(StripeAdapter(api_key="sk_test_key", account_id="acct_test", client=client).inspect_charge("ch_live"))
    except ProviderError as error:
        assert error.result.state == "blocked"
        assert error.result.error_code == "live_mode_charge"
    else:
        raise AssertionError("live charge was not blocked")
    finally:
        run(client.aclose())


def test_stripe_create_response_must_match_the_frozen_refund() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/account":
            return httpx.Response(200, json={"id": "acct_test"})
        if request.url.path == "/v1/charges/ch_test":
            return httpx.Response(200, json={
                "id": "ch_test", "customer": "cus_test", "amount": 100, "currency": "usd", "livemode": False,
                "paid": True, "captured": True, "amount_refunded": 0,
            })
        return httpx.Response(200, json={
            "id": "re_other", "charge": "ch_other", "amount": 100, "currency": "usd",
            "status": "succeeded", "metadata": {"relayy_operation_id": "op_1"},
        })

    client = client_for(handler)
    result = run(StripeAdapter(api_key="sk_test_key", account_id="acct_test", client=client).refund(
        RefundRequest("ch_test", 100, "usd", "acct_test", "cus_test", "op_1"), "refund:operation-1"
    ))
    run(client.aclose())

    assert result.state == "unknown"
    assert result.error_code == "refund_response_mismatch"


def test_stripe_retrieval_checks_the_referenced_test_charge() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/account":
            return httpx.Response(200, json={"id": "acct_test"})
        if request.url.path == "/v1/refunds/re_test":
            return httpx.Response(200, json={"id": "re_test", "charge": "ch_test", "status": "succeeded"})
        if request.url.path == "/v1/charges/ch_test":
            return httpx.Response(200, json={
                "id": "ch_test", "customer": "cus_test", "amount": 100, "currency": "usd", "livemode": False,
                "paid": True, "captured": True, "amount_refunded": 0,
            })
        raise AssertionError(f"Unexpected {request.url}")

    client = client_for(handler)
    result = run(StripeAdapter(api_key="sk_test_key", account_id="acct_test", client=client).retrieve("re_test"))
    run(client.aclose())

    assert result.state == "succeeded"
    assert calls == ["/v1/account", "/v1/refunds/re_test", "/v1/account", "/v1/charges/ch_test"]


def test_stripe_lists_all_bounded_pages_with_metadata_for_reconciliation() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/v1/account":
            return httpx.Response(200, json={"id": "acct_test"})
        if request.url.params.get("starting_after") is None:
            return httpx.Response(200, json={"data": [{
                "id": "re_1", "charge": "ch_1", "amount": 5000, "currency": "usd", "status": "pending",
                "metadata": {"relayy_operation_id": "op_1"},
            }], "has_more": True})
        return httpx.Response(200, json={"data": [{
            "id": "re_2", "charge": "ch_1", "amount": 5000, "currency": "usd", "status": "succeeded",
            "metadata": {"relayy_operation_id": "op_1"},
        }], "has_more": False})

    client = client_for(handler)
    refunds = run(StripeAdapter(api_key="sk_test_key", account_id="acct_test", client=client).list_refunds("ch_1"))
    run(client.aclose())

    assert [refund["id"] for refund in refunds] == ["re_1", "re_2"]
    assert refunds[0]["metadata"] == {"relayy_operation_id": "op_1"}
    assert any("starting_after=re_1" in call for call in calls)


def test_stripe_refund_list_fails_closed_on_missing_pagination_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/account":
            return httpx.Response(200, json={"id": "acct_test"})
        return httpx.Response(200, json={"data": []})

    client = client_for(handler)
    try:
        run(StripeAdapter(api_key="sk_test_key", account_id="acct_test", client=client).list_refunds("ch_1"))
    except ProviderError as error:
        assert error.result.state == "unknown"
        assert error.result.error_code == "invalid_response"
    else:
        raise AssertionError("missing has_more was accepted")
    finally:
        run(client.aclose())
