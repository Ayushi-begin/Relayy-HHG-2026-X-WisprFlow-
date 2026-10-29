"""Slack escalations: Block Kit messages, request verification, and Web API calls.

Posting uses the bot token (chat.postMessage) when SLACK_BOT_TOKEN and SLACK_CHANNEL_ID
are set, otherwise the incoming webhook. The Edit modal needs the bot token.
"""

import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx

import config
from swarm.policy import RULES
from swarm.state import TicketState

log = logging.getLogger(__name__)

API_URL = "https://slack.com/api"
MAX_SIGNATURE_AGE = 60 * 5  # seconds; Slack's recommended replay window
TEXT_LIMIT = 2900  # Block Kit section text limit is 3000

URGENCY_EMOJI = {"critical": "🚨", "high": "🔴", "medium": "🟠", "low": "🟢"}

APPROVE, EDIT, REJECT = "approve", "edit", "reject"
EDIT_CALLBACK = "edit_reply"
EDIT_BLOCK, EDIT_INPUT = "reply", "text"


class SlackError(Exception):
    pass


def is_configured() -> bool:
    return bool((config.SLACK_BOT_TOKEN and config.SLACK_CHANNEL_ID) or config.SLACK_WEBHOOK_URL)


# --- Request verification -------------------------------------------------


def verify_signature(body: bytes, timestamp: str | None, signature: str | None, now: float | None = None) -> bool:
    """Check X-Slack-Signature against the signing secret (https://api.slack.com/authentication/verifying-requests-from-slack)."""
    secret = config.SLACK_SIGNING_SECRET
    if not (secret and timestamp and signature):
        return False
    try:
        age = abs((now or time.time()) - int(timestamp))
    except ValueError:
        return False
    if age > MAX_SIGNATURE_AGE:
        return False
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# --- Block Kit -------------------------------------------------------------


def escape(text: str) -> str:
    """Escape mrkdwn control characters so ticket text can't inject mentions or links."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _truncate(text: str, limit: int = TEXT_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": _truncate(text)}}


def _button(label: str, action_id: str, ticket_id: int, style: str | None = None, confirm: str | None = None):
    button: dict[str, Any] = {
        "type": "button",
        "text": {"type": "plain_text", "text": label, "emoji": True},
        "action_id": action_id,
        "value": str(ticket_id),
    }
    if style:
        button["style"] = style
    if confirm:
        button["confirm"] = {
            "title": {"type": "plain_text", "text": "Are you sure?"},
            "text": {"type": "plain_text", "text": confirm},
            "confirm": {"type": "plain_text", "text": label},
            "deny": {"type": "plain_text", "text": "Cancel"},
        }
    return button


def fallback_text(ticket_id: int, state: TicketState) -> str:
    return f"Escalated ticket #{ticket_id}: {state.ticket.subject}"


def escalation_blocks(
    ticket_id: int, state: TicketState, resolution: str | None = None, sent_reply: str | None = None
) -> list[dict[str, Any]]:
    """The escalation message. With `resolution`, the buttons are replaced by that (mrkdwn) note,
    and `sent_reply` (if it differs from the AI draft) is shown as the reply that went out."""
    triage, research, action = state.triage, state.research, state.action
    urgency = triage.urgency if triage else "medium"

    header = f"{URGENCY_EMOJI.get(urgency, '⚪')} Ticket #{ticket_id}: {state.ticket.subject}"
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": _truncate(header, 150), "emoji": True}}
    ]

    if research and (c := research.customer):
        fields = [
            f"*Customer*\n{escape(c.name)}\n{escape(c.email)}",
            f"*Tier*\n{c.tier.upper()} · {escape(c.plan)}",
            f"*Lifetime value*\n${c.lifetime_value:,.2f}",
            f"*Urgency*\n{urgency}",
        ]
        blocks.append({"type": "section", "fields": [{"type": "mrkdwn", "text": f} for f in fields]})
    else:
        blocks.append(_section(f"*Customer*\nUnknown (customer id {state.ticket.customer_id})"))

    if triage:
        flags = f"{triage.category} · {triage.sentiment} · {triage.language}"
        if triage.is_suspicious:
            flags += " · ⚠️ *flagged suspicious*"
        blocks.append(_section(f"*Triage* ({flags})\n{escape(triage.summary)}"))

    if research and research.key_findings:
        blocks.append(_section("*Key findings*\n" + "\n".join(f"• {escape(f)}" for f in research.key_findings)))

    if action and action.rules_fired:
        why = "\n".join(f"• `{rule}`: {RULES.get(rule, rule)}" for rule in action.rules_fired)
        if action.proposal and action.proposal.decision != "escalate":
            refund = f" ${action.proposal.refund_amount:,.2f}" if action.proposal.refund_amount else ""
            why = f"AI proposed *{action.proposal.decision}{refund}*, overridden by policy:\n{why}"
        blocks.append(_section(f"*Why escalated*\n{why}"))
    elif action:
        blocks.append(_section(f"*Why escalated*\nThe AI recommended a human review: {escape(action.reasoning)}"))
    else:
        blocks.append(_section("*Why escalated*\nThe agent pipeline did not finish; please handle manually."))

    if action:
        edited = sent_reply is not None and sent_reply != action.reply_draft
        reply, label = (sent_reply, "Sent reply (edited)") if edited else (action.reply_draft, "Suggested reply")
        quoted = "\n".join(f">{line}" for line in escape(reply).splitlines())
        blocks.append(_section(f"*{label}*\n{quoted}"))

    blocks.append({"type": "divider"})
    if resolution:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": resolution}]})
    elif action:
        blocks.append({
            "type": "actions",
            "block_id": "ticket_actions",
            "elements": [
                _button("✅ Approve & Send", APPROVE, ticket_id, "primary", "Send the suggested reply to the customer?"),
                _button("✏️ Edit", EDIT, ticket_id),
                _button("❌ Reject", REJECT, ticket_id, "danger", "Reject the AI's reply? The ticket will need manual handling."),
            ],
        })
    return blocks


def edit_modal(ticket_id: int, reply: str, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": EDIT_CALLBACK,
        "private_metadata": json.dumps(metadata),
        "title": {"type": "plain_text", "text": f"Edit reply #{ticket_id}"[:24]},
        "submit": {"type": "plain_text", "text": "Approve & Send"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [{
            "type": "input",
            "block_id": EDIT_BLOCK,
            "label": {"type": "plain_text", "text": "Reply to the customer"},
            "element": {
                "type": "plain_text_input",
                "action_id": EDIT_INPUT,
                "multiline": True,
                "initial_value": reply[:3000],
            },
        }],
    }


# --- Web API -----------------------------------------------------------------


async def _api(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not config.SLACK_BOT_TOKEN:
        raise SlackError(f"{method} needs SLACK_BOT_TOKEN")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{API_URL}/{method}", json=payload, headers={"Authorization": f"Bearer {config.SLACK_BOT_TOKEN}"}
        )
    data = response.json()
    if not data.get("ok"):
        raise SlackError(f"{method} failed: {data.get('error', response.status_code)}")
    return data


async def _post_url(url: str, payload: dict[str, Any]) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload)
    if response.status_code >= 400:
        raise SlackError(f"Slack returned {response.status_code}: {response.text[:200]}")


async def post_escalation(ticket_id: int, state: TicketState) -> str:
    """Post the escalation message and return a short description of where it went."""
    payload = {"text": fallback_text(ticket_id, state), "blocks": escalation_blocks(ticket_id, state)}
    if config.SLACK_BOT_TOKEN and config.SLACK_CHANNEL_ID:
        await _api("chat.postMessage", {"channel": config.SLACK_CHANNEL_ID, **payload})
        return f"channel {config.SLACK_CHANNEL_ID}"
    if config.SLACK_WEBHOOK_URL:
        await _post_url(config.SLACK_WEBHOOK_URL, payload)
        return "the webhook channel"
    raise SlackError("Slack is not configured")


async def update_message(
    ticket_id: int,
    state: TicketState,
    resolution: str,
    *,
    sent_reply: str | None = None,
    channel: str | None = None,
    ts: str | None = None,
    response_url: str | None = None,
) -> None:
    """Replace the buttons on the original message with a resolution note."""
    blocks = escalation_blocks(ticket_id, state, resolution, sent_reply)
    payload = {"text": fallback_text(ticket_id, state), "blocks": blocks}
    if config.SLACK_BOT_TOKEN and channel and ts:
        await _api("chat.update", {"channel": channel, "ts": ts, **payload})
    elif response_url:
        await _post_url(response_url, {"replace_original": True, **payload})
    else:
        raise SlackError("No way to update the original message")


async def open_modal(trigger_id: str, view: dict[str, Any]) -> None:
    await _api("views.open", {"trigger_id": trigger_id, "view": view})


async def post_ephemeral_note(response_url: str, text: str) -> None:
    await _post_url(response_url, {"response_type": "ephemeral", "replace_original": False, "text": text})
