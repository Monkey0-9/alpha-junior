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
async def test_order_submission_timeout_marks_state_unknown_and_disables_client():
    from unittest.mock import MagicMock, AsyncMock
    import aiohttp
    from nexus.execution.alpaca import AlpacaClient, AlpacaCredentials

    class BrokenPost:
        async def __aenter__(self):
            raise aiohttp.ClientError("connection dropped after request write")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    client = AlpacaClient(
        credentials=AlpacaCredentials(api_key="test", api_secret="test")
    )
    client.simulated = False
    mock_session = MagicMock()
    mock_session.post.return_value = BrokenPost()
    client._get_session = AsyncMock(return_value=mock_session)

    result = await client.submit_order("AAPL", 1, "buy")
    assert result["status"] == "SUBMISSION_UNKNOWN"
    assert result["reconciliation_required"] is True
    assert result["trading_halted"] is True
    assert client.enabled is False
    assert client.simulated is False


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
