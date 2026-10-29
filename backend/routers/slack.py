"""POST /slack/interactions: button clicks and the Edit modal from escalation messages.

Slack expects a response within 3 seconds, so message updates run as background tasks.
Opening the modal is done inline because its trigger_id expires after 3 seconds.
"""

import json
import logging
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlmodel import Session

import ticket_service
from db import engine
from integrations import slack
from models import Ticket
from swarm.state import TicketState

log = logging.getLogger(__name__)
router = APIRouter()

NOTES = {
    "approved": "✅ Approved by {who}; reply sent to the customer",
    "edited": "✏️ Edited and approved by {who}; reply sent to the customer",
    "rejected": "❌ Rejected by {who}; needs manual handling",
    "auto_resolved": "🤖 Resolved automatically",
    "auto_refunded": "🤖 Refunded automatically",
}


def _note(ticket: Ticket, slack_user_id: str | None = None) -> str:
    """Resolution line for the Slack message. Mentions the user when we have their Slack id."""
    who = f"<@{slack_user_id}>" if slack_user_id else f"@{slack.escape(ticket.resolved_by or 'someone')}"
    note = NOTES.get(ticket.resolution or "", f"Ticket is {ticket.status}").format(who=who)
    if ticket.updated_at:
        ts = int(ticket.updated_at.timestamp())
        note += f" · <!date^{ts}^{{date_short_pretty}} at {{time}}|{ticket.updated_at:%Y-%m-%d %H:%M} UTC>"
    return note


def _user(payload: dict[str, Any]) -> tuple[str, str]:
    user = payload.get("user", {})
    return user.get("id", ""), user.get("username") or user.get("name") or user.get("id", "unknown")


async def _refresh_message(ticket: Ticket, state: TicketState, note: str, target: dict[str, Any]) -> None:
    try:
        await slack.update_message(ticket.id, state, note, sent_reply=ticket.final_reply, **target)
    except (slack.SlackError, OSError):
        log.exception("Could not update the Slack message for ticket %s", ticket.id)


async def _ephemeral(response_url: str | None, text: str) -> None:
    if not response_url:
        return
    try:
        await slack.post_ephemeral_note(response_url, text)
    except (slack.SlackError, OSError):
        log.exception("Could not post an ephemeral Slack note")


@router.post("/slack/interactions")
async def slack_interactions(request: Request, background: BackgroundTasks) -> Response:
    body = await request.body()
    if not slack.verify_signature(
        body, request.headers.get("X-Slack-Request-Timestamp"), request.headers.get("X-Slack-Signature")
    ):
        raise HTTPException(401, "Invalid Slack signature")

    try:
        payload = json.loads(parse_qs(body.decode())["payload"][0])
    except (KeyError, IndexError, ValueError):
        raise HTTPException(400, "Missing interaction payload")

    match payload.get("type"):
        case "block_actions":
            return await _block_action(payload, background)
        case "view_submission":
            return _view_submission(payload, background)
    return Response()


async def _block_action(payload: dict[str, Any], background: BackgroundTasks) -> Response:
    action = payload["actions"][0]
    action_id = action.get("action_id")
    if action_id not in (slack.APPROVE, slack.EDIT, slack.REJECT):
        return Response()

    ticket_id = int(action["value"])
    user_id, username = _user(payload)
    container = payload.get("container", {})
    target = {
        "channel": container.get("channel_id") or payload.get("channel", {}).get("id"),
        "ts": container.get("message_ts"),
        "response_url": payload.get("response_url"),
    }

    if action_id == slack.EDIT:
        return await _open_editor(payload, ticket_id, target, background)

    human_action = "approved" if action_id == slack.APPROVE else "rejected"
    try:
        ticket, state, applied = ticket_service.apply_human_action(ticket_id, human_action, username, channel="slack")
    except KeyError:
        background.add_task(_ephemeral, target["response_url"], f"Ticket #{ticket_id} no longer exists.")
        return Response()
    # If someone already handled it, show their decision instead.
    background.add_task(_refresh_message, ticket, state, _note(ticket, user_id if applied else None), target)
    return Response()


async def _open_editor(
    payload: dict[str, Any], ticket_id: int, target: dict[str, Any], background: BackgroundTasks
) -> Response:
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        state = ticket_service.load_state(ticket) if ticket else None

    if ticket is None or state.action is None:
        background.add_task(_ephemeral, target["response_url"], f"Ticket #{ticket_id} has no reply to edit.")
        return Response()
    if ticket.status != "escalated":
        background.add_task(_refresh_message, ticket, state, _note(ticket), target)
        return Response()

    view = slack.edit_modal(ticket_id, state.action.reply_draft, {"ticket_id": ticket_id, **target})
    try:
        await slack.open_modal(payload["trigger_id"], view)
    except (slack.SlackError, OSError) as exc:
        background.add_task(_ephemeral, target["response_url"], f"Couldn't open the editor: {exc}")
    return Response()


def _view_submission(payload: dict[str, Any], background: BackgroundTasks) -> Response:
    view = payload.get("view", {})
    if view.get("callback_id") != slack.EDIT_CALLBACK:
        return Response()

    meta = json.loads(view.get("private_metadata") or "{}")
    ticket_id = meta.pop("ticket_id")
    reply = (view["state"]["values"][slack.EDIT_BLOCK][slack.EDIT_INPUT].get("value") or "").strip()
    if not reply:
        return JSONResponse({"response_action": "errors", "errors": {slack.EDIT_BLOCK: "The reply can't be empty."}})

    user_id, username = _user(payload)
    try:
        ticket, state, applied = ticket_service.apply_human_action(ticket_id, "edited", username, reply, channel="slack")
    except KeyError:
        return JSONResponse({"response_action": "errors", "errors": {slack.EDIT_BLOCK: "This ticket no longer exists."}})
    if not applied:
        return JSONResponse({
            "response_action": "errors",
            "errors": {slack.EDIT_BLOCK: f"Already handled: ticket is {ticket.status}."},
        })

    background.add_task(_refresh_message, ticket, state, _note(ticket, user_id), meta)
    return Response()  # an empty 200 closes the modal
