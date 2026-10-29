"""Signed Slack review surfaces for already-persisted reviewable ticket runs."""

import hashlib
import hmac
import json
import time
from typing import Any

import httpx

import config
from swarm.policy import RULES
from swarm.state import TicketState

API_URL = "https://slack.com/api"
MAX_SIGNATURE_AGE = 60 * 5
TEXT_LIMIT = 2900
URGENCY_EMOJI = {"critical": "🚨", "high": "🔴", "medium": "🟠", "low": "🟢"}
APPROVE, EDIT, REJECT = "approve", "edit", "reject"
EDIT_CALLBACK = "edit_reply"
EDIT_BLOCK, EDIT_INPUT = "reply", "text"


class SlackError(Exception):
    pass


def is_configured() -> bool:
    return bool(config.SLACK_BOT_TOKEN and config.SLACK_CHANNEL_ID)


def interaction_authorized(payload: dict[str, Any]) -> bool:
    team_id = (payload.get("team") or {}).get("id")
    user_id = (payload.get("user") or {}).get("id")
    return bool(config.SLACK_ALLOWED_TEAM_ID and config.SLACK_ALLOWED_REVIEWERS and
                hmac.compare_digest(team_id or "", config.SLACK_ALLOWED_TEAM_ID) and
                user_id in config.SLACK_ALLOWED_REVIEWERS)


def verify_signature(body: bytes, timestamp: str | None, signature: str | None, now: float | None = None) -> bool:
    secret = config.SLACK_SIGNING_SECRET
    if not (secret and timestamp and signature):
        return False
    try:
        age = abs((now or time.time()) - int(timestamp))
    except ValueError:
        return False
    if age > MAX_SIGNATURE_AGE:
        return False
    expected = "v0=" + hmac.new(secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _truncate(text: str, limit: int = TEXT_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": _truncate(text)}}


def _value(ticket_id: int, run_id: str, version: int) -> str:
    return json.dumps({"ticket_id": ticket_id, "run_id": run_id, "expected_version": version}, separators=(",", ":"))


def _button(label: str, action_id: str, ticket_id: int, run_id: str, version: int, style: str | None = None,
            confirm: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "button", "text": {"type": "plain_text", "text": label, "emoji": True},
                              "action_id": action_id, "value": _value(ticket_id, run_id, version)}
    if style:
        result["style"] = style
    if confirm:
        result["confirm"] = {"title": {"type": "plain_text", "text": "Are you sure?"},
                             "text": {"type": "plain_text", "text": confirm},
                             "confirm": {"type": "plain_text", "text": label},
                             "deny": {"type": "plain_text", "text": "Cancel"}}
    return result


def fallback_text(ticket_id: int, state: TicketState) -> str:
    return f"Escalated ticket #{ticket_id}: {state.ticket.subject}"


def escalation_blocks(ticket_id: int, state: TicketState, run_id: str, version: int, resolution: str | None = None,
                      sent_reply: str | None = None) -> list[dict[str, Any]]:
    triage, research, action = state.triage, state.research, state.action
    urgency = triage.urgency if triage else "medium"
    blocks: list[dict[str, Any]] = [{"type": "header", "text": {"type": "plain_text", "emoji": True,
        "text": _truncate(f"{URGENCY_EMOJI.get(urgency, '⚪')} Ticket #{ticket_id}: {state.ticket.subject}", 150)}}]
    if research and research.customer:
        customer = research.customer
        fields = [f"*Customer*\n{escape(customer.name)}\n{escape(customer.email)}",
                  f"*Tier*\n{escape(customer.tier.upper())}", f"*Urgency*\n{urgency}"]
        blocks.append({"type": "section", "fields": [{"type": "mrkdwn", "text": field} for field in fields]})
    if triage:
        blocks.append(_section(f"*Triage* ({triage.category} · {triage.sentiment})\n{escape(triage.summary)}"))
    if action and action.rules_fired:
        why = "\n".join(f"• `{rule}`: {RULES.get(rule, rule)}" for rule in action.rules_fired)
        blocks.append(_section(f"*Why escalated*\n{why}"))
    elif action:
        blocks.append(_section(f"*Why escalated*\n{escape(action.reasoning)}"))
    if action:
        edited = sent_reply is not None and sent_reply != action.reply_draft
        reply, label = (sent_reply, "Authorized reply (edited)") if edited else (action.reply_draft, "Suggested reply")
        blocks.append(_section(f"*{label}*\n" + "\n".join(f">{line}" for line in escape(reply).splitlines())))
    blocks.append({"type": "divider"})
    if resolution:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": resolution}]})
    elif action:
        blocks.append({"type": "actions", "block_id": "ticket_actions", "elements": [
            _button("Approve reply", APPROVE, ticket_id, run_id, version, "primary", "Queue this safe displayed reply?"),
            _button("Edit reply", EDIT, ticket_id, run_id, version),
            _button("Reject", REJECT, ticket_id, run_id, version, "danger", "Reject this reply?"),
        ]})
    return blocks


def edit_modal(ticket_id: int, reply: str, metadata: dict[str, Any]) -> dict[str, Any]:
    return {"type": "modal", "callback_id": EDIT_CALLBACK, "private_metadata": json.dumps(metadata),
            "title": {"type": "plain_text", "text": f"Edit reply #{ticket_id}"[:24]},
            "submit": {"type": "plain_text", "text": "Queue reply"},
            "close": {"type": "plain_text", "text": "Cancel"}, "blocks": [{"type": "input", "block_id": EDIT_BLOCK,
            "label": {"type": "plain_text", "text": "Reply to the customer"}, "element": {"type": "plain_text_input",
            "action_id": EDIT_INPUT, "multiline": True, "initial_value": reply[:3000]}}]}


async def _api(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not config.SLACK_BOT_TOKEN:
        raise SlackError(f"{method} needs SLACK_BOT_TOKEN")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(f"{API_URL}/{method}", json=payload,
                                     headers={"Authorization": f"Bearer {config.SLACK_BOT_TOKEN}"})
    try:
        data = response.json()
    except ValueError as exc:
        raise SlackError(f"{method} returned invalid JSON") from exc
    if not data.get("ok"):
        raise SlackError(f"{method} failed: {data.get('error', response.status_code)}")
    return data


async def _post_url(url: str, payload: dict[str, Any]) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload)
    if response.status_code >= 400:
        raise SlackError(f"Slack returned {response.status_code}")


async def post_escalation(ticket_id: int, state: TicketState, run_id: str, version: int) -> dict[str, str]:
    if not is_configured():
        raise SlackError("Slack interactive posting is not configured")
    data = await _api("chat.postMessage", {"channel": config.SLACK_CHANNEL_ID, "text": fallback_text(ticket_id, state),
        "blocks": escalation_blocks(ticket_id, state, run_id, version)})
    return {"channel": config.SLACK_CHANNEL_ID, "ts": data.get("ts", "")}


async def update_message(ticket_id: int, state: TicketState, run_id: str, version: int, resolution: str, *,
                         sent_reply: str | None = None, channel: str | None = None, ts: str | None = None) -> None:
    if not channel or not ts:
        raise SlackError("No persisted Slack message target")
    await _api("chat.update", {"channel": channel, "ts": ts, "text": fallback_text(ticket_id, state),
        "blocks": escalation_blocks(ticket_id, state, run_id, version, resolution, sent_reply)})


async def open_modal(trigger_id: str, view: dict[str, Any]) -> None:
    await _api("views.open", {"trigger_id": trigger_id, "view": view})


async def post_ephemeral_note(response_url: str, text: str) -> None:
    await _post_url(response_url, {"response_type": "ephemeral", "replace_original": False, "text": text})


async def refresh_delivery(ticket_id: int, target: dict[str, str]) -> None:
    """Reflect committed provider status; called only after worker transaction commit."""
    from sqlmodel import Session

    from db import engine
    from models import Ticket, TicketRun
    import ticket_service

    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        run = session.get(TicketRun, ticket.current_run_id) if ticket and ticket.current_run_id else None
        if not ticket or not run:
            return
        state = ticket_service.load_state(ticket)
        operations = ticket_service.serialize_ticket(session, ticket)["operations"]
    labels = []
    for operation in operations:
        if operation["kind"] == "refund":
            labels.append(f"Stripe test refund: {operation['state']}")
        elif operation["kind"] == "email":
            labels.append(f"Resend email: {operation['state']}")
    outcome = " · ".join(labels) or ticket.fulfillment_state
    authorized_reply = (run.authorized_plan or {}).get("reply")
    await update_message(ticket_id, state, run.id, ticket.version, f"{outcome}.", sent_reply=authorized_reply,
                         channel=target.get("channel"), ts=target.get("ts"))
