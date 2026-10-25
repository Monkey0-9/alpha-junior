import os
import logging
import pandas as pd
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class ParquetDataStore:
    """
    High-performance Parquet-backed data store for quantitative research
    and deep historical bar storage.
    """

    def __init__(self, store_dir: str = "data_cache/parquet"):
        self.store_dir = store_dir
        if not os.path.exists(self.store_dir):
            os.makedirs(self.store_dir, exist_ok=True)

    def _get_path(self, symbol: str, timeframe: str = "1D") -> str:
        clean_sym = symbol.replace("^", "").replace(".", "_").upper()
        clean_tf = timeframe.upper()
        return os.path.join(self.store_dir, f"{clean_sym}_{clean_tf}.parquet")

    def save_bars(self, symbol: str, df: pd.DataFrame, timeframe: str = "1D") -> bool:
        """Saves or appends historical bar data to a Parquet file."""
        if df.empty:
            return False

        path = self._get_path(symbol, timeframe)
        try:
            df_save = df.copy()
            if not isinstance(df_save.index, pd.DatetimeIndex):
                if "timestamp" in df_save.columns:
                    df_save["timestamp"] = pd.to_datetime(df_save["timestamp"])
                    df_save.set_index("timestamp", inplace=True)
                elif "date" in df_save.columns:
                    df_save["date"] = pd.to_datetime(df_save["date"])
                    df_save.set_index("date", inplace=True)

            if os.path.exists(path):
                existing = pd.read_parquet(path)
                combined = pd.concat([existing, df_save])
                combined = combined[
                    ~combined.index.duplicated(keep="last")
                ].sort_index()
                combined.to_parquet(path, compression="snappy")
            else:
                df_save.sort_index().to_parquet(path, compression="snappy")

            logger.info(
                "Saved %d bars for %s (%s) to Parquet.", len(df_save), symbol, timeframe
            )
            return True
        except Exception as exc:
            logger.error("Failed to save Parquet data for %s: %s", symbol, exc)
            return False

    def load_bars(
        self,
        symbol: str,
        timeframe: str = "1D",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """Loads historical bars from Parquet storage with optional date filtering."""
        path = self._get_path(symbol, timeframe)
        if not os.path.exists(path):
            return pd.DataFrame()

        try:
            df = pd.read_parquet(path)
            if start_date:
                df = df.loc[df.index >= pd.to_datetime(start_date)]
            if end_date:
                df = df.loc[df.index <= pd.to_datetime(end_date)]
            if limit and len(df) > limit:
                df = df.iloc[-limit:]
            return df
        except Exception as exc:
            logger.error("Failed to load Parquet data for %s: %s", symbol, exc)
            return pd.DataFrame()

    def list_available_symbols(self) -> List[Dict[str, Any]]:
        """Lists all symbols stored in the Parquet data lake."""
        symbols: List[Dict[str, Any]] = []
        if not os.path.exists(self.store_dir):
            return symbols

        for fname in os.listdir(self.store_dir):
            if fname.endswith(".parquet"):
                parts = fname.replace(".parquet", "").split("_")
                sym = parts[0]
                tf = parts[1] if len(parts) > 1 else "1D"
                full_path = os.path.join(self.store_dir, fname)
                size_kb = round(os.path.getsize(full_path) / 1024.0, 2)
                symbols.append({"symbol": sym, "timeframe": tf, "size_kb": size_kb})
        return symbols
