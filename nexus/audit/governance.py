import datetime
import json
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


