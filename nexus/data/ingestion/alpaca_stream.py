import asyncio
import logging
from typing import List, Callable, Any
from .base import DataSource

try:
    from alpaca.data.live import StockDataStream
except ImportError:
    StockDataStream = None

logger = logging.getLogger(__name__)


class AlpacaStreamSource(DataSource):
    """
    Phase 2 Data Engine: Real-time WebSocket feed using Alpaca.
    Provides live tick data and minute bars.
    """

    def __init__(self, api_key: str = None, secret_key: str = None):
        if StockDataStream is None:
            raise ImportError("alpaca-py is required to use AlpacaStreamSource.")

        self.client = StockDataStream(api_key, secret_key)
        self.callbacks = []

    @property
    def source_name(self) -> str:
        return "alpaca_stream"

    def register_callback(self, callback: Callable[[Any], None]):
        """
        Register a callback to be called whenever a new bar or trade arrives.
        """
        self.callbacks.append(callback)

    async def _broadcast(self, data: Any):
        """
        Broadcast incoming data to all registered callbacks.
        """
        for cb in self.callbacks:
            if asyncio.iscoroutinefunction(cb):
                await cb(data)
            else:
                cb(data)

    def subscribe_bars(self, symbols: List[str]):
        """
        Subscribe to minute-level bars for the given symbols.
        """
        logger.info(f"Subscribing to minute bars for {len(symbols)} symbols...")
        self.client.subscribe_bars(self._broadcast, *symbols)

    def subscribe_trades(self, symbols: List[str]):
        """
        Subscribe to tick-level trades for the given symbols.
        """
        logger.info(f"Subscribing to live trades for {len(symbols)} symbols...")
        self.client.subscribe_trades(self._broadcast, *symbols)

    def start(self):
        """
        Start the WebSocket connection (blocking).
        """
        logger.info("Starting Alpaca Data Stream (WebSocket)...")
        self.client.run()

    async def start_async(self):
        """
        Start the WebSocket connection (async).
        """
        logger.info("Starting Alpaca Data Stream (WebSocket async)...")
        await self.client._run_forever()
