"""Explicit trusted-local provider binding. This command never sends or refunds.

Only a human operator can map a fictional demo identity to an approved test inbox
and an owned Stripe test charge. Never put this setup behind a public API.
"""

import argparse
import asyncio
import datetime as dt
from decimal import Decimal, InvalidOperation

from sqlmodel import Session, select

import config
from db import create_db_and_tables, engine
from fulfillment import store
from integrations.stripe import StripeAdapter
from models import Customer, DeliveryOperation, Payment


async def bind(args) -> None:
    create_db_and_tables()
    if config.DELIVERY_MODE != "test" or len(config.DEMO_OPERATOR_TOKEN) < 32:
        raise ValueError("Set DELIVERY_MODE=test and a secure demo operator token before binding.")
    recipient = args.recipient.strip().lower()
    if recipient not in config.RESEND_ALLOWED_RECIPIENTS:
        raise ValueError("The recipient must appear in RESEND_ALLOWED_RECIPIENTS.")
    charge = None
    if args.payment_id is not None:
        if not all((args.charge_id, args.stripe_customer_id, args.purchase_type, args.workspace_id, args.billing_period, args.product_id)):
            raise ValueError("A payment binding requires a charge, expected Stripe customer, purchase type, workspace, billing period, and product.")
        provider = StripeAdapter()
        try:
            charge = await provider.inspect_charge(args.charge_id)
            refunds = await provider.list_refunds(args.charge_id)
        finally:
            await provider.aclose()
        if (charge.account_id != config.STRIPE_ACCOUNT_ID or charge.customer_id != args.stripe_customer_id
                or charge.livemode or charge.currency != "usd" or not charge.paid or not charge.captured
                or charge.amount_refunded or refunds or not charge.created_at):
            raise ValueError("The provider charge is not an eligible owned, captured, unrefunded USD test charge.")
    with Session(engine) as session:
        customer = session.get(Customer, args.customer_id)
        if not customer:
            raise ValueError("Unknown local customer.")
        if session.exec(select(Customer.id).where(Customer.email == recipient).limit(1)).first():
            raise ValueError("Fictional seeded customer addresses cannot become delivery targets.")
        # Existing operations on this customer's tickets also freeze recipient identity.
        from models import Ticket
        if session.exec(select(DeliveryOperation).join(Ticket, Ticket.id == DeliveryOperation.ticket_id).where(Ticket.customer_id == customer.id).limit(1)).first():
            raise ValueError("This customer already has a delivery operation. Use a fresh demo database or keep the existing binding.")
        customer.demo_recipient = recipient
        customer.binding_verified_at = store.now()
        if charge:
            payment = session.get(Payment, args.payment_id)
            if not payment or payment.customer_id != customer.id:
                raise ValueError("The exact local payment must belong to this customer.")
            try:
                cents = Decimal(str(payment.amount)) * 100
                if not cents.is_finite() or cents != cents.to_integral_value() or int(cents) != charge.amount_minor:
                    raise ValueError("Local and provider payment amounts differ.")
            except InvalidOperation as exc:
                raise ValueError("Local payment amount is invalid.") from exc
            if customer.stripe_customer_id and customer.stripe_customer_id != charge.customer_id:
                raise ValueError("Customer already has a different Stripe binding.")
            if payment.stripe_charge_id and payment.stripe_charge_id != charge.id:
                raise ValueError("Payment already has a different Stripe binding.")
            customer.stripe_customer_id, customer.provider_account_id = charge.customer_id, charge.account_id
            payment.stripe_charge_id, payment.provider_account_id = charge.id, charge.account_id
            payment.amount_minor, payment.currency, payment.livemode = charge.amount_minor, charge.currency, False
            payment.binding_verified_at = store.now()
            payment.eligibility_evidence = {
                "purchase_type": args.purchase_type,
                "charge_at": dt.datetime.fromtimestamp(charge.created_at, dt.timezone.utc).isoformat(),
                "verified_at": store.now().isoformat(), "workspace_id": args.workspace_id,
                "billing_period": args.billing_period, "product_id": args.product_id,
                "eligibility_reason": "operator_verified_demo_fixture",
                "valid_until": (store.now() + dt.timedelta(days=14)).isoformat(),
            }
            session.add(payment)
        session.add(customer)
        session.commit()
    print("Test binding verified and saved. No refund or email was executed. Create a new ticket after setup.")


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--customer-id", type=int, required=True)
    result.add_argument("--recipient", required=True)
    result.add_argument("--payment-id", type=int)
    result.add_argument("--charge-id")
    result.add_argument("--stripe-customer-id")
    result.add_argument("--purchase-type", choices=["monthly", "annual", "addon", "upgrade", "enterprise"])
    result.add_argument("--workspace-id")
    result.add_argument("--billing-period")
    result.add_argument("--product-id")
    return result


if __name__ == "__main__":
    asyncio.run(bind(parser().parse_args()))
