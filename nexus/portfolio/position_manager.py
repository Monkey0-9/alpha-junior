import logging
from typing import Dict
import numpy as np
import pandas as pd
from nexus.utils.config import Config

logger = logging.getLogger(__name__)


class VolatilityRegimeAdapter:
    def __init__(self, window: int = 21) -> None:
        self.window = window
        self._vol_history: list[float] = []

    def update(self, returns: np.ndarray) -> str:
        if len(returns) < self.window:
            return "NORMAL"
        recent_vol = float(np.std(returns[-self.window :]))
        hist_vol = (
            float(np.std(returns)) if len(returns) > self.window * 3 else recent_vol
        )
        ratio = recent_vol / max(hist_vol, 1e-10)
        regime = "HIGH" if ratio > 1.5 else "LOW" if ratio < 0.6 else "NORMAL"
        self._vol_history.append(ratio)
        return regime

    def get_stop_multiplier(self, regime: str) -> float:
        mults = {"HIGH": 2.5, "NORMAL": 1.5, "LOW": 1.0}
        return mults.get(regime, 1.5)


class PositionManager:
    def __init__(self) -> None:
        self._watermarks: Dict[str, float] = {}
        self._entry_times: Dict[str, float] = {}
        self._vol_adapter = VolatilityRegimeAdapter()
        self._return_buffer: Dict[str, list[float]] = {}

    def _get_atr(self, history: pd.DataFrame, period: int = 14) -> float:
        if history.empty or len(history) < period:
            return 0.0
        high = history["high"].astype(float)
        low = history["low"].astype(float)
        close = history["close"].astype(float)
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])

    def evaluate_exit(
        self,
        symbol: str,
        current_price: float,
        avg_entry_price: float,
        pnl_pct: float,
        history: pd.DataFrame,
    ) -> bool:
        if current_price <= 0 or avg_entry_price <= 0:
            return False
        if symbol not in self._watermarks or current_price > self._watermarks[symbol]:
            self._watermarks[symbol] = current_price
        highest_price = self._watermarks[symbol]

        if symbol not in self._return_buffer:
            self._return_buffer[symbol] = []
        if not history.empty and len(history) > 1:
            ret = (
                float(history["close"].pct_change().dropna().iloc[-1])
                if len(history["close"].pct_change().dropna()) > 0
                else 0.0
            )
            self._return_buffer[symbol].append(ret)
            if len(self._return_buffer[symbol]) > 21:
                self._return_buffer[symbol] = self._return_buffer[symbol][-21:]

        vol_regime = "NORMAL"
        if len(self._return_buffer.get(symbol, [])) > 5:
            vol_regime = self._vol_adapter.update(np.array(self._return_buffer[symbol]))

        if pnl_pct <= Config.STOP_LOSS_THRESHOLD:
            logger.info(
                "Closing %s: Fixed Stop Loss Hit (%.2f%%)",
                symbol,
                pnl_pct * 100,
            )
            return True

        if pnl_pct >= Config.TAKE_PROFIT_THRESHOLD:
            logger.info(
                "Closing %s: Take Profit Reached (%.2f%%)",
                symbol,
                pnl_pct * 100,
            )
            return True

        peak_profit_pct = (highest_price - avg_entry_price) / avg_entry_price
        if peak_profit_pct >= Config.TRAILING_PROFIT_LOCK:
            current_profit_pct = (current_price - avg_entry_price) / avg_entry_price
            trailing_lock = (
                peak_profit_pct * 0.5
                if vol_regime != "HIGH"
                else peak_profit_pct * 0.35
            )
            if current_profit_pct < trailing_lock:
                logger.info(
                    "Closing %s: Trailing Profit Lock (peak=%.2f%%, now=%.2f%%)",
                    symbol,
                    peak_profit_pct * 100,
                    current_profit_pct * 100,
                )
                return True

        atr = self._get_atr(history)
        if atr > 0:
            stop_mult = self._vol_adapter.get_stop_multiplier(vol_regime)
            stop_level = highest_price - (atr * stop_mult * Config.ATR_STOP_MULTIPLIER)
            if current_price < stop_level:
                logger.info(
                    "Closing %s: ATR Trailing Stop (vol_regime=%s, mult=%.1f, curr=%.2f, stop=%.2f)",
                    symbol,
                    vol_regime,
                    stop_mult,
                    current_price,
                    stop_level,
                )
                return True

        if symbol not in self._entry_times:
            self._entry_times[symbol] = 0
        self._entry_times[symbol] += 1
        max_bars = 240 if vol_regime == "NORMAL" else 120
        if self._entry_times[symbol] > max_bars:
            logger.info(
                "Closing %s: Maximum holding period reached (%s bars)",
                symbol,
                max_bars,
            )
            return True

        return False

    def reset_watermark(self, symbol: str):
        self._watermarks.pop(symbol, None)
        self._entry_times.pop(symbol, None)
        self._return_buffer.pop(symbol, None)
