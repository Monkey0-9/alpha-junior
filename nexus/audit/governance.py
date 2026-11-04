import datetime
import json
import hashlib
import logging
import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any, Set

logger = logging.getLogger(__name__)


# Default DB path — data/ directory in project root
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DB_DIR = _PROJECT_ROOT / "data"
_DB_PATH = _DB_DIR / "nexus_audit.db"


def _get_db() -> sqlite3.Connection:
    """Open or create the audit database."""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("""CREATE TABLE IF NOT EXISTS audit_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT    NOT NULL,
            symbol    TEXT    NOT NULL,
            side      TEXT    NOT NULL,
            qty       REAL,
            price     REAL,
            status    TEXT    NOT NULL,
            details   TEXT,
            client_order_id TEXT,
            order_type TEXT,
            strategy TEXT
        )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS trade_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            symbol      TEXT    NOT NULL,
            side        TEXT    NOT NULL,
            qty         REAL,
            price       REAL,
            order_type  TEXT,
            strategy    TEXT,
            status      TEXT    NOT NULL,
            order_id TEXT,
            client_order_id TEXT,
            intent_id TEXT
        )""")

    # Additive schema migrations for installations created by earlier versions.
    migrations = {
        "audit_log": {
            "client_order_id": "TEXT",
            "order_type": "TEXT",
            "strategy": "TEXT",
        },
        "trade_history": {
            "order_id": "TEXT",
            "client_order_id": "TEXT",
            "intent_id": "TEXT",
        },
    }
    for table, columns in migrations.items():
        existing = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for column, sql_type in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_history_client_order_id "
        "ON trade_history(client_order_id) WHERE client_order_id IS NOT NULL"
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS order_intents (
        client_order_id TEXT PRIMARY KEY,
        intent_fingerprint TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        qty REAL NOT NULL,
        order_type TEXT NOT NULL,
        strategy TEXT,
        status TEXT NOT NULL,
        broker_order_id TEXT,
        error TEXT,
        reconciliation_reference TEXT,
        reconciled_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    conn.commit()
    return conn


class GovernanceEngine:
    """Institutional-grade trading compliance engine.

    Enforces position concentration limits, drawdown protection,
    and a configurable symbol blacklist.  All audit decisions are
    persisted to SQLite for post-session analysis and regulatory
    compliance.
    """

    def __init__(
        self,
        single_position_limit: Optional[float] = None,
        max_drawdown_limit: Optional[float] = None,
    ):
        # Allow overriding limits via environment variables for runtime tuning
        try:
            env_pos = os.getenv("NEXUS_MAX_POSITION_SIZE")
            if env_pos is not None and env_pos.strip() != "":
                self.single_position_limit = float(env_pos)
            elif single_position_limit is not None:
                self.single_position_limit = float(single_position_limit)
            else:
                self.single_position_limit = 0.05
        except ValueError:
            self.single_position_limit = 0.05

        try:
            env_dd = os.getenv("NEXUS_MAX_DRAWDOWN")
            if env_dd is not None and env_dd.strip() != "":
                self.max_drawdown_limit = float(env_dd)
            elif max_drawdown_limit is not None:
                self.max_drawdown_limit = float(max_drawdown_limit)
            else:
                self.max_drawdown_limit = 0.15
        except ValueError:
            self.max_drawdown_limit = 0.15
        self.audit_log: List[Dict[str, Any]] = []

        # Configurable blacklist from env or file
        self._blacklist = self._load_blacklist()

        # Initialise persistent storage
        self._db = _get_db()

    @staticmethod
    def _load_blacklist() -> Set[str]:
        """Load blacklist from NEXUS_BLACKLIST env var or file."""
        # 1. Check env var (comma-separated symbols)
        env_val = os.getenv("NEXUS_BLACKLIST", "")
        if env_val.strip():
            return {s.strip().upper() for s in env_val.split(",") if s.strip()}

        # 2. Check file
        bl_path = Path("config/blacklist.txt")
        if bl_path.exists():
            return {
                line.strip().upper()
                for line in bl_path.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            }

        # 3. Empty blacklist — no placeholder symbols
        return set()

    def check_compliance(
        self,
        trade_request: Dict[str, Any],
        portfolio_state: Dict[str, Any],
        current_qty: float = 0.0,
    ) -> Tuple[bool, List[str]]:
        """Perform compliance checks against risk limits."""
        symbol = trade_request["symbol"]
        qty = trade_request["qty"]
        price = trade_request["price"]
        side = trade_request["side"]

        delta = qty if side == "buy" else -qty
        new_qty = current_qty + delta

        trade_value = abs(new_qty) * price
        total_value = portfolio_state["total_value"]
        drawdown = portfolio_state["drawdown"]

        violations: List[str] = []

        # 1. Concentration Check
        if total_value > 0:
            pos_pct = trade_value / total_value
            if pos_pct > self.single_position_limit:
                violations.append(
                    f"POSITION_CONCENTRATION: "
                    f"{pos_pct:.2%} > "
                    f"{self.single_position_limit:.1%}"
                )

        # 2. Drawdown Protection
        if drawdown > self.max_drawdown_limit:
            violations.append(
                f"DRAWDOWN_BREACH: "
                f"{drawdown:.1%} > "
                f"{self.max_drawdown_limit:.1%}"
            )

        # 3. Symbol Blacklist
        if symbol in self._blacklist:
            violations.append(f"BLACKLIST_SYMBOL: {symbol} is restricted")

        if not violations:
            self._log_audit(trade_request, "APPROVED")
            return True, []

        self._log_audit(trade_request, "REJECTED", violations)
        return False, violations

    def _log_audit(
        self,
        request: Dict[str, Any],
        status: str,
        details: Optional[List[str]] = None,
    ) -> None:
        entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "symbol": request["symbol"],
            "side": request["side"],
            "status": status,
            "details": details or "Compliance Passed",
        }
        # Persist first. An order must never be approved on an in-memory-only audit.
        try:
            self._db.execute(
                "INSERT INTO audit_log "
                "(timestamp, symbol, side, qty, price, status, details, "
                "client_order_id, order_type, strategy) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry["timestamp"],
                    request["symbol"],
                    request["side"],
                    request.get("qty"),
                    request.get("price"),
                    status,
                    json.dumps(entry["details"]),
                    request.get("client_order_id"),
                    request.get("order_type"),
                    request.get("strategy"),
                ),
            )
            self._db.commit()
        except sqlite3.Error as exc:
            try:
                self._db.rollback()
            except sqlite3.Error:
                pass
            logger.critical("Audit DB write failed; approval is unavailable: %s", exc)
            raise RuntimeError("Unable to durably persist governance audit.") from exc

        self.audit_log.append(entry)

        msg = (
            f"AUDIT: {status} - {request['side']} "
            f"{request['symbol']} - {entry['details']}"
        )
        logger.info(msg)

    @staticmethod
    def _intent_fingerprint(request: Dict[str, Any]) -> str:
        """Hash business intent fields; live market mark is not an order parameter."""
        payload = {
            "symbol": str(request.get("symbol", "")).upper(),
            "side": str(request.get("side", "")).lower(),
            "qty": float(request.get("qty", 0.0)),
            "order_type": str(request.get("order_type", "market")).lower(),
            "limit_price": request.get("limit_price"),
            "stop_price": request.get("stop_price"),
            "trail_price": request.get("trail_price"),
            "trail_percent": request.get("trail_percent"),
            "time_in_force": request.get("time_in_force", "day"),
            "asset_class": request.get("asset_class", "equity"),
            "strategy": request.get("strategy"),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def get_order_intent(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        row = self._db.execute(
            "SELECT client_order_id, intent_fingerprint, symbol, side, qty, order_type, "
            "strategy, status, broker_order_id, error, reconciliation_reference, "
            "reconciled_by, created_at, updated_at FROM order_intents WHERE client_order_id = ?",
            (client_order_id,),
        ).fetchone()
        if row is None:
            return None
        keys = (
            "client_order_id", "intent_fingerprint", "symbol", "side", "qty",
            "order_type", "strategy", "status", "broker_order_id", "error",
            "reconciliation_reference", "reconciled_by", "created_at", "updated_at",
        )
        return dict(zip(keys, row, strict=True))

    def get_order_intent_by_broker_order_id(
        self, broker_order_id: str
    ) -> Optional[Dict[str, Any]]:
        row = self._db.execute(
            "SELECT client_order_id FROM order_intents WHERE broker_order_id = ?",
            (broker_order_id,),
        ).fetchone()
        return self.get_order_intent(row[0]) if row else None

    def prepare_order_intent(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Atomically reserve an intent before dispatch; concurrent duplicate keys cannot both submit."""
        client_order_id = request.get("client_order_id")
        if not isinstance(client_order_id, str) or not client_order_id:
            raise ValueError("client_order_id is required for an order intent.")
        fingerprint = self._intent_fingerprint(request)
        try:
            self._db.execute("BEGIN IMMEDIATE")
            existing = self.get_order_intent(client_order_id)
            if existing:
                if existing["intent_fingerprint"] != fingerprint:
                    raise RuntimeError(
                        "Idempotency key was already used for a different order intent."
                    )
                self._db.commit()
                existing["_created"] = False
                return existing

            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self._db.execute(
                "INSERT INTO order_intents "
                "(client_order_id, intent_fingerprint, symbol, side, qty, order_type, "
                "strategy, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    client_order_id,
                    fingerprint,
                    request["symbol"],
                    request["side"],
                    float(request["qty"]),
                    request.get("order_type", "market"),
                    request.get("strategy"),
                    "APPROVED",
                    now,
                    now,
                ),
            )
            self._db.commit()
        except Exception:
            try:
                self._db.rollback()
            except sqlite3.Error:
                pass
            raise

        result = self.get_order_intent(client_order_id)
        if result is None:
            raise RuntimeError("Persisted order intent could not be reloaded.")
        result["_created"] = True
        return result

    def has_trade_record(self, client_order_id: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM trade_history WHERE client_order_id = ? LIMIT 1",
            (client_order_id,),
        ).fetchone()
        return row is not None

    def update_order_intent(
        self,
        client_order_id: str,
        status: str,
        *,
        broker_order_id: Optional[str] = None,
        error: Optional[str] = None,
        operator: Optional[str] = None,
        reconciliation_reference: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update an intent state with explicit terminal/reconciliation metadata."""
        allowed = {
            "APPROVED",
            "SUBMITTED",
            "PARTIALLY_FILLED",
            "FILLED",
            "CANCELLED",
            "EXPIRED",
            "REJECTED",
            "SUBMISSION_UNKNOWN",
            "RECONCILIATION_REQUIRED",
        }
        new_status = status.upper()
        if new_status not in allowed:
            raise ValueError(f"Unsupported order-intent status: {status}")
        existing = self.get_order_intent(client_order_id)
        if existing is None:
            raise KeyError(f"Unknown client_order_id: {client_order_id}")

        terminal = {"FILLED", "CANCELLED", "EXPIRED", "REJECTED"}
        current_status = existing["status"]
        if current_status in terminal and current_status != new_status:
            raise RuntimeError(
                f"Cannot change terminal order intent {current_status} to {new_status}."
            )

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            self._db.execute(
                "UPDATE order_intents SET status = ?, broker_order_id = COALESCE(?, broker_order_id), "
                "error = ?, reconciled_by = COALESCE(?, reconciled_by), "
                "reconciliation_reference = COALESCE(?, reconciliation_reference), updated_at = ? "
                "WHERE client_order_id = ?",
                (
                    new_status,
                    broker_order_id,
                    error,
                    operator,
                    reconciliation_reference,
                    now,
                    client_order_id,
                ),
            )
            self._db.commit()
        except sqlite3.Error as exc:
            try:
                self._db.rollback()
            except sqlite3.Error:
                pass
            logger.critical("Order intent update failed for %s: %s", client_order_id, exc)
            raise RuntimeError("Unable to durably update order intent.") from exc

        result = self.get_order_intent(client_order_id)
        if result is None:
            raise RuntimeError("Updated order intent could not be reloaded.")
        return result

    def list_unresolved_order_intents(self) -> List[Dict[str, Any]]:
        """Return intents that can represent a crash between approval and final ledger write."""
        rows = self._db.execute(
            "SELECT i.client_order_id FROM order_intents i "
            "LEFT JOIN trade_history t ON t.client_order_id = i.client_order_id "
            "WHERE i.status IN ('APPROVED', 'SUBMISSION_UNKNOWN', 'RECONCILIATION_REQUIRED', 'PARTIALLY_FILLED') "
            "OR (i.status IN ('SUBMITTED', 'FILLED') AND t.id IS NULL) "
            "ORDER BY i.created_at"
        ).fetchall()
        return [
            intent
            for row in rows
            if (intent := self.get_order_intent(row[0])) is not None
        ]

    def record_trade(self, trade: Dict[str, Any]) -> None:
        """Persist broker order lineage; identical idempotency IDs are safe to replay."""
        client_order_id = trade.get("client_order_id")
        try:
            if client_order_id:
                existing = self._db.execute(
                    "SELECT order_id, symbol, side, qty, price FROM trade_history "
                    "WHERE client_order_id = ?",
                    (client_order_id,),
                ).fetchone()
                if existing:
                    _, existing_symbol, existing_side, existing_qty, existing_price = existing
                    identical = (
                        existing_symbol == trade.get("symbol")
                        and existing_side == trade.get("side")
                        and abs(float(existing_qty or 0.0) - float(trade.get("qty") or 0.0)) < 1e-9
                        and abs(float(existing_price or 0.0) - float(trade.get("price") or 0.0)) < 1e-6
                    )
                    if not identical:
                        raise RuntimeError(
                            "Client order ID already exists with a different trade intent."
                        )
                    return

            self._db.execute(
                "INSERT INTO trade_history "
                "(timestamp, symbol, side, qty, price, order_type, strategy, status, "
                "order_id, client_order_id, intent_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.datetime.now().isoformat(),
                    trade.get("symbol"),
                    trade.get("side"),
                    trade.get("qty"),
                    trade.get("price"),
                    trade.get("order_type"),
                    trade.get("strategy"),
                    trade.get("status", "SUBMITTED"),
                    trade.get("order_id"),
                    client_order_id,
                    trade.get("intent_id"),
                ),
            )
            self._db.commit()
        except sqlite3.Error as exc:
            try:
                self._db.rollback()
            except sqlite3.Error:
                pass
            logger.critical("Trade history persistence failed: %s", exc)
            raise RuntimeError("Unable to durably persist trade lineage.") from exc


