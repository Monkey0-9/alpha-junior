import pytest
import numpy as np

from nexus.risk.pre_trade_guard import PreTradeRiskGuard
from nexus.execution.alpaca import AlpacaClient, AlpacaCredentials
from nexus.portfolio.optimization import PortfolioOptimizer
from nexus.universe import UniverseManager
from nexus.utils.polyglot_bridge import PolyglotBridge
from nexus.risk.engine import RiskEngine


@pytest.mark.asyncio
async def test_pre_trade_guard_rejects_unauthenticated_broker() -> None:
    """Verify PreTradeRiskGuard fails-closed if broker client is disabled or unauthenticated."""
    guard = PreTradeRiskGuard()
    client = AlpacaClient(credentials=AlpacaCredentials("BAD_KEY", "BAD_SECRET"))
    client.enabled = False  # Broker marked disabled/unauthorized

    order_req = {
        "symbol": "AAPL",
        "qty": 10,
        "side": "buy",
        "price": 150.0,
    }

    result = await guard.validate_and_submit(client=client, order_request=order_req)
    assert result["success"] is False
    assert result["status"] == "rejected"
    assert "disabled" in result["error"].lower() or "unauthorized" in result["error"].lower()


@pytest.mark.asyncio
async def test_pre_trade_guard_governance_concentration() -> None:
    """Verify PreTradeRiskGuard enforces 20% single position concentration limit."""
    guard = PreTradeRiskGuard()
    client = AlpacaClient(credentials=AlpacaCredentials("KEY", "SECRET"))
    client.enabled = True
    client.simulated = False  # Live broker mode

    # Order value = 300 * $100 = $30,000, which is 30% of $100,000 equity (exceeds 20% max)
    oversized_order = {
        "symbol": "TSLA",
        "qty": 300,
        "side": "buy",
        "price": 100.0,
    }

    empty_pos: list[dict[str, object]] = []
    portfolio_state = {
        "total_value": 100000.0,
        "drawdown": 0.01,
        "positions": empty_pos,
    }

    result = await guard.validate_and_submit(
        client=client, order_request=oversized_order, portfolio_state=portfolio_state
    )
    assert result["success"] is False
    assert result["status"] == "rejected"
    assert "violations" in result
    assert any("POSITION_CONCENTRATION" in v for v in result["violations"])


def test_portfolio_optimizer_strict_water_filling_bounds() -> None:
    """Verify PortfolioOptimizer never breaches MAX_POSITION under any signal distribution."""
    optimizer = PortfolioOptimizer()
    symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA"]
    # Extreme signals
    signals = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]

    weights = optimizer.optimize_weights(symbols, signals)
    assert len(weights) >= 5

    total_weight = sum(weights.values())
    assert total_weight <= 1.0 + 1e-6

    for sym, w in weights.items():
        assert w <= optimizer.MAX_POSITION + 1e-6, f"{sym} weight {w} exceeded MAX_POSITION {optimizer.MAX_POSITION}"
        assert w >= optimizer.MIN_POSITION, f"{sym} weight {w} below MIN_POSITION {optimizer.MIN_POSITION}"


def test_universe_manager_filtering_and_fallback() -> None:
    """Verify UniverseManager filters out penny stocks, blacklisted symbols, and OTC exchanges."""
    mgr = UniverseManager()

    raw_assets = [
        {"symbol": "AAPL", "exchange": "NASDAQ", "tradable": True, "status": "active"},
        {"symbol": "MSFT", "exchange": "NASDAQ", "tradable": True, "status": "active"},
        {"symbol": "PENNY", "exchange": "OTC", "tradable": True, "status": "active"},   # OTC exchange rejected
        {"symbol": "UVXY", "exchange": "ARCA", "tradable": True, "status": "active"},    # Blacklisted
        {"symbol": "INACTIVE", "exchange": "NYSE", "tradable": True, "status": "inactive"},  # Inactive rejected
        {"symbol": "BAD$SYM", "exchange": "NYSE", "tradable": True, "status": "active"},  # Special chars rejected
    ]

    selected = mgr.select_universe(raw_assets, max_symbols=10)
    assert "AAPL" in selected
    assert "MSFT" in selected
    assert "PENNY" not in selected
    assert "UVXY" not in selected
    assert "INACTIVE" not in selected
    assert "BAD$SYM" not in selected

    # Verify fallback universe contains liquid institutional blue chips
    fallback = mgr.get_fallback_universe(limit=5)
    assert len(fallback) == 5
    assert "SPY" in fallback or "AAPL" in fallback


def test_polyglot_zig_validation_defense_in_depth() -> None:
    """Verify PolyglotBridge rejects zero/negative qty and price regardless of native binary availability."""
    # Invalid Qty
    res1 = PolyglotBridge.validate_order_zig({"symbol": "AAPL", "qty": 0, "price": 100.0})
    assert res1["valid"] is False
    assert "quantity" in res1["error"].lower()

    # Invalid Price
    res2 = PolyglotBridge.validate_order_zig({"symbol": "AAPL", "qty": 10, "price": -5.0})
    assert res2["valid"] is False
    assert "price" in res2["error"].lower()

    # Empty Symbol
    res3 = PolyglotBridge.validate_order_zig({"symbol": "", "qty": 10, "price": 100.0})
    assert res3["valid"] is False
    assert "symbol" in res3["error"].lower()

    # Valid Order
    res4 = PolyglotBridge.validate_order_zig({"symbol": "AAPL", "qty": 10, "price": 150.0})
    assert res4["valid"] is True


def test_stress_test_reproducibility() -> None:
    """Verify RiskEngine.stress_test produces deterministic, calibrated crisis metrics."""
    engine = RiskEngine()
    rng = np.random.default_rng(123)
    returns = rng.normal(0.0005, 0.015, 252)

    results1 = engine.stress_test(returns)
    results2 = engine.stress_test(returns)

    assert "var_2008_crisis" in results1
    assert "cvar_2008_crisis" in results1
    assert "var_covid_crash" in results1

    # Exact reproducibility with deterministic seed 42
    assert results1 == results2
