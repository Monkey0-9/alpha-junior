"""
nexus/risk/pre_trade_guard.py — Centralized Mandatory Pre-Trade Risk Guard.

Enforces a single, non-bypassable pre-trade approval boundary for both the
Core Engine and API Router order endpoints. No order can reach the broker
without passing this unified gate.
"""

import logging
import math
from datetime import datetime, timezone
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

        # 2. Establish a trustworthy mark for portfolio risk checks. Live orders
        # always use a fresh broker-market bar, never the caller's limit price.
        price_value = 0.0
        if not client.simulated:
            try:
                bars = await client.get_bars(symbol, timeframe="1Min", limit=1)
            except Exception as exc:
                logger.error("Unable to fetch current mark for %s: %s", symbol, exc)
                return {
                    "success": False,
                    "status": "rejected",
                    "error": "Unable to verify current market data; order rejected.",
                }
            if not bars:
                return {
                    "success": False,
                    "status": "rejected",
                    "error": "No trustworthy current market price is available; order rejected.",
                }
            latest_bar = bars[-1]
            timestamp = latest_bar.get("t") or latest_bar.get("timestamp") or latest_bar.get("time")
            if not isinstance(timestamp, str) or not timestamp.strip():
                return {
                    "success": False,
                    "status": "rejected",
                    "error": "Market bar has no timestamp; order rejected.",
                }
            try:
                timestamp_text = timestamp.strip()
                if timestamp_text.endswith("Z"):
                    timestamp_text = timestamp_text[:-1] + "+00:00"
                bar_time = datetime.fromisoformat(timestamp_text)
                if bar_time.tzinfo is None:
                    raise ValueError("timestamp lacks timezone")
                age_seconds = (
                    datetime.now(timezone.utc) - bar_time.astimezone(timezone.utc)
                ).total_seconds()
                max_age = max(1, int(Config.MAX_MARKET_DATA_AGE_SECONDS))
                if age_seconds < -60 or age_seconds > max_age:
                    return {
                        "success": False,
                        "status": "rejected",
                        "error": f"Market data is stale or future-dated (age={age_seconds:.1f}s, limit={max_age}s); order rejected.",
                    }
            except (TypeError, ValueError, OverflowError):
                return {
                    "success": False,
                    "status": "rejected",
                    "error": "Market bar timestamp is invalid; order rejected.",
                }
            try:
                price_value = float(latest_bar.get("close", latest_bar.get("c", 0.0)))
            except (TypeError, ValueError, KeyError):
                price_value = 0.0
        else:
            # Simulation is explicit and cannot be mistaken for live valuation.
            raw_price = (
                order_request.get("price")
                or order_request.get("limit_price")
                or order_request.get("stop_price")
            )
            try:
                price_value = float(raw_price) if raw_price is not None else 0.0
            except (TypeError, ValueError):
                price_value = 0.0
            if not math.isfinite(price_value) or price_value <= 0:
                bars = await client.get_bars(symbol, timeframe="1Min", limit=1)
                if bars:
                    try:
                        price_value = float(bars[-1].get("close", bars[-1].get("c", 0.0)))
                    except (TypeError, ValueError, KeyError):
                        price_value = 0.0

        if not math.isfinite(price_value) or price_value <= 0:
            return {
                "success": False,
                "status": "rejected",
                "error": "Market mark is missing or invalid; order rejected.",
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
        # Live account and position state are always re-read at the mandatory
        # order boundary. A caller-supplied snapshot is advisory only and cannot
        # be the authority for current concentration/exposure checks.
        if not client.simulated:
            try:
                if not client.enabled:
                    raise RuntimeError("Live broker execution is disabled.")
                acc = await client.get_account()
                if (
                    acc.get("status") != "ACTIVE"
                    or acc.get("simulated")
                    or not acc.get("enabled", True)
                ):
                    raise RuntimeError("Broker account is not verified ACTIVE live state.")
                equity_raw = acc.get("equity", acc.get("portfolio_value"))
                last_equity_raw = acc.get("last_equity", equity_raw)
                if equity_raw is None or last_equity_raw is None:
                    raise ValueError("Broker omitted equity values.")
                equity = float(equity_raw)
                last_equity = float(last_equity_raw)
                if (
                    not math.isfinite(equity)
                    or equity <= 0
                    or not math.isfinite(last_equity)
                    or last_equity <= 0
                ):
                    raise ValueError("Broker returned invalid equity values.")
                positions = await client.get_positions()
                dd = max(0.0, (last_equity - equity) / last_equity)
                verified_state = {
                    "total_value": equity,
                    "drawdown": dd,
                    "positions": positions,
                }
                if portfolio_state is not None:
                    supplied_equity = float(portfolio_state.get("total_value", 0.0))
                    # Reject material changes between the engine's snapshot and
                    # the authoritative broker read rather than sizing on stale data.
                    if (
                        not math.isfinite(supplied_equity)
                        or supplied_equity <= 0
                        or abs(supplied_equity - equity) / equity > 0.01
                    ):
                        raise RuntimeError("Precomputed equity snapshot differs from verified broker equity by more than 1%.")
                portfolio_state = verified_state
            except Exception as exc:
                logger.critical("Unable to verify live broker state at order boundary: %s", exc)
                return {
                    "success": False,
                    "status": "rejected",
                    "error": "Unable to verify current live account and positions; order rejected.",
                }
        elif portfolio_state is None:
            try:
                acc = await client.get_account()
                equity_raw = acc.get("equity", acc.get("portfolio_value"))
                last_equity_raw = acc.get("last_equity", equity_raw)
                if equity_raw is None or last_equity_raw is None:
                    raise ValueError("Simulation account omitted equity values.")
                equity = float(equity_raw)
                last_equity = float(last_equity_raw)
                if not math.isfinite(equity) or equity <= 0 or not math.isfinite(last_equity) or last_equity <= 0:
                    raise ValueError("Simulation account returned invalid equity values.")
                positions = await client.get_positions()
                portfolio_state = {
                    "total_value": equity,
                    "drawdown": max(0.0, (last_equity - equity) / last_equity),
                    "positions": positions,
                }
            except Exception as exc:
                logger.error("Unable to establish simulation account state; rejecting order: %s", exc)
                return {
                    "success": False,
                    "status": "rejected",
                    "error": "Unable to verify simulation account and positions; order rejected.",
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

        positions_value = portfolio_state.get("positions")
        if not isinstance(positions_value, list):
            return {
                "success": False,
                "status": "rejected",
                "error": "Portfolio positions are unavailable; order rejected.",
            }
        positions = positions_value
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
        try:
            approved, violations = self.governance.check_compliance(
                trade_compliance_req, portfolio_state, current_qty=current_qty
            )
        except Exception as exc:
            logger.critical(
                "Governance decision could not be durably recorded; halting broker client: %s",
                exc,
            )
            client.enabled = False
            client.simulated = False
            return {
                "success": False,
                "status": "rejected",
                "error": "Governance audit persistence failed; order rejected and execution halted.",
            }
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
            try:
                self.governance.record_trade(
                    {
                        "symbol": symbol,
                        "side": side,
                        "qty": qty,
                        "price": float(price),
                        "order_type": order_type,
                        "strategy": order_request.get("strategy", "manual"),
                        "status": "SUBMITTED",
                    }
                )
            except Exception as exc:
                # The broker may have accepted the order. Never return a generic
                # failure that could induce a duplicate retry; stop new submissions.
                logger.critical(
                    "Broker order submitted but lineage persistence failed; halt and reconcile before retry: %s",
                    exc,
                )
                client.enabled = False
                client.simulated = False
                return {
                    "success": True,
                    "status": "SUBMITTED_AUDIT_FAILURE",
                    "order_id": res.get("order_id") or res.get("id"),
                    "error": "Order may be active at the broker; trade lineage was not persisted. Do not retry; reconcile broker state and restore audit storage.",
                    "reconciliation_required": True,
                    "trading_halted": True,
                }

        return res
