"""Regression tests for production safety invariants.

These tests use fakes and never submit orders to a real broker.
"""
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from nexus.utils.polyglot_bridge import PolyglotBridge


@pytest.mark.asyncio
async def test_private_read_fails_closed_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("NEXUS_API_KEY", raising=False)
    from nexus.api.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/monitor/brain")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_private_read_requires_key_even_for_get(monkeypatch):
    monkeypatch.setenv("NEXUS_API_KEY", "test-secret-key-for-unit-tests-123456")
    from nexus.api.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/monitor/brain")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_weak_api_key_fails_closed(monkeypatch):
    monkeypatch.setenv("NEXUS_API_KEY", "short")
    from nexus.api.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/monitor/brain")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_liveness_endpoint_is_public(monkeypatch):
    monkeypatch.delenv("NEXUS_API_KEY", raising=False)
    from nexus.api.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/monitor/health")
    assert response.status_code == 200


def test_order_validator_rejects_non_finite_or_invalid_values(monkeypatch):
    monkeypatch.delenv("NEXUS_STRICT_POLYGLOT", raising=False)
    invalid_orders = [
        {"symbol": "AAPL", "qty": float("nan"), "price": 100, "side": "buy"},
        {"symbol": "AAPL", "qty": float("inf"), "price": 100, "side": "buy"},
        {"symbol": "AAPL", "qty": 1, "price": float("nan"), "side": "buy"},
        {"symbol": "AAPL", "qty": 1, "price": 100, "side": "short"},
        {"symbol": " AAPL", "qty": 1, "price": 100, "side": "buy"},
        {"symbol": "AAPL", "qty": 1, "price": 100, "side": "buy", "order_type": "limit"},
    ]
    for order in invalid_orders:
        assert PolyglotBridge.validate_order_zig(order).get("valid") is False


@pytest.mark.asyncio
async def test_guard_rejects_unverified_account_state_without_submission(monkeypatch):
    monkeypatch.setattr("nexus.utils.config.Config.ALLOW_SIMULATION_FALLBACK", False)
    from nexus.risk.pre_trade_guard import PreTradeRiskGuard

    client = MagicMock()
    client.enabled = True
    client.simulated = False
    client.get_account = AsyncMock(
        return_value={"enabled": True, "simulated": False, "status": "ERROR", "error": "network"}
    )
    client.get_positions = AsyncMock(return_value=[])
    client.submit_order = AsyncMock(return_value={"success": True, "order_id": "should-not-exist"})

    guard = PreTradeRiskGuard(governance=MagicMock())
    result = await guard.validate_and_submit(
        client,
        {"symbol": "AAPL", "qty": 1, "price": 100, "side": "buy", "order_type": "market"},
    )

    assert result.get("status") == "rejected"
    client.submit_order.assert_not_awaited()
