"""Leakage-aware chronological holdout evaluation for NCM research."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .model import NeuralCausalModel


def evaluate_chronological_holdout(
    data: pd.DataFrame,
    graph: Mapping[str, Sequence[str]],
    target: str,
    test_fraction: float = 0.2,
    purge_rows: int = 1,
    hidden_layer_sizes: tuple[int, ...] = (32, 16),
    max_iter: int = 500,
    random_state: int = 42,
) -> dict[str, Any]:
    """Fit only before the holdout, apply a purge gap, and compare with a mean baseline.

    Caller must sort input rows by event/availability time and provide only
    features that would have been available before the target horizon. This
    routine cannot infer point-in-time provenance or prove causal identification.
    """
    if not isinstance(data, pd.DataFrame):
        raise TypeError("data must be a pandas DataFrame.")
    if not 0.05 <= test_fraction <= 0.4:
        raise ValueError("test_fraction must be between 0.05 and 0.40.")
    if not isinstance(purge_rows, int) or purge_rows < 0:
        raise ValueError("purge_rows must be a non-negative integer.")
    if isinstance(data.index, pd.DatetimeIndex) and not data.index.is_monotonic_increasing:
        raise ValueError("DatetimeIndex must be sorted in ascending time order.")
    if target not in graph or not graph[target]:
        raise ValueError("Target must be a declared non-root node.")
    missing = sorted(set(graph) - set(data.columns))
    if missing:
        raise ValueError(f"Evaluation data is missing graph columns: {missing}")
    if len(data) < 100:
        raise ValueError("At least 100 chronologically ordered rows are required.")

    split_at = int(len(data) * (1.0 - test_fraction))
    train_end = split_at - purge_rows
    if train_end < 50 or len(data) - split_at < 10:
        raise ValueError("Training or holdout slice is too small after purging.")

    train = data.iloc[:train_end].copy()
    test = data.iloc[split_at:].copy()
    model = NeuralCausalModel(
        graph,
        hidden_layer_sizes=hidden_layer_sizes,
        max_iter=max_iter,
        random_state=random_state,
    ).fit(train)

    actual = pd.to_numeric(test[target], errors="coerce").to_numpy(dtype=np.float64)
    if not np.isfinite(actual).all():
        raise ValueError("Holdout target contains missing/non-finite values.")
    prediction = model.predict_target(test, target)
    baseline_value = float(pd.to_numeric(train[target], errors="raise").mean())
    baseline = np.full(len(actual), baseline_value, dtype=np.float64)

    rmse = float(np.sqrt(np.mean(np.square(prediction - actual))))
    baseline_rmse = float(np.sqrt(np.mean(np.square(baseline - actual))))
    mae = float(np.mean(np.abs(prediction - actual)))
    baseline_mae = float(np.mean(np.abs(baseline - actual)))
    skill = float(1.0 - rmse / baseline_rmse) if baseline_rmse > 1e-12 else None
    rank_ic_raw = pd.Series(prediction).corr(pd.Series(actual), method="spearman")
    rank_ic = (
        float(rank_ic_raw)
        if pd.notna(rank_ic_raw) and np.isfinite(rank_ic_raw)
        else None
    )

    return {
        "status": "research_only",
        "promotion_eligible": False,
        "model_type": "NeuralCausalModel",
        "target": target,
        "train_rows": int(len(train)),
        "purged_rows": int(purge_rows),
        "test_rows": int(len(test)),
        "train_end": str(train.index[-1]),
        "test_start": str(test.index[0]),
        "ncm_rmse": rmse,
        "baseline_rmse": baseline_rmse,
        "ncm_mae": mae,
        "baseline_mae": baseline_mae,
        "rmse_skill_vs_train_mean": skill,
        "rank_ic": rank_ic,
        "metadata": model.metadata(),
        "note": (
            "This single holdout is a screening result only. Require multiple "
            "walk-forward windows, point-in-time provenance, calibration, costs, "
            "uncertainty and independent approval before any promotion."
        ),
    }
