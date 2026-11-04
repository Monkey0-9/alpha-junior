"""Production-safety regression tests. No test submits a real broker order."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import httpx
import numpy as np
import pandas as pd
import pytest

from nexus.execution.halt_state import ExecutionHaltState
from nexus.portfolio.optimization import KellyCriterionSizer, PortfolioOptimizer
from nexus.utils.polyglot_bridge import PolyglotBridge


@pytest.mark.asyncio
async def test_api_private_routes_fail_closed_and_health_is_liveness(monkeypatch):
    monkeypatch.delenv("NEXUS_API_KEY", raising=False)
    from nexus.api.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        health = await client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "alive"

        no_key = await client.get("/openapi.json")
        assert no_key.status_code == 503

        monkeypatch.setenv("NEXUS_API_KEY", "strong-test-key-1234567890-abcdef")
        wrong_key = await client.get(
            "/openapi.json", headers={"X-API-Key": "wrong-key"}
        )
        assert wrong_key.status_code == 401

        valid_key = await client.get(
            "/openapi.json",
            headers={"X-API-Key": "strong-test-key-1234567890-abcdef"},
        )
        assert valid_key.status_code == 200


def test_execution_halt_is_persistent_and_requires_operator_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "nexus.execution.halt_state._STATE_PATH",
        tmp_path / "execution_halt.json",
    )
    assert ExecutionHaltState.is_halted() is False

    state = ExecutionHaltState.trip(
        "order submit timed out", symbol="AAPL", order_id="broker-order-123"
    )
    assert state["halted"] is True
    assert ExecutionHaltState.is_halted() is True

    with pytest.raises(ValueError):
        ExecutionHaltState.clear_after_reconciliation(
            operator="", reconciliation_reference=""
        )

    cleared = ExecutionHaltState.clear_after_reconciliation(
        operator="test-operator",
        reconciliation_reference="broker-export-2026-11-05",
    )
    assert cleared["halted"] is False
    assert ExecutionHaltState.is_halted() is False


@pytest.mark.asyncio
async def test_order_submit_timeout_persists_halt_and_does_not_disable_reads(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "nexus.execution.halt_state._STATE_PATH",
        tmp_path / "execution_halt.json",
    )
    from nexus.execution.alpaca import AlpacaClient, AlpacaCredentials

    class BrokenPost:
        async def __aenter__(self):
            raise aiohttp.ClientError("connection dropped after request write")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    client = AlpacaClient(
        credentials=AlpacaCredentials(api_key="test", api_secret="test")
    )
    client.enabled = True
    client.simulated = False
    client._get_session = AsyncMock()
    session = MagicMock()
    session.post.return_value = BrokenPost()
    client._get_session.return_value = session

    result = await client.submit_order("AAPL", 1, "buy")
    assert result["status"] == "SUBMISSION_UNKNOWN"
    assert result["reconciliation_required"] is True
    assert result["trading_halted"] is True
    assert client.enabled is True  # keep broker reads available for reconciliation
    assert client.trading_halted is True
    assert ExecutionHaltState.is_halted() is True

    blocked = await client.submit_order("AAPL", 1, "buy")
    assert blocked["status"] == "EXECUTION_HALTED"


@pytest.mark.asyncio
async def test_cancel_404_trips_halt_for_reconciliation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "nexus.execution.halt_state._STATE_PATH",
        tmp_path / "execution_halt.json",
    )
    from nexus.execution.alpaca import AlpacaClient, AlpacaCredentials

    client = AlpacaClient(
        credentials=AlpacaCredentials(api_key="test", api_secret="test")
    )
    client.enabled = True
    client.simulated = False
    response = MagicMock()
    response.status = 404
    response.json = AsyncMock(return_value={"message": "order not found"})
    session = MagicMock()
    session.delete.return_value = _AsyncResponse(response)
    client._get_session = AsyncMock(return_value=session)

    result = await client.cancel_order("missing-order")
    assert result["status"] == "ORDER_NOT_FOUND"
    assert result["reconciliation_required"] is True
    assert result["trading_halted"] is True
    assert ExecutionHaltState.is_halted() is True


class _AsyncResponse:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_polyglot_validator_rejects_nonfinite_and_missing_side(monkeypatch):
    monkeypatch.delenv("NEXUS_STRICT_POLYGLOT", raising=False)
    valid_base = {"symbol": "AAPL", "qty": 1, "price": 100, "side": "buy"}
    assert PolyglotBridge.validate_order_zig(valid_base)["valid"] is True

    bad_side = {**valid_base, "side": "short"}
    assert PolyglotBridge.validate_order_zig(bad_side)["valid"] is False

    bad_qty = {**valid_base, "qty": float("nan")}
    assert PolyglotBridge.validate_order_zig(bad_qty)["valid"] is False

    bad_price = {**valid_base, "price": float("inf")}
    assert PolyglotBridge.validate_order_zig(bad_price)["valid"] is False

    missing_side = {"symbol": "AAPL", "qty": 1, "price": 100}
    assert PolyglotBridge.validate_order_zig(missing_side)["valid"] is False


def test_rust_risk_failure_is_unavailable_not_zero(monkeypatch):
    monkeypatch.setattr(
        "nexus.utils.polyglot_bridge.shutil.which", lambda _: None
    )
    result = PolyglotBridge.calculate_risk_rust([0.01, -0.02, 0.005])
    assert result["status"] == "RUST_NOT_INSTALLED"
    assert result["var"] is None
    assert result["expected_shortfall"] is None


def test_invalid_kelly_inputs_never_allocate_capital():
    sizer = KellyCriterionSizer()
    assert sizer.size_position(0.6, 0.02, 0.0) == 0.0
    assert sizer.size_position(float("nan"), 0.02, 0.01) == 0.0
    assert sizer.size_position(0.6, float("inf"), 0.01) == 0.0


def test_portfolio_optimizer_rejects_malformed_signal_vector():
    optimizer = PortfolioOptimizer()
    assert optimizer.optimize_weights(["AAPL", "MSFT"], [1.0]) == {}
    assert optimizer.optimize_weights(["AAPL", "AAPL"], [0.5, 0.8]) == {}
    assert optimizer.optimize_weights(["AAPL"], [float("nan")]) == {}


def test_alpha_normalizer_rejects_missing_ohlcv_columns():
    from nexus.core.alpha import AlphaEngine

    engine = AlphaEngine()
    malformed = pd.DataFrame({"close": [100.0, 101.0], "volume": [1000, 1100]})
    result = engine._normalize_columns(malformed, limit=10)
    assert result.empty
    assert {"open", "high", "low", "close", "volume"}.issubset(result.columns)


@pytest.mark.asyncio
async def test_engine_order_404_trips_persistent_halt_and_keeps_tracking(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "nexus.execution.halt_state._STATE_PATH",
        tmp_path / "execution_halt.json",
    )
    from nexus.core.engine import NexusEngine

    engine = NexusEngine()
    engine._active_orders = {"AAPL": "missing-order"}
    client = MagicMock()
    response = MagicMock()
    response.status_code = 404
    client.get = AsyncMock(return_value=response)
    engine._get_client = AsyncMock(return_value=client)
    engine.health_monitor.record = MagicMock()
    engine.health_monitor.heartbeat = MagicMock()

    await engine._verify_order_fills()
    assert engine._active_orders["AAPL"] == "missing-order"
    assert ExecutionHaltState.is_halted() is True
    await engine.close()
