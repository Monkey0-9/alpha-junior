"""Record a manual broker reconciliation for one durable order intent.

Only use after comparing broker order history, executions and positions with
this application's order intent. This tool records the operator assertion; it
does not query or independently verify the broker.
"""
from __future__ import annotations

import argparse
import json

from nexus.audit.governance import GovernanceEngine


TERMINAL = {"FILLED", "CANCELLED", "EXPIRED", "REJECTED"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-order-id", required=True)
    parser.add_argument("--status", required=True, choices=sorted(TERMINAL))
    parser.add_argument("--operator", required=True)
    parser.add_argument("--reconciliation-reference", required=True)
    parser.add_argument("--broker-order-id")
    parser.add_argument("--fill-price", type=float)
    args = parser.parse_args()

    governance = GovernanceEngine()
    intent = governance.get_order_intent(args.client_order_id)
    if intent is None:
        parser.error("Unknown client_order_id.")

    if args.status == "FILLED":
        if args.fill_price is None or args.fill_price <= 0:
            parser.error("--fill-price must be positive for FILLED reconciliation.")
        if not args.broker_order_id and not intent.get("broker_order_id"):
            parser.error("A broker order ID is required for FILLED reconciliation.")

    # Record the operator assertion before changing the terminal state. If audit
    # persistence fails, the order intent remains unresolved and the halt stays.
    governance._log_audit(
        {
            "symbol": intent["symbol"],
            "side": intent["side"],
            "qty": intent["qty"],
            "price": args.fill_price,
        },
        "MANUAL_RECONCILIATION",
        [
            f"operator={args.operator}",
            f"reference={args.reconciliation_reference}",
            f"status={args.status}",
            f"client_order_id={args.client_order_id}",
        ],
    )

    if args.status == "FILLED" and not governance.has_trade_record(args.client_order_id):
        governance.record_trade({
            "symbol": intent["symbol"],
            "side": intent["side"],
            "qty": intent["qty"],
            "price": args.fill_price,
            "order_type": intent["order_type"],
            "strategy": intent.get("strategy"),
            "status": "FILLED",
            "order_id": args.broker_order_id or intent.get("broker_order_id"),
            "client_order_id": args.client_order_id,
            "intent_id": args.client_order_id,
        })

    updated = governance.update_order_intent(
        args.client_order_id,
        args.status,
        broker_order_id=args.broker_order_id,
        operator=args.operator,
        reconciliation_reference=args.reconciliation_reference,
    )
    print(json.dumps(updated, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
