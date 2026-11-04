"""Operator-only reset after explicit broker and ledger reconciliation."""
from __future__ import annotations

import argparse
import json

from nexus.audit.governance import GovernanceEngine
from nexus.execution.halt_state import ExecutionHaltState


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Clear the persistent execution halt only after reconciling broker "
            "orders, fills, positions and local order-intent records."
        )
    )
    parser.add_argument("--operator", required=True)
    parser.add_argument("--reconciliation-reference", required=True)
    parser.add_argument(
        "--confirm-broker-reconciled",
        action="store_true",
        help="Confirm broker open orders, fills and positions were independently reconciled.",
    )
    parser.add_argument(
        "--confirm-no-unresolved-intents",
        action="store_true",
        help="Confirm the local order-intent ledger contains no unresolved order intents.",
    )
    args = parser.parse_args()

    if not args.confirm_broker_reconciled or not args.confirm_no_unresolved_intents:
        parser.error(
            "Both --confirm-broker-reconciled and --confirm-no-unresolved-intents are required."
        )

    governance = GovernanceEngine()
    unresolved = governance.list_unresolved_order_intents()
    if unresolved:
        print(json.dumps({
            "cleared": False,
            "reason": "Unresolved order intents remain in the local ledger.",
            "unresolved": unresolved,
        }, indent=2))
        return 2

    result = ExecutionHaltState.clear_after_reconciliation(
        operator=args.operator,
        reconciliation_reference=args.reconciliation_reference,
    )
    print(json.dumps({
        "cleared": True,
        "state": result,
        "next_step": "Restart the API and trading-engine processes. Their in-memory halt flags intentionally remain set until restart.",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
