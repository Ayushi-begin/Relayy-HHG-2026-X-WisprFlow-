"""Focused deterministic coverage for persisted review/run lifecycle invariants."""

import asyncio
import datetime as dt
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ticket_service
from events import EventBus
from models import Customer, DeliveryOperation, Payment, PaymentStatus, Ticket, TicketRun, TicketStatus, Tier
from swarm.state import ActionProposal, ActionResult, Event, PaymentRecord, ResearchResult, TicketData, TicketState, Usage


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)
        self.original_engine = ticket_service.engine
        ticket_service.engine = self.engine
        with Session(self.engine) as session:
            customer = Customer(id=1, name="Review Customer", email="review@example.test", plan="Pro", tier=Tier.pro,
                                signup_date=dt.date(2026, 1, 1))
            ticket = Ticket(id=1, customer_id=1, subject="Need help", body="Please review", created_at=dt.datetime.now(dt.timezone.utc),
                            status=TicketStatus.escalated, current_run_id="run-1", version=7)
            proposal = ActionProposal(decision="auto_refund", reply_draft="Refunding", holding_reply="A specialist will follow up.",
                                      refund_amount=30.0, reasoning="proposed", confidence=0.9)
            action = ActionResult(decision="escalate", reply_draft="A specialist will follow up.", refund_amount=None,
                                  reasoning="approval required", confidence=0.9, proposal=proposal)
            state = TicketState(ticket=TicketData(id=1, customer_id=1, subject=ticket.subject, body=ticket.body), action=action)
            run = TicketRun(id="run-1", ticket_id=1, run_number=1, state="reviewable",
                            input_snapshot=state.ticket.model_dump(mode="json"), agent_state=state.model_dump(mode="json"),
                            started_at=dt.datetime.now(dt.timezone.utc))
            session.add(customer)
            session.add(ticket)
            session.add(run)
            session.commit()

    def tearDown(self) -> None:
        ticket_service.engine = self.original_engine
        ticket_service._live.clear()

    def test_blank_edit_rejected_before_claim(self) -> None:
        with self.assertRaises(ValueError):
            ticket_service.apply_human_action(1, "edited", "operator", run_id="run-1", expected_version=7, reply="  ")
        with Session(self.engine) as session:
            ticket = session.get(Ticket, 1)
            self.assertEqual(ticket.version, 7)
            self.assertEqual(ticket.status, TicketStatus.escalated)

    def test_stale_version_cannot_claim_review(self) -> None:
        ticket, _, applied = ticket_service.apply_human_action(1, "rejected", "operator", run_id="run-1", expected_version=6)
        self.assertFalse(applied)
        self.assertEqual(ticket.version, 7)
        with Session(self.engine) as session:
            self.assertEqual(session.get(TicketRun, "run-1").review_outcome, None)

    def test_holding_reply_approval_never_freezes_refund(self) -> None:
        ticket, _, applied = ticket_service.apply_human_action(
            1, "approved", "operator", run_id="run-1", expected_version=7, channel="web"
        )
        self.assertTrue(applied)
        with Session(self.engine) as session:
            operations = session.exec(select(DeliveryOperation).where(DeliveryOperation.ticket_id == 1)).all()
            run = session.get(TicketRun, "run-1")
            self.assertEqual([operation.kind for operation in operations], ["email"])
            self.assertEqual(run.review_actor, "operator")
            self.assertEqual(run.authorized_plan["source"], "approved")
            self.assertEqual(ticket_service.serialize_ticket(session, session.get(Ticket, 1))["version"], 8)

        # A blocked authorization remains escalated for manual attention, but its
        # run is no longer reviewable. A stale Reject cannot remove its ledger.
        ticket, _, applied = ticket_service.apply_human_action(
            1, "rejected", "operator", run_id="run-1", expected_version=8
        )
        self.assertFalse(applied)
        self.assertEqual(ticket.current_run_id, "run-1")

    def test_start_processing_claims_version_and_keeps_run_scalars_after_commit(self) -> None:
        async def exercise() -> None:
            async def mocked_pipeline(state: TicketState):
                event = Event(agent="triage", status="done", message="mocked pipeline")
                state.events.append(event)
                yield event

            with patch.object(ticket_service, "run_pipeline", mocked_pipeline):
                self.assertTrue(ticket_service.start_processing(1))
                task = next(iter(ticket_service._tasks))
                # The in-memory claim closes the local race before the mock starts.
                self.assertFalse(ticket_service.start_processing(1))
                await task
                with Session(self.engine) as session:
                    ticket = session.get(Ticket, 1)
                    run = session.get(TicketRun, ticket.current_run_id)
                    self.assertEqual(ticket.status, TicketStatus.escalated)
                    self.assertEqual(ticket.version, 9)
                    self.assertEqual(ticket.current_run_id, run.id)
                    self.assertEqual(run.state, "reviewable")
                    self.assertEqual(run.agent_state["events"][-1]["message"], "mocked pipeline")

        asyncio.run(exercise())

    def test_agent_event_persists_incremental_run_snapshot(self) -> None:
        event = Event(agent="triage", status="done", message="classified")
        with Session(self.engine) as session:
            run = session.get(TicketRun, "run-1")
            run.state = "processing"
            session.add(run)
            session.commit()
        state = TicketState(ticket=TicketData(id=1, customer_id=1, subject="Need help", body="Please review"), events=[event])
        ticket_service._record_agent_event(1, "run-1", state, event)
        with Session(self.engine) as session:
            run = session.get(TicketRun, "run-1")
            ticket = session.get(Ticket, 1)
            self.assertEqual(run.agent_state["events"][-1]["message"], "classified")
            self.assertEqual(ticket.swarm_state["events"][-1]["agent"], "triage")

    def test_late_slack_target_survives_a_review_during_message_creation(self) -> None:
        from integrations import slack
        refreshed = []

        async def post(*_):
            with Session(self.engine) as session:
                run = session.get(TicketRun, "run-1")
                run.state = "authorized"
                session.add(run)
                session.commit()
            return {"channel": "demo", "ts": "1"}

        async def refresh(*args):
            refreshed.append(args)

        with Session(self.engine) as session:
            ticket = session.get(Ticket, 1)
            state = ticket_service.load_state(ticket)
        with patch.object(slack, "is_configured", return_value=True), patch.object(slack, "post_escalation", post), patch.object(slack, "refresh_review", refresh):
            asyncio.run(ticket_service._post_slack_review(ticket, "run-1", state))
        with Session(self.engine) as session:
            self.assertEqual(session.get(TicketRun, "run-1").slack_target, {"channel": "demo", "ts": "1"})
        self.assertEqual(refreshed, [(1, "run-1", None)])

    def test_metrics_preserves_impact_schema_and_run_audit_costs(self) -> None:
        with Session(self.engine) as session:
            ticket = session.get(Ticket, 1)
            current = session.get(TicketRun, "run-1")
            historic_state = TicketState(
                ticket=TicketData(id=1, customer_id=1, subject=ticket.subject, body=ticket.body),
                usage=[Usage(agent="triage", model="verified", cost_usd=1.25)],
            )
            current_state = TicketState(
                ticket=TicketData(id=1, customer_id=1, subject=ticket.subject, body=ticket.body),
                action=ActionResult(decision="escalate", reply_draft="Holding", reasoning="Review", confidence=0.8),
                usage=[Usage(agent="action", model="unpriced", cost_usd=None)],
                provenance={"triage": "real_model", "research": "real_model", "action": "real_model"},
            )
            historical = TicketRun(id="run-0", ticket_id=1, run_number=0, state="superseded",
                                   input_snapshot=historic_state.ticket.model_dump(mode="json"),
                                   agent_state=historic_state.model_dump(mode="json"), started_at=dt.datetime.now(dt.timezone.utc))
            current.agent_state = current_state.model_dump(mode="json")
            current.review_outcome = "approved"
            current.state = "processing"
            ticket.swarm_state = current.agent_state
            ticket.resolution = None
            session.add(historical)
            session.add(current)
            session.add(ticket)
            session.commit()

        ticket_service._finalize_run(1, "run-1", current_state, status=TicketStatus.escalated, run_state="reviewable")

        report = ticket_service.metrics()
        for key in ("by_category", "escalated_rate", "avg_time_to_decision_seconds", "cost_by_agent",
                    "escalations_by_rule", "ai_reply_acceptance_rate", "suspicious_flagged", "timeline"):
            self.assertIn(key, report)
        self.assertEqual(report["human_reviews"]["approved"], 1)
        self.assertIsNone(report["total_cost_usd"])
        self.assertEqual(report["cost_by_agent"]["triage"], 1.25)
        self.assertIsNone(report["cost_by_agent"]["action"])
        with Session(self.engine) as session:
            self.assertEqual(session.get(TicketRun, "run-1").execution_mode, "real_model")

    def test_legacy_ticket_has_audit_run_projection(self) -> None:
        with Session(self.engine) as session:
            legacy = Ticket(id=2, customer_id=1, subject="Legacy", body="Local only", created_at=dt.datetime.now(dt.timezone.utc),
                            legacy_local_only=True, swarm_state={"ticket": {"id": 2, "customer_id": 1,
                            "subject": "Legacy", "body": "Local only"}})
            session.add(legacy)
            session.commit()
            summary = ticket_service.run_summaries(session, 2)
        self.assertEqual(summary[0]["state"], "legacy_local_only")
        self.assertEqual(summary[0]["agent_state"]["ticket"]["id"], 2)

    def test_reserved_auto_refund_becomes_manual_only_not_reviewable(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        with Session(self.engine) as session:
            first_ticket = session.get(Ticket, 1)
            first_run = session.get(TicketRun, "run-1")
            first_ticket.status = TicketStatus.processing
            first_run.state = "processing"
            second_ticket = Ticket(id=2, customer_id=1, subject="Second refund", body="same charge", created_at=now,
                                   status=TicketStatus.processing, current_run_id="run-2", version=1)
            second_run = TicketRun(id="run-2", ticket_id=2, run_number=1, state="processing",
                                   input_snapshot={"subject": "Second refund"}, started_at=now)
            payment = Payment(id=1, customer_id=1, amount=30.0, date=now, status=PaymentStatus.succeeded,
                              description="shared", stripe_charge_id="ch_shared", amount_minor=3000, currency="usd", livemode=False)
            session.add(first_ticket)
            session.add(first_run)
            session.add(second_ticket)
            session.add(second_run)
            session.add(payment)
            session.commit()

        def automatic_state(ticket_id: int, subject: str) -> TicketState:
            return TicketState(
                ticket=TicketData(id=ticket_id, customer_id=1, subject=subject, body="refund"),
                research=ResearchResult(payments=[PaymentRecord(id=1, customer_id=1, amount=30.0, date=now,
                    status="succeeded", description="shared", stripe_charge_id="ch_shared", amount_minor=3000,
                    currency="usd", livemode=False)]),
                action=ActionResult(decision="auto_refund", reply_draft="Refund confirmed", reasoning="eligible",
                                    confidence=0.9, payment_id=1, refund_amount_minor=3000),
            )

        async def no_op_pipeline(state: TicketState):
            if False:
                yield Event(agent="action", status="done", message="unreachable")

        with patch.object(ticket_service, "run_pipeline", no_op_pipeline):
            asyncio.run(ticket_service._process(1, "run-1", automatic_state(1, "Need help")))
            asyncio.run(ticket_service._process(2, "run-2", automatic_state(2, "Second refund")))

        with Session(self.engine) as session:
            second = session.get(Ticket, 2)
            second_run = session.get(TicketRun, "run-2")
            second_ops = session.exec(select(DeliveryOperation).where(DeliveryOperation.ticket_id == 2)).all()
            self.assertEqual(second_run.state, "authorization_failed")
            self.assertEqual(second.fulfillment_state, "blocked")
            self.assertTrue(second.manual_followup_required)
            self.assertEqual(second.resolution, "refund_target_reserved")
            self.assertEqual(second_ops, [])
            _, _, applied = ticket_service.apply_human_action(2, "approved", "operator", run_id="run-2",
                                                               expected_version=second.version)
            self.assertFalse(applied)

    def test_event_bus_invalidates_overflow_without_unbounded_queue(self) -> None:
        async def exercise() -> None:
            bus = EventBus()
            with bus.subscribe(1) as queue:
                for event_id in range(200):
                    bus.publish_ticket_event(1, event_id, {"run_id": "r"})
                await asyncio.sleep(0)
                self.assertLessEqual(queue.qsize(), 128)
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
