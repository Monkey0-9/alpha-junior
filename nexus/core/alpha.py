import sys
import os
import asyncio
import logging
import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Tuple, Optional

# Set DLL and C++ extension search path before native import
mingw_bin = os.path.expanduser(r"~\scoop\apps\mingw\current\bin")
if os.path.exists(mingw_bin):
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(mingw_bin)
    else:
        os.environ["PATH"] = mingw_bin + os.pathsep + os.environ["PATH"]

cpp_dir = os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "cpp_extensions")
)
if cpp_dir not in sys.path:
    sys.path.append(cpp_dir)

import nexus_cpp  # noqa: E402

from nexus.math.models import KalmanFilter  # noqa: E402
from nexus.math.indicators import HawkesProcess, compute_hurst_exponent  # noqa: E402
from nexus.execution.alpaca import get_client  # noqa: E402
from nexus.utils.config import Config  # noqa: E402
from nexus.core.sentiment import SentimentEngine  # noqa: E402


logger = logging.getLogger(__name__)

try:
    import pywt

    WAVELET_AVAILABLE = True
except ImportError:
    pywt = None  # type: ignore[assignment, misc]
    WAVELET_AVAILABLE = False

try:
    EMD_AVAILABLE = True
except ImportError:
    EMD_AVAILABLE = False


class AdaptiveKalmanFilter(KalmanFilter):
    def adapt_to_volatility(self, rolling_vol: float) -> None:
        self.process_variance = max(1e-7, rolling_vol**2 * 0.1)
        self.measurement_variance = max(1e-5, rolling_vol**2 * 5.0)


class CrossAssetAlphaModel:
    def __init__(self, lookback: int = 60):
        self.lookback = lookback
        self._correlation_matrix: Dict[str, Dict[str, float]] = {}

    def compute_pair_alpha(
        self,
        symbol: str,
        peer_symbols: List[str],
        prices: Dict[str, pd.Series],
    ) -> float:
        if len(peer_symbols) < 2 or symbol not in prices:
            return 0.0
        my_price = prices[symbol]
        peer_alpha = 0.0
        for peer in peer_symbols:
            if peer == symbol or peer not in prices:
                continue
            peer_p = prices[peer]
            aligned = pd.concat([my_price, peer_p], axis=1).dropna()
            if len(aligned) < self.lookback:
                continue
            my_ret = aligned.iloc[:, 0].pct_change().dropna().tail(self.lookback)
            peer_ret = aligned.iloc[:, 1].pct_change().dropna().tail(self.lookback)
            if len(my_ret) < 10 or len(peer_ret) < 10:
                continue
            corr = my_ret.corr(peer_ret)
            self._correlation_matrix.setdefault(symbol, {})[peer] = float(corr)
            my_momentum = float(my_ret.tail(5).mean())
            peer_momentum = float(peer_ret.tail(5).mean())
            if corr > 0.7:
                divergence = my_momentum - peer_momentum
                peer_alpha += np.tanh(divergence * 5) * 0.3
            elif corr < -0.3:
                divergence = my_momentum + peer_momentum
                peer_alpha += np.tanh(divergence * 3) * 0.2
        return float(np.clip(peer_alpha, -0.5, 0.5))


class WaveletDenoiser:
    def denoise(
        self, prices: np.ndarray, wavelet: str = "db4", level: int = 3
    ) -> np.ndarray:
        if not WAVELET_AVAILABLE or pywt is None or len(prices) < 2**level:
            return prices
        coeffs = pywt.wavedec(prices, wavelet, level=level)
        sigma = np.median(np.abs(coeffs[-1])) / 0.6745
        threshold = sigma * np.sqrt(2 * np.log(len(prices)))
        coeffs_thresh = [coeffs[0]] + [
            pywt.threshold(c, threshold, mode="soft") for c in coeffs[1:]
        ]
        return pywt.waverec(coeffs_thresh, wavelet)[: len(prices)]


class AlphaEngine:
    _cache: Dict[str, Dict[str, Any]] = {}
    _CACHE_TTL = 300

    def __init__(self, backend_url: str = Config.BACKEND_URL):
        self.kf = AdaptiveKalmanFilter()
        self.hawkes = HawkesProcess()
        self.backend_url = backend_url
        self.client = get_client()
        self.sentiment_engine = SentimentEngine()
        self.cross_asset = CrossAssetAlphaModel()
        self.wavelet = WaveletDenoiser()

    async def fetch_market_data(
        self, symbol: str, timeframe: str = "1Min", limit: int = 250
    ) -> pd.DataFrame:
        cache_key = f"{symbol}_{timeframe}_{limit}"
        now = time.time()
        entry = self._cache.get(cache_key)
        if entry and now - entry["timestamp"] < self._CACHE_TTL:
            return entry["data"]
        df = await self._fetch_with_backoff(symbol, timeframe, limit)
        if not df.empty:
            self._cache[cache_key] = {"timestamp": now, "data": df}
        return df

    async def _fetch_with_backoff(
        self, symbol: str, timeframe: str, limit: int
    ) -> pd.DataFrame:
        bars: List[Dict[str, Any]] = []
        for attempt in range(2):
            try:
                days_to_lookback = (limit // 390) + 3
                start_date = (
                    datetime.now(timezone.utc) - timedelta(days=days_to_lookback)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
                bars = await self.client.get_bars(
                    symbol, timeframe=timeframe, limit=limit, start=start_date
                )
                if len(bars) >= 20:
                    break
                if attempt == 0:
                    await asyncio.sleep(1)
            except Exception as e:
                logger.debug(
                    "Alpaca fetch failed for %s (Attempt %d): %s",
                    symbol,
                    attempt,
                    e,
                )
        df = None
        if bars and len(bars) >= 5:
            df = pd.DataFrame(bars)
            mapping = {
                "o": "open",
                "h": "high",
                "l": "low",
                "c": "close",
                "v": "volume",
            }
            df = df.rename(
                columns={k: v for k, v in mapping.items() if k in df.columns}
            )
        else:
            yf_logger: Optional[logging.Logger] = None
            old_yf_level: Optional[int] = None
            try:
                import yfinance as yf

                yf_logger = logging.getLogger("yfinance")
                old_yf_level = yf_logger.level
                yf_logger.setLevel(logging.CRITICAL)
                interval = (
                    "1d"
                    if timeframe == "1D"
                    else "15m" if timeframe == "15Min" else "1m"
                )
                df = yf.download(symbol, period="7d", interval=interval, progress=False)
                if getattr(df, "empty", True) and timeframe == "1Min":
                    df = yf.download(
                        symbol, period="7d", interval="15m", progress=False
                    )
            except Exception as e:
                logger.debug("yfinance fallback failed for %s: %s", symbol, e)
                return pd.DataFrame()
            finally:
                if yf_logger is not None and old_yf_level is not None:
                    yf_logger.setLevel(old_yf_level)
        if df is None or (hasattr(df, "empty") and df.empty):
            return pd.DataFrame()
        return self._normalize_columns(df, limit)

    def _normalize_columns(self, df: pd.DataFrame, limit: int) -> pd.DataFrame:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = pd.Index([str(c[0]).lower() for c in df.columns])
        else:
            df.columns = pd.Index([str(c).lower() for c in df.columns])
        rename_map = {"adj close": "close", "unadjusted close": "close"}
        for k, v in rename_map.items():
            if k in df.columns and v not in df.columns:
                df[v] = df[k]
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in df.columns:
                df[col] = df["close"] if "close" in df.columns else 0.0
            col_data = df[col]
            if isinstance(col_data, pd.DataFrame):
                df[col] = col_data.iloc[:, 0]
        return df.loc[:, ~df.columns.duplicated()].tail(limit)

    def generate_signal(
        self,
        data: pd.DataFrame,
        sentiment_score: float = 0.0,
        peer_data: Optional[Dict[str, pd.Series]] = None,
        peer_symbols: Optional[List[str]] = None,
        symbol: Optional[str] = None,
    ) -> float:
        if data.empty or "close" not in data.columns:
            return 0.0
        prices = data["close"].astype(float).to_numpy().flatten()
        if len(prices) > 5:
            rolling_vol = float(np.std(np.diff(prices[-20:]) / (prices[-20:-1] + 1e-9)))
            self.kf.adapt_to_volatility(rolling_vol)
        prices_denoised = self.wavelet.denoise(prices) if len(prices) > 32 else prices
        denoised_prices = self.kf.batch_filter(prices_denoised)
        if len(denoised_prices) < 5:
            return 0.0
        pct_changes = pd.Series(denoised_prices).pct_change().dropna()
        if pct_changes.empty:
            return 0.0
        trend = (
            float(denoised_prices[-1] / denoised_prices[-10] - 1)
            if len(denoised_prices) >= 10
            else 0.0
        )
        trend_score = float(np.tanh(trend * 20))
        momentum = float(pct_changes.tail(5).mean())
        volatility = (
            float(pct_changes.tail(20).std())
            if len(pct_changes) >= 20
            else float(pct_changes.std())
        )
        entropy_filter = self._compute_entropy_filter(pct_changes.to_numpy())
        momentum_score = float(np.tanh(momentum * 10)) * entropy_filter
        price_series = pd.Series(prices)
        hurst = compute_hurst_exponent(price_series)
        hurst_signal = self._hurst_gate(hurst, trend_score, momentum_score)
        vwap_signal = self._compute_vwap_signal(
            data, prices[-1] if len(prices) > 0 else 0.0
        )
        pct_arr = pct_changes.to_numpy()
        intensity = self.hawkes.calculate_intensity(pct_arr)
        hawkes_adj = 1.0 / (1.0 + intensity)
        vol_penalty = 1.0 / (1.0 + volatility * 8.0)
        sentiment_adj = 0.0
        if Config.SENTIMENT_ENABLED:
            sentiment_adj = sentiment_score * Config.SENTIMENT_WEIGHT
        cross_asset_alpha = 0.0
        target_sym = symbol or "SPY"
        if peer_data and peer_symbols:
            cross_asset_alpha = self.cross_asset.compute_pair_alpha(
                target_sym, peer_symbols, peer_data
            )
        base_signal = (
            (
                0.30 * hurst_signal
                + 0.25 * momentum_score
                + 0.20 * trend_score
                + 0.15 * vwap_signal
                + 0.10 * cross_asset_alpha
            )
            * vol_penalty
            * hawkes_adj
        )
        signal = base_signal * (1.0 - Config.SENTIMENT_WEIGHT) + sentiment_adj
        return float(np.clip(signal, -1.0, 1.0))

    def _compute_entropy_filter(self, returns: np.ndarray) -> float:
        if len(returns) < 5:
            return 0.6
        try:
            entropy = nexus_cpp.stats.compute_shannon_entropy(returns.tolist(), 10)
            clip_val = float(np.clip((entropy - 1.5) / 2.0, 0.0, 0.70))
            return 1.0 - clip_val
        except Exception:
            return 0.6

    def _hurst_gate(
        self, hurst: float, trend_score: float, momentum_score: float
    ) -> float:
        if hurst > 0.60:
            amplifier = min(1.5, 1.0 + (hurst - 0.60) * 3.0)
            return float(
                np.tanh((trend_score * 0.55 + momentum_score * 0.45) * amplifier)
            )
        elif hurst < 0.40:
            reverter = min(1.3, 1.0 + (0.40 - hurst) * 2.5)
            return float(np.tanh(-momentum_score * reverter))
        else:
            random_walk_suppressor = 1.0 - abs(hurst - 0.50) * 4.0
            return float(
                np.tanh((trend_score + momentum_score) * 0.5 * random_walk_suppressor)
            )

    def _compute_vwap_signal(self, data: pd.DataFrame, current_price: float) -> float:
        if "volume" not in data.columns or "close" not in data.columns or len(data) < 5:
            return 0.0
        try:
            close = data["close"].astype(float)
            volume = data["volume"].astype(float)
            typical_price = close
            if "high" in data.columns and "low" in data.columns:
                typical_price = (
                    data["high"].astype(float) + data["low"].astype(float) + close
                ) / 3.0
            vwap_vec = nexus_cpp.signals.compute_vwap(
                typical_price.tolist(), volume.tolist()
            )
            if not vwap_vec:
                return 0.0
            vwap_val = vwap_vec[-1]
            deviation = (current_price - vwap_val) / max(vwap_val, 1e-6)
            vol_avg = float(volume.tail(20).mean())
            vol_recent = float(volume.tail(5).mean())
            vol_ratio = vol_recent / max(vol_avg, 1.0)
            vol_confirmation = min(1.5, vol_ratio)
            return float(np.tanh(deviation * 15.0 * vol_confirmation))
        except Exception:
            return 0.0

    def monte_carlo_simulation(
        self, prices: np.ndarray, num_paths: int = 500, horizon: int = 20
    ) -> float:
        prices = prices.flatten()
        if len(prices) < 5:
            return 0.5
        returns = np.diff(np.log(prices)).astype(float)
        mu = float(np.mean(returns))
        sigma = float(np.std(returns)) if np.std(returns) > 1e-9 else 0.01
        last_price = float(prices[-1])
        success_count = 0
        half_paths = num_paths // 2
        rng = np.random.default_rng()
        for _ in range(half_paths):
            sampled = rng.normal(mu, sigma, horizon)
            antithetic = (2.0 * mu) - sampled
            for path in [sampled, antithetic]:
                final_price = last_price * np.exp(float(np.sum(path)))
                if final_price > last_price:
                    success_count += 1
        return float(success_count / num_paths)

    async def get_batch_signals(
        self, symbols: List[str], timeframe: str = "15Min"
    ) -> Dict[str, float]:
        signals: Dict[str, float] = {}
        semaphore = asyncio.Semaphore(2)

        async def symbol_signal(symbol: str) -> Tuple[str, float]:
            async with semaphore:
                data_15m = await self.fetch_market_data(symbol, timeframe="15Min")
                data_1m = await self.fetch_market_data(symbol, timeframe="1Min")
                data_1d = await self.fetch_market_data(
                    symbol, timeframe="1D", limit=100
                )
                sentiment = await self.sentiment_engine.get_sentiment(symbol)
                peer_symbols = [s for s in symbols if s != symbol][:5]
                peer_prices = {}
                for ps in peer_symbols:
                    d = await self.fetch_market_data(ps, timeframe="1D", limit=100)
                    if not d.empty:
                        peer_prices[ps] = d["close"]
                alpha_15m = (
                    self.generate_signal(
                        data_15m, sentiment, peer_prices, peer_symbols, symbol=symbol
                    )
                    if not data_15m.empty
                    else 0.0
                )
                alpha_1m = (
                    self.generate_signal(data_1m, sentiment, symbol=symbol)
                    if not data_1m.empty
                    else 0.0
                )
                alpha_1d = (
                    self.generate_signal(
                        data_1d, sentiment, peer_prices, peer_symbols, symbol=symbol
                    )
                    if not data_1d.empty
                    else 0.0
                )
                alpha = (
                    alpha_1m * Config.SIGNAL_1MIN_WEIGHT
                    + alpha_15m * Config.SIGNAL_15MIN_WEIGHT
                    + alpha_1d * Config.SIGNAL_1D_WEIGHT
                )
                if not data_15m.empty:
                    mc_prob = self.monte_carlo_simulation(
                        data_15m["close"].astype(float).to_numpy()
                    )
                    alpha = alpha * 0.65 + (mc_prob - 0.5) * 0.70
                return symbol, float(np.clip(alpha, -1.0, 1.0))

        results = await asyncio.gather(
            *[symbol_signal(s) for s in symbols], return_exceptions=True
        )
        for r in results:
            if isinstance(r, tuple):
                signals[r[0]] = r[1]
        return signals

    async def close(self) -> None:
        if hasattr(self.client, "close"):
            try:
                await self.client.close()
            except Exception as exc:
                logger.warning("Failed to close AlphaEngine client: %s", exc)
