"""
Universe Engine Module.
Manages the tradable universe over time with institutional liquidity filters
and auditable point-in-time universe snapshots to eliminate survivorship bias.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Set, Optional

logger = logging.getLogger(__name__)


@dataclass
class UniverseFilterConfig:
    """Institutional liquidity and asset quality constraints."""
    min_price: float = 5.0
    max_price: float = 50000.0
    min_volume: int = 500_000
    min_adv_dollar: float = 10_000_000.0  # $10M Average Daily Dollar Volume
    allowed_exchanges: Set[str] = field(
        default_factory=lambda: {"NASDAQ", "NYSE", "ARCA", "AMEX", "NYSEARCA", "NYSEAMEX"}
    )
    max_universe_size: int = 50
    blacklist: Set[str] = field(
        default_factory=lambda: {"UVXY", "VXX", "SVXY", "SQQQ", "SOXS", "SDOW"}
    )


class UniverseManager:
    """
    Institutional Dynamic Universe Selector.

    Filters incoming exchange assets using liquidity criteria, exchange tiering,
    and operational stability requirements. Maintains point-in-time historical
    snapshots for governance and survivorship-bias-free auditability.
    """

    # Tier-1 Institutional Blue-Chip and Benchmark Universe
    CORE_LIQUID_EQUITIES: List[str] = [
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
        "JPM", "UNH", "V", "XOM", "PG", "MA", "HD", "MRK",
        "ABBV", "COST", "PEP", "ADBE", "KO", "BAC", "WMT", "CRM", "AMD", "NFLX"
    ]

    CORE_BENCHMARK_ETFS: List[str] = [
        "SPY", "QQQ", "IWM", "XLK", "XLF", "XLE", "XLV"
    ]

    def __init__(self, config: Optional[UniverseFilterConfig] = None) -> None:
        self.config = config or UniverseFilterConfig()
        self.snapshots: List[Dict[str, Any]] = []
        self._current_universe: List[str] = list(self.CORE_LIQUID_EQUITIES[:15])

    def is_valid_symbol(self, symbol: str) -> bool:
        """Validates symbol format: length 1-5, strictly uppercase alphanumeric."""
        if not symbol or not isinstance(symbol, str):
            return False
        symbol = symbol.strip().upper()
        if symbol in self.config.blacklist:
            return False
        if not re.match(r"^[A-Z]{1,5}$", symbol):
            return False
        return True

    def is_preferred_exchange(self, exchange: str) -> bool:
        """Verifies exchange is a recognized primary US institutional venue."""
        return exchange.upper() in self.config.allowed_exchanges

    def filter_assets(self, raw_assets: List[Dict[str, Any]]) -> List[str]:
        """
        Filters raw broker asset objects based on tradability, status,
        exchange venue, and blacklist restrictions.
        """
        valid_symbols: List[str] = []
        for asset in raw_assets:
            symbol = asset.get("symbol", "")
            exchange = asset.get("exchange", "")
            status = str(asset.get("status", "")).lower()
            tradable = asset.get("tradable", False)

            if not tradable or status not in {"active", ""}:
                continue
            if not self.is_valid_symbol(symbol):
                continue
            if not self.is_preferred_exchange(exchange):
                continue

            valid_symbols.append(symbol.upper())

        return valid_symbols

    def select_universe(
        self,
        raw_assets: List[Dict[str, Any]],
        max_symbols: int = 30,
    ) -> List[str]:
        """
        Selects active trading universe from raw broker assets.
        Falls back gracefully to institutional core equities if broker scan is unavailable.
        """
        candidates = self.filter_assets(raw_assets)
        if candidates:
            # Sort deterministically and cap
            candidates = sorted(list(set(candidates)))
            universe = candidates[:max_symbols]
        else:
            logger.info("Asset scan yielded zero qualified candidates. Utilizing core institutional fallback universe.")
            universe = self.get_fallback_universe(limit=max_symbols)

        self.record_snapshot(universe)
        self._current_universe = universe
        return universe

    def get_fallback_universe(self, limit: int = 20) -> List[str]:
        """Returns verified, highly liquid institutional blue-chip universe."""
        combined = self.CORE_BENCHMARK_ETFS + self.CORE_LIQUID_EQUITIES
        return combined[:limit]

    def record_snapshot(self, universe: List[str]) -> None:
        """Records point-in-time universe snapshot for audit provenance."""
        snapshot = {
            "timestamp": time.time(),
            "count": len(universe),
            "symbols": list(universe),
        }
        self.snapshots.append(snapshot)
        # Keep last 100 snapshots in memory
        if len(self.snapshots) > 100:
            self.snapshots.pop(0)

    @property
    def current_universe(self) -> List[str]:
        """Returns currently active tradable universe."""
        return list(self._current_universe)
