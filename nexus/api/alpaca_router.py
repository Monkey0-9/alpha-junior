from fastapi import APIRouter, HTTPException, Depends
from typing import Optional, Dict, Any
from pydantic import BaseModel
import logging
from nexus.execution.alpaca import get_client, AlpacaClient
from nexus.utils.config import Config
from nexus.risk.pre_trade_guard import PreTradeRiskGuard

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alpaca", tags=["execution"])


class OrderRequest(BaseModel):
    symbol: str
    qty: float
    side: str
    asset_class: str = "equity"
    strategy: Optional[str] = None
    order_type: str = "market"
    time_in_force: str = "day"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    trail_price: Optional[float] = None
    trail_percent: Optional[float] = None
    client_order_id: Optional[str] = None
    extended_hours: bool = False


async def get_alpaca() -> AlpacaClient:
    client = get_client()
    if not client.enabled:
        raise HTTPException(
            status_code=503, detail="Alpaca execution is not configured."
        )
    return client


@router.get("/account")
async def get_account(
    client: AlpacaClient = Depends(get_alpaca),
) -> Dict[str, Any]:
    return await client.get_account()


@router.get("/positions")
async def get_positions(
    client: AlpacaClient = Depends(get_alpaca),
) -> Dict[str, Any]:
    positions = await client.get_positions()
    return {
        "status": "success",
        "count": len(positions),
        "positions": positions,
    }


@router.delete("/positions/{symbol}")
async def close_position(
    symbol: str, client: AlpacaClient = Depends(get_alpaca)
) -> Dict[str, Any]:
    return await client.close_position(symbol)


@router.get("/orders")
async def list_orders(
    status: str = "all",
    limit: int = 50,
    client: AlpacaClient = Depends(get_alpaca),
) -> Dict[str, Any]:
    orders = await client.get_orders(status=status, limit=limit)
    return {"status": "success", "count": len(orders), "orders": orders}


@router.get("/orders/{order_id}")
async def get_order_status(
    order_id: str, client: AlpacaClient = Depends(get_alpaca)
) -> Dict[str, Any]:
    orders = await client.get_orders(status="all")
    for order in orders:
        if order.get("id") == order_id:
            return order
    raise HTTPException(status_code=404, detail="Order not found")


@router.post("/order")
async def submit_order(
    order: OrderRequest, client: AlpacaClient = Depends(get_alpaca)
) -> Dict[str, Any]:
    guard = PreTradeRiskGuard.get_instance()
    order_dict = order.model_dump() if hasattr(order, "model_dump") else order.dict()
    result = await guard.validate_and_submit(client, order_dict)
    if result.get("status") == "rejected":
        raise HTTPException(
            status_code=403,
            detail=result.get("error", "Order rejected by pre-trade risk guard."),
        )
    return result


@router.post("/cancel")
async def cancel_order(
    order_id: str, client: AlpacaClient = Depends(get_alpaca)
) -> Dict[str, Any]:
    return await client.cancel_order(order_id)


@router.post("/buy")
async def buy_stock(
    symbol: str, qty: float, client: AlpacaClient = Depends(get_alpaca)
) -> Dict[str, Any]:
    guard = PreTradeRiskGuard.get_instance()
    result = await guard.validate_and_submit(
        client, {"symbol": symbol, "qty": qty, "side": "buy"}
    )
    if result.get("status") == "rejected":
        raise HTTPException(
            status_code=403,
            detail=result.get("error", "Order rejected by pre-trade risk guard."),
        )
    return result


@router.post("/sell")
async def sell_stock(
    symbol: str, qty: float, client: AlpacaClient = Depends(get_alpaca)
) -> Dict[str, Any]:
    guard = PreTradeRiskGuard.get_instance()
    result = await guard.validate_and_submit(
        client, {"symbol": symbol, "qty": qty, "side": "sell"}
    )
    if result.get("status") == "rejected":
        raise HTTPException(
            status_code=403,
            detail=result.get("error", "Order rejected by pre-trade risk guard."),
        )
    return result


@router.get("/bars")
async def get_bars(
    symbol: str,
    timeframe: str = "1Min",
    limit: int = 100,
    client: AlpacaClient = Depends(get_alpaca),
) -> Dict[str, Any]:
    bars = await client.get_bars(symbol, timeframe=timeframe, limit=limit)
    return {"status": "success", "symbol": symbol, "bars": bars}


@router.get("/assets")
async def get_assets(
    asset_class: str = "us_equity",
    status: str = "active",
    tradable: bool = True,
    limit: int = Config.MAX_UNIVERSE_ASSETS,
    client: AlpacaClient = Depends(get_alpaca),
) -> Dict[str, Any]:
    assets = await client.get_assets(
        asset_class=asset_class,
        status=status,
        tradable=tradable,
        page_size=limit,
    )
    symbols = [asset.get("symbol") for asset in assets if asset.get("symbol")]
    return {
        "status": "success",
        "count": len(symbols),
        "symbols": symbols,
        "assets": assets,
    }


@router.get("/screener/all")
async def get_universe(
    asset_class: str = "us_equity",
    status: str = "active",
    tradable: bool = True,
    limit: int = 1000,
    client: AlpacaClient = Depends(get_alpaca),
) -> Dict[str, Any]:
    assets = await client.get_assets(
        asset_class=asset_class,
        status=status,
        tradable=tradable,
        page_size=limit,
    )
    symbols = [asset.get("symbol") for asset in assets if asset.get("symbol")]
    return {"status": "success", "count": len(symbols), "symbols": symbols}


@router.get("/clock")
async def get_clock(
    client: AlpacaClient = Depends(get_alpaca),
) -> Dict[str, Any]:
    clock = await client.get_clock()
    return {"status": "success", "clock": clock}


@router.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "healthy"}
