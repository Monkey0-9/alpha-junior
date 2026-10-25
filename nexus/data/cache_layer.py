import os
import sqlite3
import time
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

DB_PATH = os.path.join("data_cache", "market_bars.db")


class MarketDataCache:
    """
    Persistent SQLite-backed local cache layer for market bar data.
    Prevents duplicate REST calls to Alpaca and eliminates 429 Rate Limits.
    """

    _instance: Optional["MarketDataCache"] = None
    _initialized: bool = False

    def __new__(cls, db_path: str = DB_PATH) -> "MarketDataCache":
        if cls._instance is None:
            cls._instance = super(MarketDataCache, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, db_path: str = DB_PATH):
        if self._initialized:
            return
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
        self._memory_cache: Dict[str, Dict[str, Any]] = {}
        self._memory_ttl = 10.0  # seconds
        self._initialized = True

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bars (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    PRIMARY KEY (symbol, timeframe, timestamp)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_symbol_tf_ts
                ON bars (symbol, timeframe, timestamp)
            """)
            conn.commit()

    def get_bars(
        self, symbol: str, timeframe: str, limit: int = 100, start: Optional[str] = None
    ) -> Optional[List[Dict[str, Any]]]:
        symbol = symbol.upper()
        cache_key = f"{symbol}_{timeframe}_{limit}_{start or ''}"
        now = time.monotonic()

        # Check memory cache
        if cache_key in self._memory_cache:
            entry = self._memory_cache[cache_key]
            if now - entry["ts"] < self._memory_ttl:
                return cast_bars(entry["data"])

        # Check SQLite DB
        with self._get_connection() as conn:
            if start:
                query = """
                    SELECT timestamp as t, open as o, high as h, low as l, close as c, volume as v
                    FROM bars
                    WHERE symbol = ? AND timeframe = ? AND timestamp >= ?
                    ORDER BY timestamp ASC
                    LIMIT ?
                """
                rows = conn.execute(query, (symbol, timeframe, start, limit)).fetchall()
            else:
                query = """
                    SELECT timestamp as t, open as o, high as h, low as l, close as c, volume as v
                    FROM (
                        SELECT timestamp, open, high, low, close, volume
                        FROM bars
                        WHERE symbol = ? AND timeframe = ?
                        ORDER BY timestamp DESC
                        LIMIT ?
                    ) ORDER BY timestamp ASC
                """
                rows = conn.execute(query, (symbol, timeframe, limit)).fetchall()

        if len(rows) >= limit or (limit > 50 and len(rows) >= int(limit * 0.8)):
            bars = [dict(r) for r in rows]
            self._memory_cache[cache_key] = {"ts": now, "data": bars}
            return bars

        return None

    def save_bars(
        self, symbol: str, timeframe: str, bars: List[Dict[str, Any]]
    ) -> None:
        if not bars:
            return
        symbol = symbol.upper()
        records = []
        for b in bars:
            ts = b.get("t") or b.get("timestamp")
            if not ts:
                continue
            o = float(b.get("o") or b.get("open") or 0.0)
            h = float(b.get("h") or b.get("high") or 0.0)
            low_val = float(b.get("l") or b.get("low") or 0.0)
            c = float(b.get("c") or b.get("close") or 0.0)
            v = float(b.get("v") or b.get("volume") or 0.0)
            records.append((symbol, timeframe, str(ts), o, h, low_val, c, v))

        if not records:
            return

        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO bars (symbol, timeframe, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                records,
            )
            conn.commit()

        # Invalidate memory cache for this symbol
        keys_to_del = [
            k for k in self._memory_cache if k.startswith(f"{symbol}_{timeframe}")
        ]
        for k in keys_to_del:
            self._memory_cache.pop(k, None)


def cast_bars(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [dict(item) for item in data if isinstance(item, dict)]
    return []
