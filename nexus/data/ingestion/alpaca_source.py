import pandas as pd
from typing import List, Optional, Any
from datetime import datetime
import logging
from .base import DataSource

try:
    from alpaca.data.historical import (
        CryptoHistoricalDataClient,
        StockHistoricalDataClient,
    )
    from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
except ImportError:
    CryptoHistoricalDataClient = None  # type: ignore[assignment, misc]
    StockHistoricalDataClient = None  # type: ignore[assignment, misc]
    CryptoBarsRequest = None  # type: ignore[assignment, misc]
    StockBarsRequest = None  # type: ignore[assignment, misc]
    TimeFrame = None  # type: ignore[assignment, misc]

logger = logging.getLogger(__name__)


class AlpacaSource(DataSource):
    """
    Alpaca data ingestion source using alpaca-py.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        is_crypto: bool = False,
    ):
        if StockHistoricalDataClient is None:
            raise ImportError("alpaca-py is required to use AlpacaSource.")

        self.is_crypto = is_crypto
        self.client: Any
        if is_crypto:
            self.client = CryptoHistoricalDataClient(api_key, secret_key)
        else:
            self.client = StockHistoricalDataClient(api_key, secret_key)

    @property
    def source_name(self) -> str:
        return "alpaca"

    def fetch_historical_ohlcv(
        self,
        symbols: List[str],
        start_date: datetime,
        end_date: datetime,
        interval: str = "1d",
    ) -> pd.DataFrame:
        if not symbols:
            return pd.DataFrame()

        # Map interval string to Alpaca TimeFrame
        if interval == "1d":
            timeframe = TimeFrame.Day
        elif interval == "1m":
            timeframe = TimeFrame.Minute
        elif interval == "1h":
            timeframe = TimeFrame.Hour
        else:
            raise ValueError(f"Unsupported interval for Alpaca: {interval}")

        logger.info(f"Fetching data from Alpaca for {len(symbols)} symbols...")

        request_params = {
            "symbol_or_symbols": symbols,
            "start": start_date,
            "end": end_date,
            "timeframe": timeframe,
        }

        try:
            bars: Any = None
            if self.is_crypto:
                req_crypto = CryptoBarsRequest(**request_params)
                bars = self.client.get_crypto_bars(req_crypto)
            else:
                req_stock = StockBarsRequest(**request_params)
                bars = self.client.get_stock_bars(req_stock)
        except Exception as e:
            logger.error(f"Failed to fetch data from Alpaca: {e}")
            raise

        if not bars or not getattr(bars, "data", None):
            return pd.DataFrame()

        df = getattr(bars, "df", pd.DataFrame()).reset_index()

        # Rename standard columns
        rename_map = {
            "timestamp": "timestamp",
            "symbol": "symbol",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
        }
        df = df.rename(columns=rename_map)

        # Convert timestamp to UTC standard
        if df["timestamp"].dt.tz is None:
            df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")
        else:
            df["timestamp"] = df["timestamp"].dt.tz_convert("UTC")

        # Reorder columns
        canonical_cols = [
            "timestamp",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
        available_cols = [c for c in canonical_cols if c in df.columns]
        df = df[available_cols]

        return df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
