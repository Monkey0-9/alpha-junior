"""Leakage-aware chronological evaluation helpers for NCM research."""
from __future__ import annotations

from typing import Mapping, Sequence, Any

import numpy as np
import pandas as pd

from .model import NeuralCausalModel


def evaluate_chronological_holdout(
    data: pd.DataFrame,
    graph: Mapping[str, Sequence[str]],
    target: str,
    test_fraction: float = 0.2,
    hidden_layer_sizes: tuple[int, ...] = (32, 16),
    max_iter: int = 400,
    random_state: int = 42,
) -> dict[str, Any]:
    """Fit on the first time slice and evaluate on a later untouched slice.

    The input must already be sorted by event/availability time. Every parent of
    the target should be a point-in-time feature that existed before the target
    horizon. This helper cannot infer feature timestamps or prove causality.
    It compares a trained NCM against a constant training-mean baseline and never
    promotes the model.
    """
    if not isinstance(data, pd.DataFrame):
        raise TypeError("data must be a pandas DataFrame.")
    if not 0.05 <= test_fraction <= 0.4:
        raise ValueError("test_fraction must be between 0.05 and 0.4.")
    if isinstance(data.index, pd.DatetimeIndex) and not data.index.is_monotonic_increasing:
        raise ValueError("DatetimeIndex must be sorted in ascending time order.")
    if target not in graph:
        raise ValueError(f"Target {target!r} is not present in the causal graph.")
    if not graph[target]:
        raise ValueError("The target must have declared parent features.")
    required = set(graph)
    missing = sorted(required - set(data.columns))
    if missing:
        raise ValueError(f"Evaluation data is missing graph columns: {missing}")
    if len(data) < 100:
        raise ValueError("At least 100 chronologically ordered rows are required.")

    split_at = int(len(data) * (1.0 - test_fraction))
    if split_at < 50 or len(data) - split_at < 10:
        raise ValueError("Holdout split is too small for research evaluation.")

    train = data.iloc[:split_at].copy()
    test = data.iloc[split_at:].copy()
    model = NeuralCausalModel(
        graph=graph,
        hidden_layer_sizes=hidden_layer_sizes,
        max_iter=max_iter,
        random_state=random_state,
    ).fit(train)

    actual = pd.to_numeric(test[target], errors="coerce").to_numpy(dtype=np.float64)
    if not np.isfinite(actual).all():
        raise ValueError("Holdout target contains missing or non-finite values.")
    prediction = model.predict_target(test, target)
    baseline_value = float(pd.to_numeric(train[target], errors="raise").mean())
    baseline = np.full(len(actual), baseline_value, dtype=np.float64)

    rmse = float(np.sqrt(np.mean(np.square(prediction - actual))))
    baseline_rmse = float(np.sqrt(np.mean(np.square(baseline - actual))))
    mae = float(np.mean(np.abs(prediction - actual)))
    baseline_mae = float(np.mean(np.abs(baseline - actual)))
    rmse_skill = (
        float(1.0 - rmse / baseline_rmse) if baseline_rmse > 1e-12 else None
    )
    rank_ic = pd.Series(prediction).corr(pd.Series(actual), method="spearman")
    rank_ic_value = float(rank_ic) if pd.notna(rank_ic) and np.isfinite(rank_ic) else None
    directional_accuracy = float(
        np.mean(np.sign(prediction) == np.sign(actual))
    )

    return {
        "status": "research_only",
        "promotion_eligible": False,
        "model_type": "NeuralCausalModel",
        "target": target,
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
        "train_end_index": str(train.index[-1]),
        "test_start_index": str(test.index[0]),
        "ncm_rmse": rmse,
        "baseline_rmse": baseline_rmse,
        "ncm_mae": mae,
        "baseline_mae": baseline_mae,
        "rmse_skill_vs_train_mean": rmse_skill,
        "rank_ic": rank_ic_value,
        "directional_accuracy": directional_accuracy,
        "metadata": model.metadata(),
        "note": (
            "Research-only metrics. Use lagged point-in-time features, multiple "
            "walk-forward windows, transaction costs, calibration and statistical "
            "uncertainty before considering any model promotion."
        ),
    }
