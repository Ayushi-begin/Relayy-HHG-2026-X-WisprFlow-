"""Signed, revision-checked Slack review interactions."""

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
from models import Ticket, TicketRun

log = logging.getLogger(__name__)
router = APIRouter()


def _user(payload: dict[str, Any]) -> tuple[str, str]:
    user = payload.get("user") or {}
    return user.get("id", ""), user.get("username") or user.get("name") or user.get("id", "unknown")


def _target(payload: dict[str, Any]) -> dict[str, str]:
    container = payload.get("container") or {}
    return {"channel": container.get("channel_id") or (payload.get("channel") or {}).get("id", ""),
            "ts": container.get("message_ts", ""), "response_url": payload.get("response_url", "")}


def _value(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
        if not isinstance(value.get("ticket_id"), int) or not isinstance(value.get("run_id"), str) or \
                not isinstance(value.get("expected_version"), int):
            return None
        return value
    except (TypeError, ValueError):
        return None


def _note(ticket: Ticket) -> str:
    if ticket.resolution == "rejected":
        return "Rejected; manual handling is required."
    if ticket.resolution in {"approved", "edited"}:
        return "Authorization queued; provider outcome will be posted when known."
    return f"Ticket is {ticket.status}."


async def _ephemeral(response_url: str, text: str) -> None:
    if not response_url:
        return
    try:
        await slack.post_ephemeral_note(response_url, text)
    except (slack.SlackError, OSError):
        log.exception("Could not post Slack ephemeral response")


async def _refresh_message(ticket: Ticket, state, run: TicketRun, target: dict[str, str]) -> None:
    try:
        await slack.update_message(ticket.id, state, run.id, ticket.version, _note(ticket),
                                   sent_reply=ticket.final_reply, channel=target.get("channel"), ts=target.get("ts"))
    except (slack.SlackError, OSError):
        # The DB action succeeded; never retry delivery based on a display refresh failure.
        log.exception("Could not refresh Slack ticket %s", ticket.id)


@router.post("/slack/interactions")
async def slack_interactions(request: Request, background: BackgroundTasks) -> Response:
    body = await request.body()
    if not slack.verify_signature(body, request.headers.get("X-Slack-Request-Timestamp"),
                                  request.headers.get("X-Slack-Signature")):
        raise HTTPException(401, "Invalid Slack signature")
    try:
        payload = json.loads(parse_qs(body.decode())["payload"][0])
    except (KeyError, IndexError, ValueError):
        raise HTTPException(400, "Missing interaction payload")
    if not slack.interaction_authorized(payload):
        # Slack still receives a quick ACK; no ticket state is touched.
        return Response()
    if payload.get("type") == "block_actions":
        return await _block_action(payload, background)
    if payload.get("type") == "view_submission":
        return _view_submission(payload, background)
    return Response()


async def _block_action(payload: dict[str, Any], background: BackgroundTasks) -> Response:
    actions = payload.get("actions") or []
    if not actions:
        return Response()
    action = actions[0]
    if action.get("action_id") not in {slack.APPROVE, slack.EDIT, slack.REJECT}:
        return Response()
    value = _value(action.get("value", ""))
    target = _target(payload)
    if value is None:
        background.add_task(_ephemeral, target["response_url"], "This review control is invalid. Refresh the ticket.")
        return Response()
    if action["action_id"] == slack.EDIT:
        return await _open_editor(payload, value, target, background)
    user_id, username = _user(payload)
    human_action = "approved" if action["action_id"] == slack.APPROVE else "rejected"
    try:
        ticket, state, applied = ticket_service.apply_human_action(
            value["ticket_id"], human_action, user_id or username, run_id=value["run_id"],
            expected_version=value["expected_version"], channel="slack",
        )
    except (KeyError, ValueError):
        background.add_task(_ephemeral, target["response_url"], "This review is no longer valid. Refresh the ticket.")
        return Response()
    if not applied:
        # A transient stale click must not replace still-valid buttons.
        background.add_task(_ephemeral, target["response_url"], "Someone else changed this ticket; refresh for its current state.")
        return Response()
    with Session(engine) as session:
        run = session.get(TicketRun, value["run_id"])
        current = session.get(Ticket, value["ticket_id"])
    if run and current:
        background.add_task(_refresh_message, current, state, run, target)
    return Response()


async def _open_editor(payload: dict[str, Any], value: dict[str, Any], target: dict[str, str],
                       background: BackgroundTasks) -> Response:
    with Session(engine) as session:
        ticket = session.get(Ticket, value["ticket_id"])
        run = session.get(TicketRun, value["run_id"])
        state = ticket_service.load_state(ticket) if ticket else None
    if not ticket or not run or not state or ticket.current_run_id != run.id or ticket.version != value["expected_version"] or \
            run.state != "reviewable" or not state.action:
        background.add_task(_ephemeral, target["response_url"], "This review is stale. Refresh the ticket.")
        return Response()
    metadata = {**value, "channel": target["channel"], "ts": target["ts"], "response_url": target["response_url"]}
    try:
        await slack.open_modal(payload["trigger_id"], slack.edit_modal(ticket.id, state.action.reply_draft, metadata))
    except (slack.SlackError, OSError):
        background.add_task(_ephemeral, target["response_url"], "Couldn't open the reply editor.")
    return Response()


def _view_submission(payload: dict[str, Any], background: BackgroundTasks) -> Response:
    view = payload.get("view") or {}
    if view.get("callback_id") != slack.EDIT_CALLBACK:
        return Response()
    try:
        metadata = json.loads(view.get("private_metadata") or "{}")
        value = _value(json.dumps({key: metadata[key] for key in ("ticket_id", "run_id", "expected_version")}))
        reply = ((view.get("state") or {}).get("values") or {})[slack.EDIT_BLOCK][slack.EDIT_INPUT].get("value", "").strip()
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"response_action": "errors", "errors": {slack.EDIT_BLOCK: "This review has expired."}})
    if not value or not reply:
        return JSONResponse({"response_action": "errors", "errors": {slack.EDIT_BLOCK: "The reply can't be blank."}})
    user_id, username = _user(payload)
    try:
        ticket, state, applied = ticket_service.apply_human_action(
            value["ticket_id"], "edited", user_id or username, run_id=value["run_id"],
            expected_version=value["expected_version"], reply=reply, channel="slack",
        )
    except (KeyError, ValueError):
        applied = False
    if not applied:
        return JSONResponse({"response_action": "errors", "errors": {slack.EDIT_BLOCK: "This review changed; refresh it."}})
    with Session(engine) as session:
        run = session.get(TicketRun, value["run_id"])
        current = session.get(Ticket, value["ticket_id"])
    if run and current:
        background.add_task(_refresh_message, current, state, run, metadata)
    return Response()
