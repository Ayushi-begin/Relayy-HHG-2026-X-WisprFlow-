"""Agent management, audit trail, and global activity replay over an isolated database."""

import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

import agent_service
import audit
import config
import main
from migrations import migrate
from models import AuditLog, Customer, Ticket, TicketEvent, TicketRun, Tier
from routers import tickets as ticket_routes
from swarm import controls
from swarm.state import TicketData, TicketState, Usage

TOKEN = "t" * 40


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    migrate(engine)
    for module in (agent_service, audit, ticket_routes):
        monkeypatch.setattr(module, "engine", engine)
    monkeypatch.setattr(config, "DEMO_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setattr(main.llm, "configured_providers", lambda: set())
    controls.replace(set())
    with Session(engine) as session:
        session.add(Customer(id=1, name="A", email="a@example.test", plan="Pro", tier=Tier.pro, signup_date=dt.date(2026, 1, 1)))
        session.add(Ticket(id=1, customer_id=1, subject="s", body="b", created_at=dt.datetime.now(dt.timezone.utc)))
        state = TicketState(
            ticket=TicketData(id=1, customer_id=1, subject="s", body="b"),
            provenance={"triage": "real_model", "research": "simulated", "action": "failed"},
            provenance_errors={"action": "Model provider request failed (gemini: http_503)"},
            usage=[Usage(agent="triage", provider="gemini", model="g-fast", input_tokens=10, output_tokens=4,
                         latency_ms=120, cost_usd=None)],
        )
        session.add(TicketRun(id="run-1", ticket_id=1, run_number=1, state="completed", input_snapshot={},
                              agent_state=state.model_dump(mode="json"), started_at=dt.datetime.now(dt.timezone.utc)))
        for kind in ("status", "agent", "agent"):
            session.add(TicketEvent(ticket_id=1, run_id="run-1", kind=kind, timestamp=dt.datetime.now(dt.timezone.utc),
                                    data={"kind": kind}))
        session.commit()
    yield TestClient(main.app), engine  # no lifespan: nothing global is migrated or started
    controls.replace(set())


def auth():
    return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def test_agents_report_profiles_routes_and_real_statistics(client):
    http, _ = client
    agents = {agent["id"]: agent for agent in http.get("/agents").json()}
    assert set(agents) == {"triage", "research", "action"}
    triage = agents["triage"]
    assert triage["tier"] == "fast" and triage["mode"] == "simulated"
    assert triage["unavailable_reason"] == "model credentials or configured model id unavailable"
    assert triage["stats"]["runs"] == 1 and triage["stats"]["real_model"] == 1
    assert triage["stats"]["success_rate"] == 1.0 and triage["stats"]["avg_latency_ms"] == 120
    assert triage["stats"]["models"] == {"gemini:g-fast": 1}
    assert triage["stats"]["cost_usd"] is None  # unverified pricing is never invented
    assert agents["action"]["stats"]["recent"][0]["error"] == "Model provider request failed (gemini: http_503)"


def test_disabling_an_agent_requires_the_operator_and_is_audited(client):
    http, engine = client
    assert http.post("/agents/triage/settings", json={"model_enabled": False}).status_code == 401
    response = http.post("/agents/triage/settings", json={"model_enabled": False}, headers=auth())
    assert response.status_code == 200
    assert response.json()["model_enabled"] is False
    assert response.json()["unavailable_reason"] == controls.DISABLED_REASON
    assert not controls.model_enabled("triage")
    with Session(engine) as session:
        row = session.exec(select(AuditLog)).one()
        assert (row.action, row.target, row.detail) == ("agent.model_enabled", "triage", {"from": True, "to": False})
    assert http.get("/audit", headers=auth()).json()[0]["action"] == "agent.model_enabled"
    assert http.post("/agents/nope/settings", json={"model_enabled": True}, headers=auth()).status_code == 404


def test_persisted_controls_load_at_startup(client):
    http, _ = client
    http.post("/agents/research/settings", json={"model_enabled": False}, headers=auth())
    controls.replace(set())
    agent_service.load_controls()
    assert not controls.model_enabled("research") and controls.model_enabled("triage")


def _events(text: str) -> list[tuple[str | None, str]]:
    parsed = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":") and ": " in line)
        if "event" in fields:
            parsed.append((fields.get("id"), fields["event"]))
    return parsed


def _first_messages(http, headers, count):
    """Read the replayed part of the endless SSE stream, then disconnect."""
    lines, blocks = [], 0
    with http.stream("GET", "/events/stream", headers=headers) as response:
        for line in response.iter_lines():
            lines.append(line)
            if line.startswith("event: "):
                blocks += 1
                if blocks == count:
                    break
    return _events("\n".join(lines) + "\n\n")


def test_global_stream_replays_after_last_event_id(client):
    http, _ = client
    assert _first_messages(http, {}, 3) == [("1", "event"), ("2", "event"), ("3", "event")]
    assert _first_messages(http, {"Last-Event-ID": "2"}, 1) == [("3", "event")]
    assert _first_messages(http, {"Last-Event-ID": "999"}, 1) == [(None, "reset")]
