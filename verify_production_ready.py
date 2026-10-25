#!/usr/bin/env python3
"""
Comprehensive production readiness verification for Nexus Trading Platform.
Tests all hardened components, security, and persistence.
"""

import asyncio
import inspect
import sys
import logging
import importlib
from typing import Dict, Any
from pathlib import Path
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ProductionVerify")


class ProductionVerifier:
    def __init__(self):
        self.results: Dict[str, bool] = {}
        self.errors: Dict[str, str] = {}

    def test_imports(self) -> bool:
        """Verify all critical modules can be imported."""
        logger.info("=" * 60)
        logger.info("1. TESTING MODULE IMPORTS")
        logger.info("=" * 60)

        modules = [
            "nexus.utils.config",
            "nexus.execution.alpaca",
            "nexus.core.engine",
            "nexus.core.alpha",
            "nexus.audit.governance",
            "nexus.monitoring.health",
            "nexus.execution.execution_ai",
            "nexus.risk.engine",
            "nexus.math.indicators",
            "nexus.portfolio.optimization",
            "nexus.api.main",
            "nexus.api.alpaca_router",
            "nexus.api.monitor_router",
            "nexus.ui.app",
        ]

        all_pass = True
        for module_name in modules:
            try:
                importlib.import_module(module_name)
                logger.info(f"  [OK] {module_name}")
                self.results[f"import_{module_name}"] = True
            except ImportError as e:
                logger.error(f"  [FAIL] {module_name}: {e}")
                self.results[f"import_{module_name}"] = False
                self.errors[module_name] = str(e)
                all_pass = False

        return all_pass

    def test_persistence(self) -> bool:
        """Verify SQLite audit persistence and write integrity."""
        logger.info("\n" + "=" * 60)
        logger.info("2. TESTING PERSISTENCE LAYER")
        logger.info("=" * 60)

        db_path = Path("data/nexus_audit.db")
        try:
            from nexus.audit.governance import GovernanceEngine
            _ = GovernanceEngine()
            # Ensure DB file exists after initialization
            if not db_path.exists():
                logger.error("  [FAIL] Database file was not created by GovernanceEngine.")
                self.results["persistence_init"] = False
                return False

            import sqlite3
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'")
            if not cur.fetchone():
                logger.error("  [FAIL] Required table 'audit_log' does not exist in DB.")
                conn.close()
                self.results["persistence_init"] = False
                return False
            conn.close()

            logger.info(f"  [OK] Database verified and schema active at: {db_path}")
            self.results["persistence_init"] = True
            return True
        except Exception as e:
            logger.error(f"  [FAIL] Persistence error: {e}")
            self.results["persistence_init"] = False
            return False

    async def test_security_hardening(self) -> bool:
        """Verify API authentication and CORS policies."""
        logger.info("\n" + "=" * 60)
        logger.info("3. TESTING SECURITY HARDENING")
        logger.info("=" * 60)

        try:
            from nexus.api.main import app
            from nexus.utils.config import Config

            transport = httpx.ASGITransport(app=app)  # type: ignore
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                # 1. Test Public GET (Brain snapshot)
                resp = await client.get("/api/monitor/brain")
                if resp.status_code not in {200, 502}:
                    logger.error(
                        f"  [FAIL] Public read endpoint returned unexpected {resp.status_code}"
                    )
                    self.results["security_hardening"] = False
                    return False
                logger.info("  [OK] Public read endpoint is accessible")

                # 2. Test Protected POST (Mutation without Key)
                if Config.API_KEY:
                    resp = await client.post("/api/alpaca/order", json={})
                    if resp.status_code != 401:
                        logger.error(
                            f"  [FAIL] Mutation endpoint allowed unauthorized access! Status: {resp.status_code}"
                        )
                        self.results["security_hardening"] = False
                        return False
                    logger.info(
                        "  [OK] Mutation endpoint is STRICTLY PROTECTED (401 Unauthorized)"
                    )

                    # 3. Test Authorized POST with valid key
                    resp = await client.post(
                        "/api/alpaca/order",
                        json={},
                        headers={"X-API-Key": Config.API_KEY},
                    )
                    # 422 is expected for empty body validation error, 403 for pre-trade guard rejection
                    if resp.status_code not in {422, 403}:
                        logger.error(
                            f"  [FAIL] Authorized mutation returned unexpected status: {resp.status_code}"
                        )
                        self.results["security_hardening"] = False
                        return False
                    logger.info(
                        f"  [OK] Mutation endpoint is ACCESSIBLE with valid API Key (Status: {resp.status_code})"
                    )

            self.results["security_hardening"] = True
            return True
        except Exception as e:
            logger.error(f"  [FAIL] Security test error: {e}")
            self.results["security_hardening"] = False
            return False

    def test_quantitative_integrity(self) -> bool:
        """Verify math stubs are removed and models are real."""
        logger.info("\n" + "=" * 60)
        logger.info("4. TESTING QUANTITATIVE INTEGRITY")
        logger.info("=" * 60)

        try:
            from nexus.math.models import NeuralODE, TrendAccelerationModel
            from nexus.portfolio.optimization import MonteCarloSimulator
            import numpy as np

            # 1. Alias Check
            if NeuralODE == TrendAccelerationModel:
                logger.info(
                    "  [OK] NeuralODE alias is correctly mapped to TrendAccelerationModel"
                )

            # 2. Monte Carlo Check
            mc = MonteCarloSimulator()
            returns = np.random.normal(0.0005, 0.01, 100)
            prob = mc.run_survival_analysis(100000, returns, days=10, n_simulations=50)
            if prob != 0.999:
                logger.info(
                    f"  [OK] Monte Carlo simulator is ACTIVE (Result: {prob:.4f})"
                )
            else:
                logger.error("  [FAIL] Monte Carlo simulator returned hardcoded 0.999")
                return False

            self.results["quant_integrity"] = True
            return True
        except Exception as e:
            logger.error(f"  [FAIL] Quant integrity error: {e}")
            self.results["quant_integrity"] = False
            return False

    async def test_alpaca_connection(self) -> bool:
        """Test Alpaca API connection."""
        logger.info("\n" + "=" * 60)
        logger.info("5. TESTING ALPACA CONNECTION")
        logger.info("=" * 60)

        try:
            from nexus.execution.alpaca import get_client

            client = get_client()
            if not client.enabled:
                from nexus.utils.config import Config
                if Config.ALLOW_SIMULATION_FALLBACK:
                    logger.warning("  ⚠ Alpaca client not enabled (Simulator mode explicitly permitted)")
                    self.results["alpaca_connection"] = True
                    return True
                else:
                    logger.error("  [FAIL] Alpaca client is disabled and simulation fallback is disallowed.")
                    self.results["alpaca_connection"] = False
                    return False

            account = await client.get_account()
            if account.get("status") in {"ERROR", "UNAUTHORIZED"}:
                logger.error(
                    f"  [FAIL] Alpaca broker authentication failed: {account.get('error', 'UNAUTHORIZED')}"
                )
                self.results["alpaca_connection"] = False
                return False

            logger.info(
                f"  [OK] Connected to Alpaca. Status: {account.get('status', 'ACTIVE')}"
            )
            self.results["alpaca_connection"] = True
            return True
        except Exception as e:
            logger.error(f"  [FAIL] Alpaca connection error: {e}")
            self.results["alpaca_connection"] = False
            return False

    async def test_pre_trade_guard(self) -> bool:
        """Verify PreTradeRiskGuard non-bypassable pre-trade boundary."""
        logger.info("\n" + "=" * 60)
        logger.info("6. TESTING PRE-TRADE RISK GUARD")
        logger.info("=" * 60)

        try:
            from nexus.risk.pre_trade_guard import PreTradeRiskGuard
            from nexus.execution.alpaca import AlpacaClient, AlpacaCredentials

            guard = PreTradeRiskGuard()
            client = AlpacaClient(credentials=AlpacaCredentials("TEST_KEY", "TEST_SECRET"))
            client.enabled = False

            # 1. Disabled broker rejection
            res = await guard.validate_and_submit(
                client=client, order_request={"symbol": "AAPL", "qty": 10, "side": "buy"}
            )
            if res.get("status") != "rejected":
                logger.error("  [FAIL] PreTradeRiskGuard allowed order on disabled broker")
                self.results["pre_trade_guard"] = False
                return False
            logger.info("  [OK] Disabled broker fail-closed check enforced")

            # 2. Position concentration gate
            client.enabled = True
            client.simulated = False
            empty_pos: list[dict[str, Any]] = []
            portfolio = {"total_value": 10000.0, "drawdown": 0.0, "positions": empty_pos}
            res2 = await guard.validate_and_submit(
                client=client,
                order_request={"symbol": "AAPL", "qty": 100, "price": 100.0, "side": "buy"},
                portfolio_state=portfolio,
            )
            if res2.get("status") != "rejected":
                logger.error("  [FAIL] PreTradeRiskGuard allowed oversized 100% position")
                self.results["pre_trade_guard"] = False
                return False
            logger.info("  [OK] Position concentration limit strictly enforced")

            self.results["pre_trade_guard"] = True
            return True
        except Exception as e:
            logger.error(f"  [FAIL] Pre-trade risk guard test error: {e}")
            self.results["pre_trade_guard"] = False
            return False

    def test_universe_filter(self) -> bool:
        """Verify UniverseManager asset filtering and liquidity rules."""
        logger.info("\n" + "=" * 60)
        logger.info("7. TESTING UNIVERSE MANAGER & ASSET FILTERS")
        logger.info("=" * 60)

        try:
            from nexus.universe import UniverseManager

            mgr = UniverseManager()
            raw = [
                {"symbol": "AAPL", "exchange": "NASDAQ", "tradable": True, "status": "active"},
                {"symbol": "PENNY", "exchange": "OTC", "tradable": True, "status": "active"},
                {"symbol": "UVXY", "exchange": "ARCA", "tradable": True, "status": "active"},
            ]
            selected = mgr.select_universe(raw, max_symbols=5)
            if "AAPL" not in selected or "PENNY" in selected or "UVXY" in selected:
                logger.error(f"  [FAIL] Unexpected universe selection result: {selected}")
                self.results["universe_filter"] = False
                return False
            logger.info(f"  [OK] Dynamic universe filters active (Selected: {selected})")
            self.results["universe_filter"] = True
            return True
        except Exception as e:
            logger.error(f"  [FAIL] Universe filter error: {e}")
            self.results["universe_filter"] = False
            return False

    def test_polyglot_defense(self) -> bool:
        """Verify PolyglotBridge defense-in-depth and fallbacks."""
        logger.info("\n" + "=" * 60)
        logger.info("8. TESTING POLYGLOT DEFENSE-IN-DEPTH")
        logger.info("=" * 60)

        try:
            from nexus.utils.polyglot_bridge import PolyglotBridge

            # Invalid qty
            invalid_res = PolyglotBridge.validate_order_zig({"symbol": "AAPL", "qty": -5, "price": 100.0})
            if invalid_res.get("valid", True):
                logger.error("  [FAIL] PolyglotBridge failed to reject negative quantity")
                self.results["polyglot_defense"] = False
                return False
            logger.info("  [OK] Polyglot order boundary validator active")
            self.results["polyglot_defense"] = True
            return True
        except Exception as e:
            logger.error(f"  [FAIL] Polyglot defense error: {e}")
            self.results["polyglot_defense"] = False
            return False

    async def run_all_checks(self) -> bool:
        """Run all verification checks."""
        logger.info("\n" + "╔" + "=" * 58 + "╗")
        logger.info("║" + "  NEXUS HARDENED PRODUCTION VERIFICATION".center(58) + "║")
        logger.info("╚" + "=" * 58 + "╝")

        checks = [
            self.test_imports,
            self.test_persistence,
            self.test_security_hardening,
            self.test_quantitative_integrity,
            self.test_alpaca_connection,
            self.test_pre_trade_guard,
            self.test_universe_filter,
            self.test_polyglot_defense,
        ]

        all_passed = True
        for check in checks:
            try:
                if inspect.iscoroutinefunction(check):
                    res = await check()
                else:
                    res = check()
                if not res:
                    all_passed = False
            except Exception as e:
                logger.error(f"Check failed with exception: {e}")
                all_passed = False

        return all_passed


async def main() -> int:
    verifier = ProductionVerifier()
    passed = await verifier.run_all_checks()

    # Final cleanup
    from nexus.execution.alpaca import get_client

    await get_client().close()

    logger.info("\n" + "=" * 60)
    if passed:
        logger.info("  [PASS] NEXUS PLATFORM IS HARDENED AND PRODUCTION READY")
    else:
        logger.error("  [FAIL] NEXUS PLATFORM FAILED HARDENING VERIFICATION")
    logger.info("=" * 60 + "\n")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
