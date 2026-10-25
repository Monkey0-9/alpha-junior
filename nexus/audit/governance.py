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
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS audit_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT    NOT NULL,
            symbol    TEXT    NOT NULL,
            side      TEXT    NOT NULL,
            qty       REAL,
            price     REAL,
            status    TEXT    NOT NULL,
            details   TEXT
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
            status      TEXT    NOT NULL
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
        self.audit_log.append(entry)

        # Persist to SQLite
        try:
            self._db.execute(
                "INSERT INTO audit_log "
                "(timestamp, symbol, side, qty, price, status, details) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entry["timestamp"],
                    request["symbol"],
                    request["side"],
                    request.get("qty"),
                    request.get("price"),
                    status,
                    json.dumps(entry["details"]),
                ),
            )
            self._db.commit()
        except sqlite3.Error as exc:
            logger.critical("Audit DB write failed; the trading decision must not proceed: %s", exc)
            raise RuntimeError("Unable to durably persist the governance decision.") from exc

        msg = (
            f"AUDIT: {status} - {request['side']} "
            f"{request['symbol']} - {entry['details']}"
        )
        logger.info(msg)

    def record_trade(self, trade: Dict[str, Any]) -> None:
        """Persist a completed trade to the history table."""
        try:
            self._db.execute(
                "INSERT INTO trade_history "
                "(timestamp, symbol, side, qty, price, "
                "order_type, strategy, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.datetime.now().isoformat(),
                    trade.get("symbol"),
                    trade.get("side"),
                    trade.get("qty"),
                    trade.get("price"),
                    trade.get("order_type"),
                    trade.get("strategy"),
                    trade.get("status", "EXECUTED"),
                ),
            )
            self._db.commit()
        except sqlite3.Error as exc:
            logger.critical(
                "Trade history write failed after broker submission; manual reconciliation is required: %s",
                exc,
            )
            raise RuntimeError("Unable to durably persist submitted trade lineage.") from exc
