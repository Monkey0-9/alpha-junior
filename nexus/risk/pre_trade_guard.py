"""Mandatory, fail-closed pre-trade risk and close-position guard."""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from nexus.audit.governance import GovernanceEngine
from nexus.execution.alpaca import AlpacaClient
from nexus.utils.config import Config
from nexus.utils.polyglot_bridge import PolyglotBridge

logger = logging.getLogger(__name__)
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
_ORDER_TYPES = {"market", "limit", "stop", "stop_limit", "trailing_stop"}


class PreTradeRiskGuard:
    """Single mandatory boundary for all risk-increasing order submissions.

    Broker account, holdings, and live market marks are re-read here. Caller-
    supplied portfolio snapshots are never authoritative for live sizing.
    """

    _instance: Optional["PreTradeRiskGuard"] = None

    def __init__(self, governance: Optional[GovernanceEngine] = None):
        self.governance = governance or GovernanceEngine(
            single_position_limit=Config.MAX_POSITION_SIZE,
            max_drawdown_limit=Config.MAX_DRAWDOWN,
        )

    @classmethod
    def get_instance(cls) -> "PreTradeRiskGuard":
        if cls._instance is None:
            cls._instance = PreTradeRiskGuard()
        return cls._instance

    @staticmethod
    def _reject(message: str, **extra: Any) -> Dict[str, Any]:
        return {"success": False, "status": "rejected", "error": message, **extra}

    @staticmethod
    def _finite_positive(value: Any, field: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be numeric.") from exc
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f"{field} must be finite and positive.")
        return number

    @staticmethod
    def _broker_position_quantity(position: Dict[str, Any]) -> float:
        quantity = float(position.get("qty", 0.0))
        if not math.isfinite(quantity):
            raise ValueError("Broker position contains a non-finite quantity.")
        side = str(position.get("side", "long")).lower()
        return -abs(quantity) if side == "short" else abs(quantity)

    async def _verified_state(
        self,
        client: AlpacaClient,
        supplied_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not client.enabled:
            raise RuntimeError("Broker execution is disabled.")
        if client.simulated and not Config.ALLOW_SIMULATION_FALLBACK:
            raise RuntimeError("Simulation is disabled by configuration.")

        account = await client.get_account()
        if not isinstance(account, dict):
            raise RuntimeError("Broker returned a malformed account response.")

        if not client.simulated:
            if (
                account.get("status") != "ACTIVE"
                or account.get("simulated")
                or account.get("enabled") is False
            ):
                raise RuntimeError("Broker account is not verified ACTIVE live state.")

        equity_raw = account.get("equity", account.get("portfolio_value"))
        last_equity_raw = account.get("last_equity", equity_raw)
        equity = self._finite_positive(equity_raw, "Broker equity")
        last_equity = self._finite_positive(last_equity_raw, "Broker last_equity")

        positions = await client.get_positions()
        if not isinstance(positions, list):
            raise RuntimeError("Broker positions response is not a list.")

        normalized_positions = []
        for position in positions:
            if not isinstance(position, dict) or not position.get("symbol"):
                raise RuntimeError("Broker returned malformed position data.")
            qty = self._broker_position_quantity(position)
            normalized_positions.append({**position, "qty_signed": qty})

        if supplied_state is not None and not client.simulated:
            try:
                supplied_equity = float(supplied_state.get("total_value", 0.0))
            except (TypeError, ValueError):
                supplied_equity = 0.0
            if (
                not math.isfinite(supplied_equity)
                or supplied_equity <= 0
                or abs(supplied_equity - equity) / equity > 0.01
            ):
                raise RuntimeError(
                    "Caller portfolio snapshot differs from live broker equity by more than 1%."
                )

        drawdown = max(0.0, (last_equity - equity) / last_equity)
        return {
            "total_value": equity,
            "drawdown": drawdown,
            "positions": normalized_positions,
            "account": account,
        }

    async def _verified_live_mark(self, client: AlpacaClient, symbol: str) -> float:
        bars = await client.get_bars(symbol, timeframe="1Min", limit=1)
        if not bars:
            raise RuntimeError("No current broker market mark is available.")

        latest = bars[-1]
        timestamp = latest.get("t") or latest.get("timestamp") or latest.get("time")
        if isinstance(timestamp, datetime):
            bar_time = timestamp
        elif isinstance(timestamp, str) and timestamp.strip():
            timestamp_text = timestamp.strip()
            if timestamp_text.endswith("Z"):
                timestamp_text = timestamp_text[:-1] + "+00:00"
            bar_time = datetime.fromisoformat(timestamp_text)
        else:
            raise RuntimeError("Market bar has no timestamp.")
        if bar_time.tzinfo is None:
            raise RuntimeError("Market bar timestamp has no timezone.")

        age_seconds = (
            datetime.now(timezone.utc) - bar_time.astimezone(timezone.utc)
        ).total_seconds()
        max_age = max(1, int(Config.MAX_MARKET_DATA_AGE_SECONDS))
        if age_seconds < -60 or age_seconds > max_age:
            raise RuntimeError(
                f"Market data is stale or future-dated (age={age_seconds:.1f}s, limit={max_age}s)."
            )
        return self._finite_positive(
            latest.get("close", latest.get("c")), "Current market mark"
        )

    async def validate_and_submit(
        self,
        client: AlpacaClient,
        order_request: Dict[str, Any],
        portfolio_state: Optional[Dict[str, Any]] = None,
        current_qty: float = 0.0,
    ) -> Dict[str, Any]:
        """Validate, audit, and submit one order; unknown state always rejects."""
        raw_symbol = order_request.get("symbol")
        symbol = raw_symbol.strip().upper() if isinstance(raw_symbol, str) else ""
        if not _SYMBOL_RE.fullmatch(symbol):
            return self._reject("Invalid order symbol.")

        side = str(order_request.get("side", "")).strip().lower()
        if side not in {"buy", "sell"}:
            return self._reject("Order side must be buy or sell.")

        try:
            qty = self._finite_positive(order_request.get("qty"), "Order quantity")
        except ValueError as exc:
            return self._reject(str(exc))

        order_type = str(order_request.get("order_type", "market")).lower()
        if order_type not in _ORDER_TYPES:
            return self._reject("Unsupported order type.")

        prices: Dict[str, float] = {}
        for field in ("limit_price", "stop_price", "trail_price", "trail_percent"):
            value = order_request.get(field)
            if value is not None:
                try:
                    prices[field] = self._finite_positive(value, field)
                except ValueError as exc:
                    return self._reject(str(exc))

        if order_type in {"limit", "stop_limit"} and "limit_price" not in prices:
            return self._reject("Limit orders require a valid limit_price.")
        if order_type in {"stop", "stop_limit"} and "stop_price" not in prices:
            return self._reject("Stop orders require a valid stop_price.")
        if order_type == "trailing_stop":
            has_trail_price = "trail_price" in prices
            has_trail_percent = "trail_percent" in prices
            if has_trail_price == has_trail_percent:
                return self._reject(
                    "Trailing stops require exactly one of trail_price or trail_percent."
                )

        try:
            state = await self._verified_state(client, portfolio_state)
            if client.simulated:
                simulation_bars = await client.get_bars(symbol, timeframe="1Min", limit=1)
                if not simulation_bars:
                    raise RuntimeError("Simulation market mark is unavailable.")
                mark = self._finite_positive(
                    simulation_bars[-1].get("close", simulation_bars[-1].get("c")),
                    "Simulation market mark",
                )
            else:
                mark = await self._verified_live_mark(client, symbol)
        except Exception as exc:
            logger.error("Pre-trade state verification failed for %s: %s", symbol, exc)
            return self._reject(
                "Unable to verify broker account, positions, or fresh market data; order rejected.",
                trading_halted=not client.simulated,
            )

        signed_current_qty = 0.0
        for position in state["positions"]:
            if str(position.get("symbol", "")).upper() == symbol:
                signed_current_qty = float(position["qty_signed"])
                break

        if not client.simulated:
            try:
                supplied_qty = float(current_qty)
            except (TypeError, ValueError):
                return self._reject("Caller position quantity is invalid.")
            if not math.isfinite(supplied_qty):
                return self._reject("Caller position quantity is non-finite.")
            # A supplied zero is not an authoritative assertion; compare only
            # when the caller actually supplies a non-zero holding.
            if abs(supplied_qty) > 1e-9 and abs(abs(supplied_qty) - abs(signed_current_qty)) > 1e-6:
                return self._reject(
                    "Caller position quantity differs from current broker holdings; refresh and retry.",
                    reconciliation_required=True,
                )

        trade_compliance_req = {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "price": mark,
            "order_type": order_type,
            "limit_price": prices.get("limit_price"),
            "stop_price": prices.get("stop_price"),
            "trail_price": prices.get("trail_price"),
            "trail_percent": prices.get("trail_percent"),
            "strategy": order_request.get("strategy", "manual"),
        }

        validation = PolyglotBridge.validate_order_zig(trade_compliance_req)
        if validation.get("valid") is not True:
            return self._reject(
                f"Order validation failed: {validation.get('error', 'INVALID_ORDER')}"
            )

        try:
            approved, violations = self.governance.check_compliance(
                trade_compliance_req,
                state,
                current_qty=signed_current_qty,
            )
        except Exception as exc:
            logger.critical("Governance approval could not be durably audited: %s", exc)
            client.enabled = False
            return self._reject(
                "Governance audit persistence failed; order rejected and execution halted.",
                trading_halted=True,
            )

        if not approved:
            return self._reject(
                "Risk limits violated.",
                violations=violations,
            )

        result = await client.submit_order(
            symbol=symbol,
            qty=qty,
            side=side,
            asset_class=order_request.get("asset_class", "equity"),
            order_type=order_type,
            time_in_force=order_request.get("time_in_force", "day"),
            limit_price=prices.get("limit_price"),
            stop_price=prices.get("stop_price"),
            trail_price=prices.get("trail_price"),
            trail_percent=prices.get("trail_percent"),
            client_order_id=order_request.get("client_order_id"),
            strategy=order_request.get("strategy"),
            extended_hours=bool(order_request.get("extended_hours", False)),
        )

        if result.get("status") in {"SUBMISSION_UNKNOWN", "UNAUTHORIZED"}:
            return result

        if result.get("success") or result.get("order_id") or result.get("id"):
            try:
                self.governance.record_trade(
                    {
                        "symbol": symbol,
                        "side": side,
                        "qty": qty,
                        "price": mark,
                        "order_type": order_type,
                        "strategy": order_request.get("strategy", "manual"),
                        "status": result.get("status", "SUBMITTED"),
                    }
                )
            except Exception as exc:
                logger.critical(
                    "Broker may have accepted %s but trade lineage persistence failed: %s",
                    symbol,
                    exc,
                )
                client.enabled = False
                return {
                    "success": True,
                    "status": "SUBMITTED_AUDIT_FAILURE",
                    "order_id": result.get("order_id") or result.get("id"),
                    "error": "Order may be active at the broker; lineage was not persisted. Do not retry. Reconcile broker orders and positions.",
                    "reconciliation_required": True,
                    "trading_halted": True,
                }
        return result

    async def validate_and_close_position(
        self, client: AlpacaClient, symbol: str
    ) -> Dict[str, Any]:
        """Audit and close an existing position through the same controlled boundary."""
        symbol = symbol.strip().upper()
        if not _SYMBOL_RE.fullmatch(symbol):
            return self._reject("Invalid position symbol.")
        try:
            state = await self._verified_state(client)
        except Exception as exc:
            logger.error("Close-position state verification failed for %s: %s", symbol, exc)
            return self._reject(
                "Unable to verify broker account and holdings; close request rejected.",
                reconciliation_required=True,
            )

        position = next(
            (
                item for item in state["positions"]
                if str(item.get("symbol", "")).upper() == symbol
            ),
            None,
        )
        if position is None:
            return self._reject(f"No open position found for {symbol}.")

        signed_qty = float(position["qty_signed"])
        qty = abs(signed_qty)
        side = "buy" if signed_qty < 0 else "sell"
        try:
            self.governance._log_audit(
                {
                    "symbol": symbol,
                    "qty": qty,
                    "side": side,
                    "price": float(position.get("current_price", 0.0) or 0.0),
                },
                "CLOSE_REQUESTED",
            )
        except Exception as exc:
            logger.critical("Close request audit failed; not dispatching: %s", exc)
            client.enabled = False
            return self._reject(
                "Close audit persistence failed; request not dispatched and execution halted.",
                trading_halted=True,
            )

        result = await client.close_position(symbol)
        if not result.get("success"):
            if result.get("reconciliation_required") or result.get("trading_halted"):
                return result
            return self._reject(
                result.get("error", "Broker did not confirm position close."),
                broker_status=result.get("status"),
            )

        try:
            self.governance.record_trade(
                {
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "price": position.get("current_price"),
                    "order_type": "market",
                    "strategy": "position_close",
                    "status": result.get("status", "CLOSE_REQUEST_ACCEPTED"),
                }
            )
        except Exception as exc:
            logger.critical("Close may have been accepted but audit persistence failed: %s", exc)
            client.enabled = False
            return {
                "success": True,
                "status": "CLOSE_SUBMITTED_AUDIT_FAILURE",
                "symbol": symbol,
                "error": "Position close may be active at the broker; audit persistence failed. Reconcile before retry.",
                "reconciliation_required": True,
                "trading_halted": True,
            }
        return result
