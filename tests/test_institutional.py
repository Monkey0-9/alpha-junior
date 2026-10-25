import pandas as pd
from nexus.audit.governance import GovernanceEngine
from nexus.portfolio.optimization import PortfolioOptimizer, MultiFactorEngine


def test_governance_concentration() -> None:
    engine = GovernanceEngine(single_position_limit=0.1)
    portfolio = {"total_value": 100000, "drawdown": 0.02}

    # Within limit
    trade = {
        "symbol": "AAPL",
        "qty": 10,
        "price": 150,
        "side": "buy",
    }  # $1500 = 1.5%
    approved, violations = engine.check_compliance(trade, portfolio)
    assert approved is True

    # Exceeds limit
    large_trade = {
        "symbol": "TSLA",
        "qty": 100,
        "price": 200,
        "side": "buy",
    }  # $20000 = 20%
    approved, violations = engine.check_compliance(large_trade, portfolio)
    assert approved is False
    assert any("POSITION_CONCENTRATION" in v for v in violations)


def test_governance_drawdown() -> None:
    engine = GovernanceEngine(max_drawdown_limit=0.1)
    trade = {"symbol": "AAPL", "qty": 1, "price": 150, "side": "buy"}

    # Healthy
    portfolio_healthy = {"total_value": 100000, "drawdown": 0.05}
    approved, _ = engine.check_compliance(trade, portfolio_healthy)
    assert approved is True

    # Breach
    portfolio_stressed = {"total_value": 100000, "drawdown": 0.12}
    approved, violations = engine.check_compliance(trade, portfolio_stressed)
    assert approved is False
    assert any("DRAWDOWN_BREACH" in v for v in violations)


def test_portfolio_optimizer() -> None:
    optimizer = PortfolioOptimizer()
    symbols = ["AAPL", "MSFT", "GOOGL"]
    signals = [0.8, 0.4, 0.9]

    weights = optimizer.optimize_weights(symbols, signals)
    # Kelly optimizer may drop low-conviction symbols below MIN_POSITION floor
    # — expected
    assert len(weights) >= 1
    # Strict institutional guarantee: every position <= MAX_POSITION and total <= 1.0
    for w in weights.values():
        assert w <= optimizer.MAX_POSITION + 1e-6
    assert sum(weights.values()) <= 1.0 + 1e-6

    # Strongest signal (GOOGL) should have highest weight among included
    # symbols
    assert "GOOGL" in weights
    if "AAPL" in weights:
        assert weights["GOOGL"] >= weights["AAPL"]
    # Verify MSFT (weakest signal) doesn't dominate
    if "MSFT" in weights and "GOOGL" in weights:
        assert weights["GOOGL"] >= weights["MSFT"]

    # Verify full allocation when 5+ high-conviction symbols are present
    full_symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "META"]
    full_signals = [0.9, 0.9, 0.9, 0.9, 0.9]
    full_weights = optimizer.optimize_weights(full_symbols, full_signals)
    assert len(full_weights) == 5
    for w in full_weights.values():
        assert w <= optimizer.MAX_POSITION + 1e-6
    assert abs(sum(full_weights.values()) - 1.0) < 1e-5


def test_factor_engine_ranking() -> None:
    engine = MultiFactorEngine()
    signals = {"AAPL": 0.5, "MSFT": -0.2, "TSLA": 0.8}
    # Constant data to focus on alpha signal dominance in this test
    hist_data = {
        "AAPL": pd.DataFrame({"close": [100.0] * 10}),
        "MSFT": pd.DataFrame({"close": [100.0] * 10}),
        "TSLA": pd.DataFrame({"close": [100.0] * 10}),
    }

    rankings = engine.rank_assets(signals, hist_data)
    assert list(rankings.keys())[0] == "TSLA"  # Highest signal should be first
    assert list(rankings.keys())[-1] == "MSFT"  # Lowest signal should be last
