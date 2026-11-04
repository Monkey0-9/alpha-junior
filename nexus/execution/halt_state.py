"""Persistent execution halt state for ambiguous broker/audit outcomes."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STATE_PATH = _PROJECT_ROOT / "data" / "execution_halt.json"
_LOCK = RLock()


class ExecutionHaltState:
    """Fail-safe persistent circuit breaker.

    A malformed state file is treated as halted. Clearing is an operator action
    and must be performed only after external broker reconciliation.
    """

    @staticmethod
    def _read() -> Optional[dict[str, Any]]:
        if not _STATE_PATH.exists():
            return None
        try:
            payload = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("halt-state root is not an object")
            return payload
        except Exception as exc:
            return {
                "halted": True,
                "reason": f"Execution halt state is unreadable: {exc}",
                "state_file": str(_STATE_PATH),
            }

    @classmethod
    def is_halted(cls) -> bool:
        with _LOCK:
            payload = cls._read()
            return bool(payload and payload.get("halted", True))

    @classmethod
    def get_state(cls) -> Optional[dict[str, Any]]:
        with _LOCK:
            return cls._read()

    @classmethod
    def _write_atomic(cls, payload: dict[str, Any]) -> None:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix="execution_halt_", suffix=".tmp", dir=str(_STATE_PATH.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, _STATE_PATH)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    @classmethod
    def trip(
        cls,
        reason: str,
        *,
        symbol: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> dict[str, Any]:
        payload = {
            "halted": True,
            "reason": str(reason),
            "symbol": symbol,
            "order_id": order_id,
            "triggered_at": datetime.now(timezone.utc).isoformat(),
            "state_file": str(_STATE_PATH),
        }
        with _LOCK:
            try:
                cls._write_atomic(payload)
            except Exception as exc:
                # Caller must still keep the in-process guard tripped.
                payload["persist_error"] = str(exc)
                raise RuntimeError(
                    "Execution halt could not be persisted; keep process halted and escalate."
                ) from exc
        return payload

    @classmethod
    def clear_after_reconciliation(
        cls, *, operator: str, reconciliation_reference: str
    ) -> dict[str, Any]:
        if not operator.strip() or not reconciliation_reference.strip():
            raise ValueError("Operator and reconciliation reference are required.")
        previous = cls.get_state()
        if previous and previous.get("halted"):
            payload = {
                "halted": False,
                "cleared_at": datetime.now(timezone.utc).isoformat(),
                "cleared_by": operator.strip(),
                "reconciliation_reference": reconciliation_reference.strip(),
                "previous_halt": previous,
            }
            with _LOCK:
                cls._write_atomic(payload)
            return payload
        return {
            "halted": False,
            "cleared_at": datetime.now(timezone.utc).isoformat(),
            "cleared_by": operator.strip(),
            "reconciliation_reference": reconciliation_reference.strip(),
            "previous_halt": None,
        }
