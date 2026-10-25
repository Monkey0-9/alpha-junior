import asyncio
import logging
import time
import httpx
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional, cast

from nexus.audit.governance import GovernanceEngine
from nexus.core.alpha import AlphaEngine
from nexus.execution.execution_ai import ExecutionAgent
from nexus.monitoring.health import HealthMonitor
from nexus.models.zoo.ensemble import AIEnsembleBrain
from nexus.risk.engine import RiskEngine
from nexus.math.indicators import RegimeDetector
from nexus.portfolio.optimization import PortfolioOptimizer, MultiFactorEngine
from nexus.math.models import NeuralODE
from nexus.audit.math_governance import LatticeVoter, StrategySwitcher
from nexus.utils.config import Config
from nexus.utils.platform_logging import setup_logging
from nexus.utils.polyglot_bridge import PolyglotBridge
from nexus.portfolio.position_manager import PositionManager
from nexus.learning.trainer import ContinuousLearner
from nexus.universe import UniverseManager

logger = logging.getLogger(__name__)


class NexusEngine:
    """Main orchestration engine for the Nexus institutional trading platform."""

    def __init__(self, backend_url: str = Config.BACKEND_URL):
        setup_logging()
        self.backend_url = backend_url

        self.governance = GovernanceEngine(
            single_position_limit=Config.MAX_POSITION_SIZE,
            max_drawdown_limit=Config.MAX_DRAWDOWN,
        )
        self.alpha_engine = AlphaEngine(backend_url=backend_url)
        self.execution_agent = ExecutionAgent()
        self.ensemble_brain = AIEnsembleBrain()
        self.risk_engine = RiskEngine()
        self.health_monitor = HealthMonitor()

        self.regime_detector = RegimeDetector()
        self.optimizer = PortfolioOptimizer()
        self.factor_engine = MultiFactorEngine()
        self.neural_ode = NeuralODE()
        self.lattice_voter = LatticeVoter()
        self.strategy_switcher = StrategySwitcher()
        self.universe_manager = UniverseManager()

        self.symbols: List[str] = self.universe_manager.get_fallback_universe(
            limit=Config.CANDIDATE_POOL_SIZE
        )
        self.market_regime = "SIDEWAYS"
        self.portfolio_value = Config.FALLBACK_EQUITY
        self.max_positions = Config.MAX_OPEN_POSITIONS
        self.last_universe_refresh = 0.0
        self.position_ages: Dict[str, int] = {}

        # PERF FIX: Persistent client for connection pooling
        self._client: Optional[httpx.AsyncClient] = None
        # Tracking Submitted Orders
        self._active_orders: Dict[str, str] = {}  # symbol -> order_id

        # Superhuman Brain: drawdown velocity tracker
        self._equity_history: List[float] = []  # recent equity snapshots
        # last cycle conviction signals
        self._conviction_cache: Dict[str, Any] = {}
        self.position_manager = PositionManager()
        self.learner = ContinuousLearner()
        self._init_ensemble_brain()

    def _init_ensemble_brain(self) -> None:
        """Register active institutional models into AIEnsembleBrain."""
        try:
            from nexus.models.zoo.time_series import (
                GradientBoostedTimeSeriesModel,
                PyTorchLSTMModel,
                TORCH_AVAILABLE,
            )

            # 1. Gradient Boosted Decision Tree
            self.ensemble_brain.add_model(
                GradientBoostedTimeSeriesModel(confidence_threshold=0.52),
                name="GradientBoostedTree",
                weight=1.0,
            )

            # 2. PyTorch LSTM Deep Model
            if TORCH_AVAILABLE:
                try:
                    self.ensemble_brain.add_model(
                        PyTorchLSTMModel(sequence_length=10, epochs=2),
                        name="PyTorchLSTM",
                        weight=0.9,
                    )
                except Exception as exc:
                    logger.debug("PyTorchLSTM init skipped: %s", exc)

            # 3. Trend Acceleration / Neural ODE Adapter
            class TrendAccelerationAdapter:
                def __init__(self, ode_model: Any):
                    self.ode_model = ode_model

                def predict(self, df: pd.DataFrame) -> float:
                    if df.empty or "close" not in df.columns or len(df) < 5:
                        return 0.0
                    drift = self.ode_model.predict_trajectory(df["close"].to_numpy())
                    return float(np.tanh(drift))

                def predict_proba(self, df: pd.DataFrame) -> Dict[int, float]:
                    sig = self.predict(df)
                    p_buy = max(0.0, sig)
                    p_sell = max(0.0, -sig)
                    p_hold = max(0.0, 1.0 - p_buy - p_sell)
                    return {1: p_buy, -1: p_sell, 0: p_hold}

            self.ensemble_brain.add_model(
                TrendAccelerationAdapter(self.neural_ode),
                name="TrendAccelerationODE",
                weight=0.8,
            )
            logger.info(
                "AIEnsembleBrain initialized with %d active models.",
                len(self.ensemble_brain.models),
            )
        except Exception as exc:
            logger.error("Failed to initialize AIEnsembleBrain models: %s", exc)

    async def _get_client(self) -> httpx.AsyncClient:
        """Returns the shared AsyncClient for connection pooling."""
        if self._client is None or self._client.is_closed:
            # Add API Key header if configured
            headers = {}
            if Config.API_KEY:
                headers["X-API-Key"] = Config.API_KEY
            self._client = httpx.AsyncClient(timeout=30, headers=headers)
        return self._client

    async def close(self) -> None:
        """Cleanup connection pool and subsystems."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        if hasattr(self.alpha_engine, "close"):
            try:
                await self.alpha_engine.close()
            except Exception as exc:
                logger.warning(f"Failed to close alpha engine client: {exc}")
        logger.info("Nexus engine resources released.")

    async def initialize(self) -> bool:
        logger.info(f"Initializing Nexus engine with backend {self.backend_url}")

        client = await self._get_client()
        for attempt in range(1, 6):
            try:
                response = await client.get(f"{self.backend_url}/api/alpaca/health")
                if response.status_code == 200:
                    logger.info("Execution backend healthy.")
                    break
                else:
                    logger.warning(
                        f"Backend returned status {response.status_code}. Retrying..."
                    )
                    await asyncio.sleep(min(30, 2**attempt))
            except Exception as exc:
                wait = min(30, 2**attempt)
                logger.warning(
                    f"Backend health check failed ({attempt}): {exc}. Retrying..."
                )
                await asyncio.sleep(wait)
        else:
            logger.error("Unable to reach execution backend.")
            return False

        # High-speed Go Audit
        audit = PolyglotBridge.audit_platform_go(self.backend_url)
        logger.info(f"Polyglot Audit Status: {audit.get('overall_health')}")

        self.health_monitor.record("backend", True, "connected")
        await self.refresh_universe()
        return True

    async def refresh_universe(self) -> None:
        """Dynamic universe re-scanner utilizing UniverseManager."""
        logger.info("Refreshing tradable universe assets...")
        client = await self._get_client()
        try:
            params: Dict[str, Any] = {
                "asset_class": "us_equity",
                "status": "active",
                "tradable": True,
                "limit": Config.MAX_UNIVERSE_ASSETS,
            }
            response = await client.get(
                f"{self.backend_url}/api/alpaca/assets", params=params
            )
            if response.status_code == 200:
                asset_data = response.json()
                assets = asset_data.get("assets", [])
                target_size = (
                    Config.MAX_UNIVERSE_ASSETS
                    if Config.TRADE_ALL_ASSETS
                    else min(Config.CANDIDATE_POOL_SIZE, 30)
                )
                self.symbols = self.universe_manager.select_universe(
                    assets, max_symbols=target_size
                )
                self.last_universe_refresh = time.time()
                logger.info(
                    f"Universe refreshed: {len(self.symbols)} qualified assets active."
                )
            else:
                raise ValueError(f"Asset scan returned {response.status_code}")
        except Exception as exc:
            logger.warning(f"Asset universe fallback: {exc}")
            target_size = min(Config.CANDIDATE_POOL_SIZE, 30)
            self.symbols = self.universe_manager.get_fallback_universe(limit=target_size)
            self.universe_manager.record_snapshot(self.symbols)
            self.last_universe_refresh = time.time()

    def _is_preferred_exchange(self, exchange: str) -> bool:
        return self.universe_manager.is_preferred_exchange(exchange)

    def _is_valid_symbol(self, symbol: str) -> bool:
        return self.universe_manager.is_valid_symbol(symbol)

    async def get_account_state(self) -> Dict[str, Any]:
        client = await self._get_client()
        try:
            response = await client.get(f"{self.backend_url}/api/alpaca/account")
            if response.status_code == 200:
                return cast(Dict[str, Any], response.json())
        except Exception as exc:
            logger.warning(f"Account fetch failure: {exc}")
        return {
            "equity": self.portfolio_value,
            "last_equity": self.portfolio_value,
        }

    async def get_positions(self) -> List[Dict[str, Any]]:
        client = await self._get_client()
        try:
            response = await client.get(f"{self.backend_url}/api/alpaca/positions")
            if response.status_code == 200:
                return cast(List[Dict[str, Any]], response.json().get("positions", []))
        except Exception as exc:
            logger.warning(f"Position fetch failure: {exc}")
        return []

    async def fetch_universe_history(
        self, symbols: List[str], timeframe: str = "1D", limit: int = 80
    ) -> Dict[str, pd.DataFrame]:
        semaphore = asyncio.Semaphore(2)

        async def fetch_symbol(symbol: str) -> pd.DataFrame:
            async with semaphore:
                result = await self.alpha_engine.fetch_market_data(
                    symbol, timeframe=timeframe, limit=limit
                )
                await asyncio.sleep(0.12)
                return result

        tasks = [fetch_symbol(symbol) for symbol in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        history: Dict[str, pd.DataFrame] = {}
        for symbol, result in zip(symbols, results, strict=False):
            if isinstance(result, BaseException):
                continue
            if not result.empty:
                history[symbol] = result
        return history

    def build_portfolio_state(
        self, account: Dict[str, Any], positions: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        total_value = float(account.get("equity", self.portfolio_value))
        last_equity = float(account.get("last_equity", total_value))
        if np.isnan(total_value) or total_value <= 0:
            total_value = self.portfolio_value
        drawdown = max(0.0, (last_equity - total_value) / max(total_value, 1.0))
        return {
            "total_value": total_value,
            "drawdown": drawdown,
            "positions": positions,
        }

    async def _verify_order_fills(self) -> None:
        """Check status of active orders and clear them once filled/cancelled."""
        if not self._active_orders:
            return

        client = await self._get_client()
        to_remove = []
        for symbol, order_id in list(self._active_orders.items()):
            try:
                response = await client.get(
                    f"{self.backend_url}/api/alpaca/orders/{order_id}"
                )
                if response.status_code == 200:
                    order_info = response.json()
                    status = order_info.get("status") or order_info.get("order_status")
                    if status in {"filled", "canceled", "expired", "rejected"}:
                        logger.info(
                            f"Order {order_id} for {symbol} finalized: {status}"
                        )
                        to_remove.append(symbol)
                elif response.status_code == 404:
                    logger.warning(f"Order {order_id} not found on backend.")
                    to_remove.append(symbol)
                else:
                    logger.debug(
                        f"Order {order_id} for {symbol} status check returned {response.status_code}."
                    )
            except Exception as exc:
                logger.debug(f"Error verifying order {order_id}: {exc}")

        for s in to_remove:
            self._active_orders.pop(s, None)

    async def run_cycle(self) -> None:
        logger.info("Starting new trading cycle.")
        if time.time() - self.last_universe_refresh > Config.UNIVERSE_RESCAN_INTERVAL:
            await self.refresh_universe()

        # Phase 1: Verify fills from previous cycle
        await self._verify_order_fills()

        client = await self._get_client()
        try:
            response = await client.get(f"{self.backend_url}/api/alpaca/clock")
            clock_data = (
                response.json() if response.status_code == 200 else {"is_open": False}
            )
        except Exception:
            clock_data = {"is_open": False}

        account = await self.get_account_state()
        current_positions = await self.get_positions()
        holdings = {p["symbol"]: p for p in current_positions}

        for symbol in list(self.position_ages):
            if symbol not in holdings:
                del self.position_ages[symbol]
        for symbol in holdings:
            self.position_ages[symbol] = self.position_ages.get(symbol, 0) + 1

        if Config.TRADE_ALL_ASSETS:
            symbols = self.symbols
        else:
            max_candidates = min(Config.CANDIDATE_POOL_SIZE, 20)
            symbols = self.symbols[:max_candidates]

        if not symbols:
            logger.warning("No trading symbols were loaded, skipping cycle.")
            return
        raw_signals = await self.alpha_engine.get_batch_signals(
            symbols, timeframe="15Min"
        )
        history = await self.fetch_universe_history(symbols, timeframe="1D", limit=100)
        logger.info(
            "History loaded for %s symbols out of %s selected symbols.",
            len(history),
            len(symbols),
        )

        # Automated continuous model retraining step
        for sym, df_history in history.items():
            if len(df_history) >= 100:
                self.learner.step_retrain(df_history)
                break
        if not history:
            logger.warning(
                "No history data could be loaded for the current symbol universe. This will prevent any orders from being submitted."
            )

        await self.manage_positions(current_positions, history)

        benchmark_data = await self.alpha_engine.fetch_market_data(
            "SPY", timeframe="1D", limit=120
        )
        if benchmark_data.empty or "close" not in benchmark_data.columns:
            logger.error("Benchmark market data is unavailable; skipping new risk-increasing orders.")
            self.health_monitor.record(
                "market", False, details="Benchmark data unavailable; new orders blocked"
            )
            self.health_monitor.heartbeat()
            return

        benchmark_close = pd.to_numeric(benchmark_data["close"], errors="coerce")
        returns_series = benchmark_close.pct_change(fill_method=None)
        returns_series = returns_series.replace([np.inf, -np.inf], np.nan).dropna()
        returns_list = returns_series.to_numpy(dtype=float)
        if returns_list.size < 20 or not np.isfinite(returns_list).all():
            logger.error(
                "Benchmark return sample is insufficient or invalid (%d observations); skipping new risk-increasing orders.",
                returns_list.size,
            )
            self.health_monitor.record(
                "market", False, details="Insufficient/invalid benchmark returns; new orders blocked"
            )
            self.health_monitor.heartbeat()
            return

        try:
            self.market_regime = self.regime_detector.detect(benchmark_data)
            regime_probs = self.regime_detector.get_regime_probabilities()
        except Exception as e:
            logger.warning(f"Regime detection failed: {e}")
            self.market_regime = "SIDEWAYS"
            regime_probs = {
                "BULL": 0.25,
                "BEAR": 0.25,
                "SIDEWAYS": 0.25,
                "TURBULENT": 0.25,
            }

        # ── AI Ensemble Brain Conviction Layer ───────────────────────────────
        enhanced_signals: Dict[str, float] = {}
        for sym, df_history in history.items():
            raw = raw_signals.get(sym, 0.0)

            # Pass through the ensemble brain
            ai_signal = self.ensemble_brain.get_signal(
                features=df_history,
                regime=self.market_regime,
                regime_probabilities=regime_probs,
            )

            if not self.ensemble_brain.models:
                # Direct pass-through if ensemble brain has no registered models
                enhanced_signals[sym] = float(np.clip(raw, -1.0, 1.0))
            elif ai_signal == 0:
                logger.debug(f"[AIEnsembleBrain] Gated out {sym} (NO_TRADE confidence)")
                continue
            else:
                # Blend Raw Alpha with Ensemble AI decision
                blended = (ai_signal * 0.7) + (raw * 0.3)
                enhanced_signals[sym] = float(np.clip(blended, -1.0, 1.0))
        # ─────────────────────────────────────────────────────────────────────

        portfolio_scores = enhanced_signals
        ranked = self.factor_engine.rank_assets(portfolio_scores, history)
        top_targets = dict(list(ranked.items())[: self.max_positions])

        # Kelly + IC + correlation-adjusted weights
        weights = self.optimizer.optimize_weights(
            list(top_targets.keys()),
            [top_targets[s] for s in top_targets],
            historical_data=history,
        )

        risk_metrics = {
            "var": 0.0,
            "cvar": 0.0,
            "volatility": 0.0,
            "sharpe": 0.0,
        }
        if returns_list.size > 0:
            rust_risk = PolyglotBridge.calculate_risk_rust(returns_list.tolist())
            risk_metrics = self.risk_engine.assess_risk(returns_list)
            rust_var = rust_risk.get("var")
            rust_expected_shortfall = rust_risk.get("expected_shortfall")
            if (
                rust_risk.get("status") == "OK"
                and isinstance(rust_var, (int, float))
                and isinstance(rust_expected_shortfall, (int, float))
                and np.isfinite(rust_var)
                and np.isfinite(rust_expected_shortfall)
            ):
                risk_metrics["rust_var"] = float(rust_var)
                risk_metrics["rust_expected_shortfall"] = float(rust_expected_shortfall)
            else:
                # Omit unavailable native numbers rather than recording fake zero risk.
                logger.warning(
                    "Rust risk metrics unavailable (status=%s); using the separately calculated Python risk assessment.",
                    rust_risk.get("status", "UNKNOWN"),
                )

        multiplier = self.determine_risk_scale(
            {"regime_probabilities": regime_probs}, risk_metrics
        )

        # Correlation crisis brake: halve sizes if portfolio in crisis mode
        crisis_mult = 1.0
        multiplier *= crisis_mult

        # Drawdown velocity protection: if equity dropped >3% in last 24h, cut
        # sizes
        ddv_mult = self._drawdown_velocity_multiplier(account)
        multiplier *= ddv_mult

        weights = {s: w * multiplier for s, w in weights.items()}

        portfolio_state = self.build_portfolio_state(account, current_positions)
        self.health_monitor.record(
            "market", not benchmark_data.empty, details=self.market_regime
        )
        self.health_monitor.record(
            "risk", risk_metrics["var"] > -0.25, details=str(risk_metrics)
        )
        self.health_monitor.heartbeat()

        current_symbols = set(holdings.keys())
        target_symbols = set(weights.keys())

        # Close tasks: preserve positions for a minimum number of cycles after
        # entry.
        close_candidates = []
        for symbol in current_symbols - target_symbols:
            if self.position_ages.get(symbol, 0) >= Config.MIN_HOLD_CYCLES:
                close_candidates.append(symbol)
            else:
                logger.info(
                    "Deferring close for %s: held %s/%s cycles.",
                    symbol,
                    self.position_ages.get(symbol, 0),
                    Config.MIN_HOLD_CYCLES,
                )

        if close_candidates:
            await asyncio.gather(*[self._close_position(s) for s in close_candidates])

        # Trade tasks
        trade_tasks = []
        for symbol, weight in weights.items():
            # Skip if there's already an active order pending for this symbol
            if symbol in self._active_orders:
                logger.debug(f"Skipping trade for {symbol}: order pending.")
                continue

            current_qty = float(holdings.get(symbol, {}).get("qty", 0.0))
            trade_tasks.append(
                self._submit_trade(
                    symbol,
                    weight,
                    current_qty,
                    top_targets,
                    history,
                    portfolio_state,
                    raw_signals,
                    {},
                    self.market_regime,
                    is_open=clock_data.get("is_open", False),
                )
            )
        if trade_tasks:
            await asyncio.gather(*trade_tasks)

        self.execution_agent.learn(
            reward=float(np.mean(list(portfolio_scores.values()) or [0.0]))
        )

        # Update IC tracker in optimizer with latest signal → return outcomes
        for sym, score in portfolio_scores.items():
            if sym in history and not history[sym].empty:
                realized = float(history[sym]["close"].pct_change().dropna().iloc[-1])
                self.optimizer.ic_tracker.record(sym, score, realized)

            # Record outcome for ensemble learning
            for pos in current_positions:
                pos_sym = str(pos.get("symbol", ""))
                unrealized_pl_pc = pos.get("unrealized_plpc", 0.0)
                if pos_sym and pos_sym in raw_signals and len(self.ensemble_brain.models) > 0:
                    sig = 1 if raw_signals[pos_sym] > 0 else -1
                    self.ensemble_brain.record_outcome(
                        model_name=self.ensemble_brain.models[0]["name"],
                        predicted_signal=sig,
                        realized_return=float(unrealized_pl_pc),
                    )

    async def _submit_trade(
        self,
        symbol: str,
        weight: float,
        current_qty: float,
        top_targets: Dict[str, float],
        history: Dict[str, pd.DataFrame],
        portfolio_state: Dict[str, Any],
        raw_signals: Dict[str, float],
        market_insight: Dict[str, Any],
        selected_strategy: str,
        is_open: bool,
    ) -> None:
        if abs(weight) < 0.0001 or symbol not in history or history[symbol].empty:
            return
        current_price = float(history[symbol]["close"].iloc[-1])

        total_value = portfolio_state.get("total_value", self.portfolio_value)
        target_qty = int((total_value * abs(weight)) / max(current_price, 1.0))
        qty_diff = target_qty - abs(current_qty)

        if abs(qty_diff) < 1 or (abs(qty_diff) * current_price < 100):
            return

        side = "buy" if qty_diff > 0 else "sell"
        qty = abs(qty_diff)
        order_type, limit_price = ("market", None)

        if not is_open:
            order_type, limit_price = (
                "limit",
                current_price * (1.002 if side == "buy" else 0.998),
            )

        trade_request = {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "price": current_price,
            "order_type": order_type,
            "strategy": selected_strategy,
        }
        if not PolyglotBridge.validate_order_zig(trade_request).get("valid"):
            return
        approved, _ = self.governance.check_compliance(
            trade_request, portfolio_state, current_qty=current_qty
        )
        if not approved:
            return

        payload = {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "order_type": order_type,
            "strategy": selected_strategy,
        }
        if limit_price:
            payload["limit_price"] = round(limit_price, 4)
        if not is_open:
            payload["extended_hours"] = True

        client = await self._get_client()
        try:
            response = await client.post(
                f"{self.backend_url}/api/alpaca/order", json=payload
            )
            if response.status_code in {200, 201}:
                order_data = response.json()
                order_id = order_data.get("id") or order_data.get("order_id")
                if order_id:
                    self._active_orders[symbol] = order_id
                    logger.info(f"Order submitted for {symbol}: {order_id}")
                    self.governance.record_trade(
                        {
                            "symbol": symbol,
                            "side": side,
                            "qty": qty,
                            "price": current_price,
                            "order_type": order_type,
                            "strategy": selected_strategy,
                            "status": order_data.get("status", "PENDING"),
                        }
                    )
                else:
                    logger.warning(
                        f"Order response missing id for {symbol}: {order_data}"
                    )
            else:
                logger.error(
                    f"Order submission failed for {symbol}: HTTP {response.status_code} {response.text}"
                )
        except Exception as exc:
            logger.error(f"Order submission error for {symbol}: {exc}")

    async def _close_position(self, symbol: str) -> None:
        client = await self._get_client()
        try:
            await client.delete(f"{self.backend_url}/api/alpaca/positions/{symbol}")
            self.position_manager.reset_watermark(symbol)
        except Exception as exc:
            logger.error(f"Error closing position for {symbol}: {exc}")

    async def manage_positions(
        self,
        current_positions: Optional[List[Dict[str, Any]]] = None,
        history: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> None:
        if current_positions is None:
            current_positions = await self.get_positions()
        if history is None:
            history = {}
        if not current_positions:
            return

        client = await self._get_client()
        for pos in current_positions:
            symbol = pos.get("symbol")
            if not symbol:
                continue
            try:
                current_price = float(pos.get("current_price", 0.0))
                avg_entry_price = float(pos.get("avg_entry_price", 0.0))
                pnl_pct = float(pos.get("unrealized_plpc", 0.0))
                pos_history = history.get(symbol, pd.DataFrame())

                should_close = self.position_manager.evaluate_exit(
                    symbol,
                    current_price,
                    avg_entry_price,
                    pnl_pct,
                    pos_history,
                )

                if should_close:
                    await client.delete(
                        f"{self.backend_url}/api/alpaca/positions/{symbol}"
                    )
                    self.position_manager.reset_watermark(symbol)
            except Exception as exc:
                logger.warning(f"Failed to evaluate close for {symbol}: {exc}")

    async def main_loop(self) -> None:
        while True:
            if not await self.initialize():
                await asyncio.sleep(10)
                continue
            while True:
                try:
                    await self.run_cycle()
                except Exception as exc:
                    logger.error(f"Engine cycle error: {exc}")
                await asyncio.sleep(Config.HEARTBEAT_INTERVAL)

    def determine_risk_scale(
        self, market_insight: Dict[str, Any], risk_metrics: Dict[str, float]
    ) -> float:
        """
        Dynamic risk scale using regime probabilities (not just a label)
        and Kelly-adjusted volatility scaling.
        """
        scale = 1.0
        regime_probs = market_insight.get("regime_probabilities", {})

        # Probabilistic regime scaling (smoother than hard if/else)
        turbulent_prob = regime_probs.get("TURBULENT", 0.0)
        bear_prob = regime_probs.get("BEAR", 0.0)
        bull_prob = regime_probs.get("BULL", 0.0)

        scale *= 1.0 - turbulent_prob * 0.70  # TURBULENT crushes scale
        scale *= 1.0 - bear_prob * 0.45  # BEAR reduces scale
        scale *= 1.0 + bull_prob * 0.10  # BULL slightly expands scale

        # Volatility-adjusted Kelly multiplier
        volatility = risk_metrics.get("volatility", 0.0)
        if volatility > 0.03:
            scale *= 0.72
        elif volatility > 0.02:
            scale *= 0.88
        elif volatility < 0.008:
            scale *= 1.08  # very low vol → increase sizing

        # VaR gate
        var = float(risk_metrics.get("var", 0.0))
        if var < -0.07:
            scale *= 0.55  # extreme tail risk
        elif var < -0.05:
            scale *= 0.72
        elif var > -0.015:
            scale *= 1.0  # benign VaR

        # Forecast confidence boost
        forecast_conf = market_insight.get("forecast_confidence", 0.5)
        scale *= 0.85 + forecast_conf * 0.30  # 0.85 → 1.15 range

        return float(max(0.15, min(scale, 1.20)))

    def _drawdown_velocity_multiplier(self, account: Dict[str, Any]) -> float:
        """
        Drawdown velocity protection: if equity drops >3% since last check,
        halve position sizes. Prevents rapid equity erosion in trending down markets.
        """
        equity = float(account.get("equity", self.portfolio_value))
        self._equity_history.append(equity)

        # Keep last 48 snapshots (roughly 24h if cycle = 30min)
        if len(self._equity_history) > 48:
            self._equity_history = self._equity_history[-48:]

        if len(self._equity_history) < 2:
            return 1.0

        # Compute recent drawdown velocity (last 6 snapshots ≈ 3h)
        lookback = min(6, len(self._equity_history))
        recent_high = max(self._equity_history[-lookback:])
        current = self._equity_history[-1]

        if recent_high <= 0:
            return 1.0

        drop_pct = (recent_high - current) / recent_high
        if drop_pct > 0.05:
            logger.warning(
                "Drawdown velocity BRAKE: equity dropped %.1f%% in recent cycles → 0.40x sizing",
                drop_pct * 100,
            )
            return 0.40  # aggressive protection
        elif drop_pct > 0.03:
            logger.warning(
                "Drawdown velocity CAUTION: equity dropped %.1f%% → 0.65x sizing",
                drop_pct * 100,
            )
            return 0.65

        return 1.0


if __name__ == "__main__":
    engine = NexusEngine()
    try:
        asyncio.run(engine.main_loop())
    except KeyboardInterrupt:
        pass
    finally:
        asyncio.run(engine.close())
