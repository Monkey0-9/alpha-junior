"""
nexus/risk/pre_trade_guard.py — Centralized Mandatory Pre-Trade Risk Guard.

Enforces a single, non-bypassable pre-trade approval boundary for both the
Core Engine and API Router order endpoints. No order can reach the broker
without passing this unified gate.
"""

import logging
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
        symbol = order_request.get("symbol", "").upper()
        qty = float(order_request.get("qty", 0.0))
        side = order_request.get("side", "buy").lower()

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
        price = order_request.get("price") or order_request.get("limit_price")
        if not price or float(price) <= 0:
            bars = await client.get_bars(symbol, timeframe="1Min", limit=1)
            price = float(bars[-1]["close"]) if bars else 100.0

        trade_compliance_req = {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "price": float(price),
            "order_type": order_request.get("order_type", "market"),
            "strategy": order_request.get("strategy", "manual"),
        }

        # 3. Retrieve or construct current portfolio state
        if portfolio_state is None:
            try:
                acc = await client.get_account()
                equity = float(acc.get("equity", Config.FALLBACK_EQUITY))
                last_equity = float(acc.get("last_equity", equity))
                dd = max(0.0, (last_equity - equity) / max(equity, 1.0))
                positions = await client.get_positions()
                portfolio_state = {
                    "total_value": equity,
                    "drawdown": dd,
                    "positions": positions,
                }
                # Find current_qty if not supplied
                for pos in positions:
                    if pos.get("symbol") == symbol:
                        current_qty = float(pos.get("qty", 0.0))
                        break
            except Exception as e:
                logger.warning(f"Failed to fetch live state for pre-trade check: {e}")
                portfolio_state = {
                    "total_value": Config.FALLBACK_EQUITY,
                    "drawdown": 0.0,
                    "positions": [],
                }

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
