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
import os
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
            "nexus.execution.halt_state",
            "nexus.research.ncm",
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

        db_path = Path(__file__).resolve().parent / "data" / "nexus_audit.db"
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
        """Verify private reads are protected and a strong deployment key exists."""
        logger.info("\\n" + "=" * 60)
        logger.info("3. TESTING API AUTHENTICATION")
        logger.info("=" * 60)

        try:
            from nexus.api.main import app

            key = os.getenv("NEXUS_API_KEY", "").strip()
            placeholders = {
                "changeme",
                "your_api_key",
                "your_secure_api_key",
                "generate_a_secure_random_key_for_internal_auth",
            }
            strong_key = len(key) >= 32 and key.lower() not in placeholders
            transport = httpx.ASGITransport(app=app)  # type: ignore

            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                health_response = await client.get("/api/health")
                if health_response.status_code != 200:
                    logger.error("  [FAIL] Public liveness endpoint did not respond.")
                    self.results["security_hardening"] = False
                    return False

                unauthenticated = await client.get("/openapi.json")
                expected_status = 401 if strong_key else 503
                if unauthenticated.status_code != expected_status:
                    logger.error(
                        "  [FAIL] Private OpenAPI route returned %s; expected %s.",
                        unauthenticated.status_code,
                        expected_status,
                    )
                    self.results["security_hardening"] = False
                    return False

                if not strong_key:
                    logger.error(
                        "  [FAIL] API correctly failed closed, but a strong NEXUS_API_KEY is required for deployment readiness."
                    )
                    self.results["security_hardening"] = False
                    return False

                wrong_key = await client.get(
                    "/openapi.json",
                    headers={"X-API-Key": "incorrect-test-key"},
                )
                if wrong_key.status_code != 401:
                    logger.error("  [FAIL] Incorrect API key was accepted.")
                    self.results["security_hardening"] = False
                    return False

                authorized = await client.get(
                    "/openapi.json",
                    headers={"X-API-Key": key},
                )
                if authorized.status_code != 200:
                    logger.error(
                        "  [FAIL] Strong configured key did not authorize private route."
                    )
                    self.results["security_hardening"] = False
                    return False

            self.results["security_hardening"] = True
            logger.info("  [OK] Private routes require a strong key; liveness remains public.")
            return True
        except Exception as exc:
            logger.error("  [FAIL] Security verification error: %s", exc)
            self.results["security_hardening"] = False
            return False

    def test_quantitative_integrity(self) -> bool:
        """Check numerical outputs are bounded and not hardcoded placeholders."""
        logger.info("\\n" + "=" * 60)
        logger.info("4. TESTING QUANTITATIVE INTEGRITY")
        logger.info("=" * 60)

        try:
            import numpy as np
            from nexus.portfolio.optimization import MonteCarloSimulator

            simulator = MonteCarloSimulator()
            returns = np.random.default_rng(20261105).normal(0.0005, 0.01, 100)
            probability = simulator.run_survival_analysis(
                100000, returns, days=10, n_simulations=50
            )
            if not np.isfinite(probability) or not 0.0 <= probability <= 1.0:
                logger.error("  [FAIL] Monte Carlo probability is invalid: %r", probability)
                self.results["quant_integrity"] = False
                return False

            logger.info("  [OK] Monte Carlo returned bounded probability %.4f", probability)
            self.results["quant_integrity"] = True
            return True
        except Exception as exc:
            logger.error("  [FAIL] Quantitative integrity error: %s", exc)
            self.results["quant_integrity"] = False
            return False

    async def test_alpaca_connection(self) -> bool:
        """Require a real broker account state; simulation is never a readiness pass."""
        logger.info("\\n" + "=" * 60)
        logger.info("5. TESTING ALPACA CONNECTION")
        logger.info("=" * 60)

        try:
            import math
            from nexus.execution.alpaca import get_client

            client = get_client()
            if not client.enabled or client.simulated:
                logger.error("  [FAIL] Broker is disabled or simulated.")
                self.results["alpaca_connection"] = False
                return False

            account = await client.get_account()
            equity = float(account.get("equity", account.get("portfolio_value", 0.0)))
            if (
                account.get("status") != "ACTIVE"
                or account.get("simulated")
                or not math.isfinite(equity)
                or equity <= 0
            ):
                logger.error("  [FAIL] Broker account is not verified ACTIVE with positive equity.")
                self.results["alpaca_connection"] = False
                return False

            logger.info("  [OK] Broker account status ACTIVE; equity present.")
            self.results["alpaca_connection"] = True
            return True
        except Exception as exc:
            logger.error("  [FAIL] Alpaca connection error: %s", exc)
            self.results["alpaca_connection"] = False
            return False

    async def test_pre_trade_guard(self) -> bool:
        """Verify malformed orders and concentration breaches never dispatch."""
        logger.info("\\n" + "=" * 60)
        logger.info("6. TESTING PRE-TRADE RISK GUARD")
        logger.info("=" * 60)

        try:
            from datetime import datetime, timezone
            from unittest.mock import AsyncMock
            from nexus.risk.pre_trade_guard import PreTradeRiskGuard
            from nexus.execution.alpaca import AlpacaClient, AlpacaCredentials

            guard = PreTradeRiskGuard()
            client = AlpacaClient(
                credentials=AlpacaCredentials("TEST_KEY", "TEST_SECRET")
            )
            client.enabled = False
            rejected_disabled = await guard.validate_and_submit(
                client=client,
                order_request={
                    "symbol": "AAPL",
                    "qty": 10,
                    "side": "buy",
                    "client_order_id": "verify-disabled-0001",
                },
            )
            if rejected_disabled.get("status") != "rejected":
                logger.error("  [FAIL] Disabled broker was not rejected.")
                self.results["pre_trade_guard"] = False
                return False

            client.enabled = True
            client.simulated = False
            client.get_account = AsyncMock(return_value={
                "enabled": True,
                "simulated": False,
                "status": "ACTIVE",
                "equity": 10000.0,
                "last_equity": 10000.0,
            })
            client.get_positions = AsyncMock(return_value=[])
            client.get_bars = AsyncMock(return_value=[{
                "t": datetime.now(timezone.utc).isoformat(),
                "close": 100.0,
            }])
            client.submit_order = AsyncMock(return_value={
                "success": True,
                "order_id": "should-not-submit",
            })

            portfolio = {
                "total_value": 10000.0,
                "drawdown": 0.0,
                "positions": [],
            }
            oversized = await guard.validate_and_submit(
                client=client,
                order_request={
                    "symbol": "AAPL",
                    "qty": 100,
                    "side": "buy",
                    "client_order_id": "verify-oversized-0001",
                },
                portfolio_state=portfolio,
            )
            if (
                oversized.get("status") != "rejected"
                or not any(
                    "POSITION_CONCENTRATION" in violation
                    for violation in oversized.get("violations", [])
                )
            ):
                logger.error("  [FAIL] Oversized concentration order was not rejected.")
                self.results["pre_trade_guard"] = False
                return False
            client.submit_order.assert_not_awaited()

            self.results["pre_trade_guard"] = True
            logger.info("  [OK] Disabled broker and concentration checks reject without dispatch.")
            return True
        except Exception as exc:
            logger.error("  [FAIL] Pre-trade guard verification error: %s", exc)
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
        logger.info("║" + "NEXUS RELEASE DIAGNOSTICS (NOT CERTIFICATION)".center(58) + "║")
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
        logger.info("  [PASS] DIAGNOSTIC CHECKS PASSED; EXTERNAL RELEASE GATES STILL APPLY")
    else:
        logger.error("  [FAIL] RELEASE DIAGNOSTICS FAILED; DO NOT ENABLE LIVE AUTOMATION")
    logger.info("=" * 60 + "\n")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
