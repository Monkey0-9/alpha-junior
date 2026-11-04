"""Regression tests for research-only Neural Causal Model behavior."""
import numpy as np
import pandas as pd
import pytest

from nexus.research.ncm import NeuralCausalModel, evaluate_chronological_holdout


def causal_sample(rows: int = 600) -> pd.DataFrame:
    rng = np.random.default_rng(17)
    x = rng.normal(size=rows)
    sector = 0.5 * x + rng.normal(scale=0.25, size=rows)
    target = 1.7 * x - 0.4 * sector + rng.normal(scale=0.08, size=rows)
    index = pd.date_range("2020-01-01", periods=rows, freq="h", tz="UTC")
    return pd.DataFrame(
        {"market_lag1": x, "sector_lag1": sector, "next_return": target},
        index=index,
    )


def test_ncm_rejects_unknown_parent_and_cycles() -> None:
    with pytest.raises(ValueError, match="unknown parents"):
        NeuralCausalModel({"target": ("missing",)})
    with pytest.raises(ValueError, match="cycle"):
        NeuralCausalModel({"x": ("y",), "y": ("x",)})


def test_ncm_fit_predict_and_intervention_are_reproducible() -> None:
    data = causal_sample()
    graph = {
        "market_lag1": (),
        "sector_lag1": (),
        "next_return": ("market_lag1", "sector_lag1"),
    }
    model = NeuralCausalModel(
        graph, hidden_layer_sizes=(24, 12), max_iter=400, random_state=17
    ).fit(data.iloc[:500])

    predictions = model.predict_target(data.iloc[500:], "next_return")
    assert predictions.shape == (100,)
    assert np.isfinite(predictions).all()

    low = model.simulate(
        500, interventions={"market_lag1": -2.0}, random_state=9
    )["next_return"].mean()
    high = model.simulate(
        500, interventions={"market_lag1": 2.0}, random_state=9
    )["next_return"].mean()
    repeat = model.simulate(
        500, interventions={"market_lag1": 2.0}, random_state=9
    )["next_return"].mean()
    assert high > low
    assert high == pytest.approx(repeat)
    assert model.metadata()["research_only"] is True


def test_ncm_purged_chronological_evaluation_compares_baseline() -> None:
    data = causal_sample()
    result = evaluate_chronological_holdout(
        data,
        {
            "market_lag1": (),
            "sector_lag1": (),
            "next_return": ("market_lag1", "sector_lag1"),
        },
        target="next_return",
        test_fraction=0.2,
        purge_rows=2,
        hidden_layer_sizes=(24, 12),
        max_iter=400,
        random_state=17,
    )
    assert result["status"] == "research_only"
    assert result["promotion_eligible"] is False
    assert result["purged_rows"] == 2
    assert result["test_rows"] == 120
    assert np.isfinite(result["ncm_rmse"])
    assert np.isfinite(result["baseline_rmse"])
    assert result["ncm_rmse"] < result["baseline_rmse"]


def test_ncm_rejects_nonfinite_data_and_unfitted_predict() -> None:
    data = causal_sample()
    model = NeuralCausalModel(
        {"market_lag1": (), "next_return": ("market_lag1",)}
    )
    with pytest.raises(RuntimeError, match="training-only"):
        model.predict_target(data, "next_return")
    data.loc[data.index[5], "market_lag1"] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        model.fit(data)


def test_ncm_artifact_integrity_manifest(tmp_path) -> None:
    data = causal_sample()
    model = NeuralCausalModel(
        {"market_lag1": (), "next_return": ("market_lag1",)},
        hidden_layer_sizes=(12,),
        max_iter=250,
    ).fit(data.iloc[:500])
    artifact = tmp_path / "ncm.joblib"
    manifest = model.save(artifact)
    assert manifest["sha256"]
    loaded = NeuralCausalModel.load_verified(artifact)
    assert loaded.is_fitted
    assert loaded.metadata()["graph"] == model.metadata()["graph"]

    artifact.write_bytes(artifact.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="SHA256"):
        NeuralCausalModel.load_verified(artifact)
