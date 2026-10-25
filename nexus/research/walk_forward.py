import logging
import numpy as np
import pandas as pd
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class WalkForwardEvaluator:
    """
    Rigorously evaluates quantitative models using Walk-Forward Optimization
    (Train -> Validate -> Test -> Roll) to prevent lookahead bias and overfit.
    """

    def __init__(
        self,
        train_window: int = 252,
        val_window: int = 63,
        test_window: int = 63,
        step_size: int = 63,
    ):
        self.train_window = train_window
        self.val_window = val_window
        self.test_window = test_window
        self.step_size = step_size

    def generate_splits(self, df: pd.DataFrame) -> List[Dict[str, pd.DataFrame]]:
        """Splits time series into rolling train, validation, and test windows."""
        splits: List[Dict[str, pd.DataFrame]] = []
        n_samples = len(df)
        min_required = self.train_window + self.val_window + self.test_window

        if n_samples < min_required:
            logger.warning(
                "Data size (%d) insufficient for full walk-forward split (min %d).",
                n_samples,
                min_required,
            )
            return splits

        start_idx = 0
        while start_idx + min_required <= n_samples:
            t_end = start_idx + self.train_window
            v_end = t_end + self.val_window
            test_end = v_end + self.test_window

            splits.append(
                {
                    "train": df.iloc[start_idx:t_end],
                    "val": df.iloc[t_end:v_end],
                    "test": df.iloc[v_end:test_end],
                    "period": f"{df.index[start_idx]} to {df.index[test_end - 1]}",
                }
            )
            start_idx += self.step_size

        return splits

    def evaluate_model(self, model_class: Any, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Executes walk-forward backtesting across all rolling windows.
        Returns out-of-sample performance metrics.
        """
        splits = self.generate_splits(df)
        if not splits:
            return {
                "status": "failed",
                "reason": "Insufficient data for walk-forward evaluation.",
            }

        out_of_sample_returns = []
        out_of_sample_signals = []

        for i, split in enumerate(splits):
            train_df = split["train"]
            test_df = split["test"]

            model = model_class()
            fit_success = model.fit(train_df)
            if not fit_success:
                continue

            # Evaluate model step-by-step on test set
            for idx in range(len(test_df)):
                window_input = test_df.iloc[: idx + 1]
                sig = model.predict(window_input)

                if idx < len(test_df) - 1:
                    ret = (
                        test_df["returns"].iloc[idx + 1]
                        if "returns" in test_df.columns
                        else 0.0
                    )
                    realized = sig * ret
                    out_of_sample_returns.append(realized)
                    out_of_sample_signals.append(sig)

        if not out_of_sample_returns:
            return {
                "status": "failed",
                "reason": "No out-of-sample predictions generated.",
            }

        returns_arr = np.array(out_of_sample_returns)
        cum_return = float(np.sum(returns_arr))
        mean_ret = float(np.mean(returns_arr))
        std_ret = float(np.std(returns_arr))

        sharpe = float((mean_ret / std_ret * np.sqrt(252))) if std_ret > 1e-6 else 0.0

        downside = returns_arr[returns_arr < 0]
        downside_std = float(np.std(downside)) if len(downside) > 0 else 1e-6
        sortino = (
            float((mean_ret / downside_std * np.sqrt(252)))
            if downside_std > 1e-6
            else 0.0
        )

        cum_curve = np.cumsum(returns_arr)
        peak = np.maximum.accumulate(cum_curve)
        drawdown = peak - cum_curve
        max_dd = float(np.max(drawdown)) if len(drawdown) > 0 else 0.0

        wins = returns_arr[returns_arr > 0]
        losses = returns_arr[returns_arr < 0]
        win_rate = float(len(wins) / len(returns_arr)) if len(returns_arr) > 0 else 0.0
        profit_factor = (
            float(np.sum(wins) / abs(np.sum(losses)))
            if len(losses) > 0 and abs(np.sum(losses)) > 1e-6
            else 1.0
        )

        return {
            "status": "success",
            "windows_evaluated": len(splits),
            "out_of_sample_samples": len(returns_arr),
            "cumulative_return": round(cum_return, 4),
            "sharpe_ratio": round(sharpe, 4),
            "sortino_ratio": round(sortino, 4),
            "max_drawdown": round(max_dd, 4),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 4),
        }
