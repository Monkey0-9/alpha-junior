import logging
import numpy as np
from scipy.stats import norm, skew, kurtosis  # type: ignore[import-untyped]
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class RiskEngine:
    def __init__(self, confidence_level: float = 0.95, ewma_lambda: float = 0.94):
        self.confidence_level = confidence_level
        self.ewma_lambda = ewma_lambda

    def calculate_ewma_covariance(self, returns: np.ndarray) -> float:
        if len(returns) < 2:
            return 0.0
        n = len(returns)
        weights = np.array(
            [
                (1 - self.ewma_lambda) * (self.ewma_lambda ** (n - 1 - i))
                for i in range(n)
            ]
        )
        weights /= weights.sum()
        mean = np.average(returns, weights=weights)
        variance = np.average((returns - mean) ** 2, weights=weights)
        return float(variance)

    def calculate_historical_var(self, returns: np.ndarray) -> float:
        if len(returns) == 0:
            return 0.0
        return float(np.percentile(returns, (1 - self.confidence_level) * 100))

    def calculate_var(self, returns: np.ndarray, method: str = "historical") -> float:
        if method == "cornish_fisher":
            return self.calculate_cornish_fisher_var(returns)
        elif method == "parametric":
            return self.calculate_parametric_var(returns)
        elif method == "ewma":
            return self.calculate_ewma_var(returns)
        return self.calculate_historical_var(returns)

    def calculate_parametric_var(self, returns: np.ndarray) -> float:
        if len(returns) == 0:
            return 0.0
        mu = np.mean(returns)
        sigma = np.std(returns, ddof=1)
        return float(norm.ppf(1 - self.confidence_level, mu, sigma))

    def calculate_cornish_fisher_var(self, returns: np.ndarray) -> float:
        if len(returns) < 5:
            return self.calculate_parametric_var(returns)
        mu = np.mean(returns)
        sigma = np.std(returns, ddof=1)
        sk = skew(returns)
        ku = kurtosis(returns, fisher=True)
        z = norm.ppf(1 - self.confidence_level)
        z_cf = (
            z
            + (sk / 6) * (z**2 - 1)
            + (ku / 24) * (z**3 - 3 * z)
            - (sk**2 / 36) * (2 * z**3 - 5 * z)
        )
        return float(mu + z_cf * sigma)

    def calculate_ewma_var(self, returns: np.ndarray) -> float:
        if len(returns) < 2:
            return self.calculate_parametric_var(returns)
        ewma_var = self.calculate_ewma_covariance(returns)
        z = norm.ppf(1 - self.confidence_level)
        return float(z * np.sqrt(ewma_var))

    def calculate_monte_carlo_var(
        self, returns: np.ndarray, num_paths: int = 10000, horizon: int = 20
    ) -> float:
        if len(returns) < 2:
            return self.calculate_parametric_var(returns)
        daily_returns = returns.astype(float)
        try:
            import sys
            import os

            mingw_bin = os.path.expanduser(r"~\scoop\apps\mingw\current\bin")
            if os.path.exists(mingw_bin):
                if hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(mingw_bin)
                else:
                    os.environ["PATH"] = mingw_bin + os.pathsep + os.environ["PATH"]
            cpp_dir = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "cpp_extensions"
            )
            if cpp_dir not in sys.path:
                sys.path.append(cpp_dir)
            import nexus_cpp

            return float(
                nexus_cpp.calculate_monte_carlo_var(
                    daily_returns.tolist(),
                    num_paths,
                    horizon,
                    self.confidence_level,
                )
            )
        except ImportError:
            simulated_end = np.array(
                [
                    np.sum(np.random.choice(daily_returns, size=horizon, replace=True))
                    for _ in range(num_paths)
                ]
            )
            return float(
                np.percentile(simulated_end, (1 - self.confidence_level) * 100)
            )

    def calculate_cvar(self, returns: np.ndarray, method: str = "historical") -> float:
        if len(returns) == 0:
            return 0.0
        if method == "cornish_fisher":
            var = self.calculate_cornish_fisher_var(returns)
        elif method == "ewma":
            var = self.calculate_ewma_var(returns)
        else:
            var = self.calculate_historical_var(returns)
        tail_losses = returns[returns <= var]
        if len(tail_losses) == 0:
            return var
        return float(np.mean(tail_losses))

    def calculate_expected_shortfall(
        self, returns: np.ndarray, confidence: Optional[float] = None
    ) -> float:
        conf = confidence or self.confidence_level
        var = np.percentile(returns, (1 - conf) * 100)
        tail = returns[returns <= var]
        return float(np.mean(tail)) if len(tail) > 0 else var

    def calculate_tail_risk(self, returns: np.ndarray, tail_pct: float = 0.01) -> float:
        if len(returns) == 0:
            return 0.0
        return float(np.percentile(returns, tail_pct * 100))

    def calculate_extreme_value_var(
        self, returns: np.ndarray, threshold: Optional[float] = None
    ) -> float:
        if len(returns) < 20:
            return self.calculate_historical_var(returns)
        threshold = threshold or float(np.percentile(np.abs(returns), 90))
        extremes = returns[returns < -threshold]
        if len(extremes) < 3:
            return self.calculate_historical_var(returns)
        xi = len(extremes) / len(returns)
        tail_mean = np.mean(extremes)
        var_pot = tail_mean + (threshold / xi) * (
            ((1 - self.confidence_level) / xi) ** (-xi) - 1
        )
        return float(var_pot)

    def stress_test(
        self,
        returns: np.ndarray,
        scenarios: Optional[Dict[str, float]] = None,
        seed: Optional[int] = 42,
    ) -> Dict[str, float]:
        """
        Executes calibrated historical crisis stress tests with deterministic reproducibility.
        Simulates tail shocks and volatility expansions corresponding to documented market crises.
        """
        if isinstance(returns, (list, tuple)):
            returns = np.array(returns, dtype=float)
        elif isinstance(returns, dict):
            returns = np.array(list(returns.values()), dtype=float)

        if returns is None or len(returns) == 0:
            return {"base_var": 0.0, "base_cvar": 0.0}

        base_var = self.calculate_historical_var(returns)
        results = {
            "base_var": base_var,
            "base_cvar": self.calculate_cvar(returns),
        }

        # Calibrated benchmark shock magnitudes
        default_scenarios = {
            "2008_crisis": -0.205,      # Lehman liquidity collapse
            "covid_crash": -0.124,      # March 2020 systemic selloff
            "flash_crash": -0.086,      # May 2010 microstructure gap
            "rate_hike_shock": -0.052,  # 2022 rapid monetary tightening
            "mild_correction": -0.030,  # Standard cyclical pullback
        }
        active_scenarios = scenarios or default_scenarios

        rng = np.random.default_rng(seed) if seed is not None else np.random.default_rng()

        for name, shock in active_scenarios.items():
            # Apply deterministic mean shift plus calibrated tail volatility multiplier
            vol_multiplier = 1.0 + abs(shock) * 4.0
            demeaned = returns - np.mean(returns)
            noise = rng.normal(0.0, 0.005, len(returns)) if seed is not None else 0.0
            stressed = shock + (demeaned * vol_multiplier) + noise
            results[f"var_{name}"] = round(self.calculate_historical_var(stressed), 6)
            results[f"cvar_{name}"] = round(self.calculate_cvar(stressed), 6)

        return results

    def assess_risk(self, returns: np.ndarray) -> Dict[str, float]:
        if len(returns) == 0:
            return {
                "var": 0.0,
                "cvar": 0.0,
                "volatility": 0.0,
                "sharpe": 0.0,
                "sortino": 0.0,
                "tail_risk": 0.0,
            }
        volatility = float(np.std(returns, ddof=1))
        downside = returns[returns < 0]
        downside_std = float(np.std(downside, ddof=1)) if len(downside) > 0 else 0.0
        mean_ret = float(np.mean(returns))
        skewness = float(skew(returns)) if len(returns) > 2 else 0.0
        kurt = float(kurtosis(returns, fisher=True)) if len(returns) > 3 else 0.0
        return {
            "var": self.calculate_historical_var(returns),
            "parametric_var": self.calculate_parametric_var(returns),
            "cornish_fisher_var": self.calculate_cornish_fisher_var(returns),
            "ewma_var": self.calculate_ewma_var(returns),
            "cvar": self.calculate_cvar(returns),
            "expected_shortfall": self.calculate_expected_shortfall(returns),
            "extreme_value_var": self.calculate_extreme_value_var(returns),
            "volatility": volatility,
            "annualized_vol": volatility * np.sqrt(252),
            "sharpe": (
                float(mean_ret / volatility * np.sqrt(252)) if volatility > 0 else 0.0
            ),
            "sortino": (
                float(mean_ret / downside_std * np.sqrt(252))
                if downside_std > 0
                else 0.0
            ),
            "tail_risk": self.calculate_tail_risk(returns),
            "skewness": skewness,
            "kurtosis": kurt,
        }

    def calculate_portfolio_risk(
        self, weights: np.ndarray, returns_matrix: np.ndarray
    ) -> Dict[str, float]:
        """
        Calculates multi-asset portfolio-level VaR, CVaR, portfolio volatility,
        and diversification ratio from asset returns matrix and allocated weights.
        """
        if len(returns_matrix) == 0 or len(weights) == 0:
            return {
                "portfolio_var": 0.0,
                "portfolio_cvar": 0.0,
                "portfolio_vol": 0.0,
                "diversification_ratio": 1.0,
            }

        w = np.array(weights, dtype=np.float64)
        if abs(np.sum(w) - 1.0) > 1e-4 and np.sum(w) > 0:
            w = w / np.sum(w)

        # Portfolio return series: R_p = X * w
        port_returns = returns_matrix @ w
        port_vol = (
            float(np.std(port_returns, ddof=1) * np.sqrt(252))
            if len(port_returns) > 1
            else 0.0
        )

        port_var = self.calculate_historical_var(port_returns)
        port_cvar = self.calculate_cvar(port_returns)

        asset_vols = (
            np.std(returns_matrix, axis=0) * np.sqrt(252)
            if returns_matrix.ndim > 1
            else np.std(returns_matrix) * np.sqrt(252)
        )
        weighted_vol = (
            float(np.sum(w * asset_vols)) if returns_matrix.ndim > 1 else port_vol
        )
        div_ratio = float(weighted_vol / max(port_vol, 1e-6)) if port_vol > 0 else 1.0

        return {
            "portfolio_var": round(port_var, 6),
            "portfolio_cvar": round(port_cvar, 6),
            "portfolio_vol": round(port_vol, 6),
            "diversification_ratio": round(div_ratio, 4),
        }
