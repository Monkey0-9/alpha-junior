"""
nexus/risk/pre_trade_guard.py — Centralized Mandatory Pre-Trade Risk Guard.

Enforces a single, non-bypassable pre-trade approval boundary for both the
Core Engine and API Router order endpoints. No order can reach the broker
without passing this unified gate.
"""

import logging
import math
from typing import Dict, Any, Optional
from nexus.audit.governance import GovernanceEngine
from nexus.utils.polyglot_bridge import PolyglotBridge
from nexus.utils.config import Config
from nexus.execution.alpaca import AlpacaClient

logger = logging.getLogger(__name__)


class PreTradeRiskGuard:
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

    async def validate_and_submit(
        self,
        client: AlpacaClient,
        order_request: Dict[str, Any],
        portfolio_state: Optional[Dict[str, Any]] = None,
        current_qty: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Single mandatory risk-approval boundary covering every order route.
        """
        symbol = str(order_request.get("symbol", "")).strip().upper()
        side = str(order_request.get("side", "buy")).strip().lower()
        try:
            qty = float(order_request.get("qty", 0.0))
        except (TypeError, ValueError):
            qty = float("nan")

        if not symbol or not symbol.replace(".", "").replace("-", "").isalnum():
            return {"success": False, "status": "rejected", "error": "Invalid order symbol."}
        if side not in {"buy", "sell"}:
            return {"success": False, "status": "rejected", "error": "Order side must be buy or sell."}
        if not math.isfinite(qty) or qty <= 0:
            return {"success": False, "status": "rejected", "error": "Order quantity must be finite and positive."}

        order_type = str(order_request.get("order_type", "market")).lower()
        if order_type not in {"market", "limit", "stop", "stop_limit", "trailing_stop"}:
            return {"success": False, "status": "rejected", "error": "Unsupported order type."}
        for field in ("limit_price", "stop_price", "trail_price", "trail_percent"):
            value = order_request.get(field)
            if value is not None:
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError):
                    numeric_value = float("nan")
                if not math.isfinite(numeric_value) or numeric_value <= 0:
                    return {"success": False, "status": "rejected", "error": f"{field} must be finite and positive."}
        if order_type in {"limit", "stop_limit"} and order_request.get("limit_price") is None:
            return {"success": False, "status": "rejected", "error": "Limit orders require limit_price."}
        if order_type in {"stop", "stop_limit"} and order_request.get("stop_price") is None:
            return {"success": False, "status": "rejected", "error": "Stop orders require stop_price."}

        # 1. State integrity check: Fail-closed on disabled broker
        if not client.enabled and not client.simulated:
            return {
                "success": False,
                "status": "rejected",
                "error": "Broker execution is disabled or unauthenticated. Pre-trade check failed.",
            }

        if client.simulated and not Config.ALLOW_SIMULATION_FALLBACK:
            return {
                "success": False,
                "status": "rejected",
                "error": "Broker is in SIMULATION mode but NEXUS_ALLOW_SIMULATION_FALLBACK is false. Pre-trade check failed.",
            }

        # 2. Estimate price if not provided
        price = order_request.get("price") or order_request.get("limit_price") or order_request.get("stop_price")
        try:
            price_value = float(price) if price is not None else 0.0
        except (TypeError, ValueError):
            price_value = 0.0
        if not math.isfinite(price_value) or price_value <= 0:
            bars = await client.get_bars(symbol, timeframe="1Min", limit=1)
            if not bars:
                return {
                    "success": False,
                    "status": "rejected",
                    "error": "No trustworthy current market price is available; order rejected.",
                }
            try:
                price_value = float(bars[-1].get("close", bars[-1].get("c", 0.0)))
            except (TypeError, ValueError, KeyError):
                price_value = 0.0
        if not math.isfinite(price_value) or price_value <= 0:
            return {
                "success": False,
                "status": "rejected",
                "error": "Market price is missing or invalid; order rejected.",
            }
        price = price_value

        trade_compliance_req = {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "price": float(price),
            "order_type": order_type,
            "limit_price": order_request.get("limit_price"),
            "stop_price": order_request.get("stop_price"),
            "trail_price": order_request.get("trail_price"),
            "trail_percent": order_request.get("trail_percent"),
            "strategy": order_request.get("strategy", "manual"),
        }

        # 3. Retrieve or construct current portfolio state
        if portfolio_state is None:
            try:
                acc = await client.get_account()
                if not client.simulated:
                    if not client.enabled or acc.get("status") != "ACTIVE" or acc.get("simulated"):
                        return {
                            "success": False,
                            "status": "rejected",
                            "error": "Broker account state is not verified ACTIVE; pre-trade checks fail closed.",
                        }
                equity_raw = acc.get("equity", acc.get("portfolio_value"))
                last_equity_raw = acc.get("last_equity", equity_raw)
                equity = float(equity_raw)
                last_equity = float(last_equity_raw)
                if not math.isfinite(equity) or equity <= 0 or not math.isfinite(last_equity) or last_equity <= 0:
                    raise ValueError("Broker returned invalid equity values.")
                dd = max(0.0, (last_equity - equity) / last_equity)
                positions = await client.get_positions()
                portfolio_state = {
                    "total_value": equity,
                    "drawdown": dd,
                    "positions": positions,
                }
            except Exception as e:
                logger.error("Unable to establish verified portfolio state; rejecting order: %s", e)
                return {
                    "success": False,
                    "status": "rejected",
                    "error": "Unable to verify broker account and positions; order rejected.",
                }

        try:
            total_value = float(portfolio_state.get("total_value", 0.0))
            drawdown = float(portfolio_state.get("drawdown", float("nan")))
        except (TypeError, ValueError):
            total_value, drawdown = 0.0, float("nan")
        if (
            not math.isfinite(total_value)
            or total_value <= 0
            or not math.isfinite(drawdown)
            or drawdown < 0
        ):
            return {
                "success": False,
                "status": "rejected",
                "error": "Portfolio equity/drawdown state is invalid; order rejected.",
            }

        positions = portfolio_state.get("positions")
        if not isinstance(positions, list):
            return {
                "success": False,
                "status": "rejected",
                "error": "Portfolio positions are unavailable; order rejected.",
            }
        for pos in positions:
            if isinstance(pos, dict) and str(pos.get("symbol", "")).upper() == symbol:
                try:
                    current_qty = float(pos.get("qty", current_qty))
                except (TypeError, ValueError):
                    return {
                        "success": False,
                        "status": "rejected",
                        "error": "Current position quantity is invalid; order rejected.",
                    }
                break

        # 4. Zig / Polyglot syntax & limit validation
        zig_res = PolyglotBridge.validate_order_zig(trade_compliance_req)
        if not zig_res.get("valid", True):
            err = zig_res.get("error", "ZIG_VALIDATION_FAILED")
            logger.error(f"[PRE-TRADE GUARD] Zig validation failed for {symbol}: {err}")
            return {
                "success": False,
                "status": "rejected",
                "error": f"Polyglot validation failed: {err}",
            }

        # 5. Governance checks (Concentration, Drawdown, Blacklist)
        approved, violations = self.governance.check_compliance(
            trade_compliance_req, portfolio_state, current_qty=current_qty
        )
        if not approved:
            logger.error(
                f"[PRE-TRADE GUARD] Governance compliance failed for {symbol}: {violations}"
            )
            return {
                "success": False,
                "status": "rejected",
                "error": f"Risk limits violated: {'; '.join(violations)}",
                "violations": violations,
            }

        # 6. Approved: Dispatch order through broker
        logger.info(f"[PRE-TRADE GUARD] Order APPROVED for {symbol} ({side} {qty})")
        res = await client.submit_order(
            symbol=symbol,
            qty=qty,
            side=side,
            asset_class=order_request.get("asset_class", "equity"),
            order_type=order_request.get("order_type", "market"),
            time_in_force=order_request.get("time_in_force", "day"),
            limit_price=order_request.get("limit_price"),
            stop_price=order_request.get("stop_price"),
            trail_price=order_request.get("trail_price"),
            trail_percent=order_request.get("trail_percent"),
            client_order_id=order_request.get("client_order_id"),
            strategy=order_request.get("strategy"),
            extended_hours=order_request.get("extended_hours", False),
        )

        if res.get("success") or res.get("order_id") or res.get("id"):
            self.governance.record_trade(
                {
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "price": float(price),
                    "order_type": order_request.get("order_type", "market"),
                    "strategy": order_request.get("strategy", "manual"),
                    "status": "SUBMITTED",
                }
            )

        return res
