"""Operator-only reset of the execution circuit breaker after broker reconciliation."""
from __future__ import annotations

import argparse
import json

from nexus.execution.halt_state import ExecutionHaltState


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clear the persisted execution halt after manually reconciling broker orders and positions."
    )
    parser.add_argument("--operator", required=True, help="Operator identifier/name for the audit trail")
    parser.add_argument(
        "--reconciliation-reference",
        required=True,
        help="Broker export, support ticket, or incident reference proving reconciliation",
    )
    parser.add_argument(
        "--confirm-broker-reconciled",
        action="store_true",
        help="Explicitly confirm that open orders, fills, and positions were reconciled with the broker",
    )
    args = parser.parse_args()
    if not args.confirm_broker_reconciled:
        parser.error("Refusing to clear halt without --confirm-broker-reconciled")

    result = ExecutionHaltState.clear_after_reconciliation(
        operator=args.operator,
        reconciliation_reference=args.reconciliation_reference,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
