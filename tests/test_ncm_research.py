"""Tests for the isolated NCM research prototype; no broker APIs are involved."""

import numpy as np
import pandas as pd
import pytest

from nexus.research.ncm import NeuralCausalModel, evaluate_chronological_holdout


def _causal_sample(rows: int = 600) -> pd.DataFrame:
    rng = np.random.default_rng(41)
    x = rng.normal(size=rows)
    y = 1.7 * x + 0.1 * rng.normal(size=rows)
    index = pd.date_range("2020-01-01", periods=rows, freq="h", tz="UTC")
    return pd.DataFrame({"x_lag1": x, "next_return": y}, index=index)


def test_ncm_rejects_cycles_and_unknown_parents():
    with pytest.raises(ValueError, match="cycle"):
        NeuralCausalModel({"x": ("y",), "y": ("x",)})
    with pytest.raises(ValueError, match="unknown parents"):
        NeuralCausalModel({"x": ("missing",)})


def test_ncm_fit_predict_and_do_intervention_are_finite_and_reproducible():
    data = _causal_sample()
    graph = {"x_lag1": (), "next_return": ("x_lag1",)}
    model = NeuralCausalModel(
        graph, hidden_layer_sizes=(16,), max_iter=300, random_state=41
    ).fit(data.iloc[:500])

    predictions = model.predict_target(data.iloc[500:], "next_return")
    assert predictions.shape == (100,)
    assert np.isfinite(predictions).all()

    low = model.simulate(
        512, interventions={"x_lag1": -2.0}, random_state=7
    )["next_return"].mean()
    high = model.simulate(
        512, interventions={"x_lag1": 2.0}, random_state=7
    )["next_return"].mean()
    repeat = model.simulate(
        512, interventions={"x_lag1": 2.0}, random_state=7
    )["next_return"].mean()

    assert high > low + 1.0
    assert high == pytest.approx(repeat)
    assert model.metadata()["research_only"] is True


def test_ncm_chronological_evaluator_reports_baseline_and_never_promotes():
    data = _causal_sample()
    result = evaluate_chronological_holdout(
        data,
        {"x_lag1": (), "next_return": ("x_lag1",)},
        target="next_return",
        hidden_layer_sizes=(16,),
        max_iter=300,
        random_state=41,
    )
    assert result["status"] == "research_only"
    assert result["promotion_eligible"] is False
    assert result["train_rows"] == 480
    assert result["test_rows"] == 120
    assert np.isfinite(result["ncm_rmse"])
    assert np.isfinite(result["baseline_rmse"])
    assert result["ncm_rmse"] < result["baseline_rmse"]


def test_ncm_fails_on_missing_or_nonfinite_data():
    data = _causal_sample()
    model = NeuralCausalModel({"x_lag1": (), "next_return": ("x_lag1",)})
    with pytest.raises(RuntimeError, match="fitted"):
        model.simulate(10)

    data.loc[data.index[10], "x_lag1"] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        model.fit(data)
