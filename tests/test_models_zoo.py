import pandas as pd
import numpy as np
from unittest.mock import MagicMock
from nexus.models.zoo.ensemble import AIEnsembleBrain
from nexus.models.zoo.time_series import (
    GradientBoostedTimeSeriesModel,
    PyTorchLSTMModel,
)


def test_ensemble_initialization():
    brain = AIEnsembleBrain(confidence_gate=0.4)
    assert brain.confidence_gate == 0.4
    assert len(brain.models) == 0


def test_ensemble_add_model():
    brain = AIEnsembleBrain()
    mock_model = MagicMock()
    brain.add_model(mock_model, name="test", weight=1.5)
    assert len(brain.models) == 1
    assert brain.models[0]["name"] == "test"
    assert brain.models[0]["weight"] == 1.5


def test_ensemble_get_signal_empty():
    brain = AIEnsembleBrain()
    # Empty models
    assert brain.get_signal(pd.DataFrame()) == 0

    mock_model = MagicMock()
    brain.add_model(mock_model)
    # Empty features
    assert brain.get_signal(pd.DataFrame()) == 0


def test_ensemble_get_signal_aggregation():
    brain = AIEnsembleBrain(confidence_gate=0.3)

    mock1 = MagicMock()
    mock1.predict.return_value = 1
    brain.add_model(mock1, name="m1", weight=1.0)

    mock2 = MagicMock()
    mock2.predict.return_value = -1
    brain.add_model(mock2, name="m2", weight=0.5)

    df = pd.DataFrame({"close": [1, 2, 3]})
    signal = brain.get_signal(df, regime="BULL")

    assert signal == 1


def test_ensemble_get_signal_turbulent_gating():
    brain = AIEnsembleBrain(confidence_gate=0.3)

    mock1 = MagicMock()
    mock1.predict.return_value = 1
    brain.add_model(mock1, name="m1", weight=1.0)

    df = pd.DataFrame({"close": [1, 2, 3]})
    signal = brain.get_signal(
        df, regime="TURBULENT", regime_probabilities={"TURBULENT": 1.0}
    )
    assert signal == 1


def test_ensemble_record_outcome():
    brain = AIEnsembleBrain()
    mock_model = MagicMock()
    brain.add_model(mock_model, name="m1")

    brain.record_outcome("m1", predicted_signal=1, realized_return=0.05)  # correct
    brain.record_outcome("m1", predicted_signal=1, realized_return=-0.05)  # incorrect

    assert brain.performance_history["m1"] == [1, 0]


def _create_ts_data(rows=50):
    df = pd.DataFrame(
        {
            "returns": np.random.randn(rows) * 0.01,
            "rsi_14": np.random.uniform(30, 70, rows),
            "macd": np.random.randn(rows) * 0.1,
            "macd_signal": np.random.randn(rows) * 0.1,
            "macd_hist": np.random.randn(rows) * 0.1,
            "roc_10": np.random.randn(rows) * 0.05,
            "bb_mean": np.random.uniform(100, 150, rows),
            "bb_std": np.random.uniform(1, 5, rows),
            "atr_14": np.random.uniform(0.5, 2, rows),
            "close": np.linspace(100, 150, rows),
        }
    )
    return df


def test_ts_model_init():
    model = GradientBoostedTimeSeriesModel(confidence_threshold=0.6)
    assert model.confidence_threshold == 0.6
    assert model.is_trained is False


def test_ts_model_fit_insufficient_data():
    model = GradientBoostedTimeSeriesModel()
    df = _create_ts_data(10)
    success = model.fit(df)
    assert not success
    assert not model.is_trained


def test_ts_model_fit_and_predict():
    model = GradientBoostedTimeSeriesModel(confidence_threshold=0.5)
    df = _create_ts_data(100)

    df["returns"] = 0.05
    df["close"] = np.linspace(100, 500, 100)

    success = model.fit(df)
    assert success
    assert model.is_trained

    new_df = _create_ts_data(5)
    new_df["returns"] = 0.05
    new_df["close"] = np.linspace(500, 550, 5)

    pred = model.predict(new_df)
    assert pred in [-1, 0, 1]


def test_ts_model_predict_untrained_does_not_fit_inference_frame():
    model = GradientBoostedTimeSeriesModel()
    df = _create_ts_data(50)
    assert model.predict(df) == 0
    assert model.is_trained is False
    assert model.predict_proba(df) == {1: 0.0, -1: 0.0, 0: 1.0}


def test_ts_model_missing_features():
    model = GradientBoostedTimeSeriesModel()
    df = pd.DataFrame({"close": [1, 2, 3]})
    pred = model.predict(df)
    assert pred == 0


def test_pytorch_lstm_model():
    model = PyTorchLSTMModel(sequence_length=5, epochs=2)
    df = _create_ts_data(50)
    success = model.fit(df)
    assert success
    pred = model.predict(df)
    assert pred in [-1, 0, 1]
    probs = model.predict_proba(df)
    assert 1 in probs and -1 in probs and 0 in probs


def test_temporal_transformer_model():
    from nexus.models.zoo.time_series import TemporalTransformerModel

    model = TemporalTransformerModel(sequence_length=5, epochs=2)
    df = _create_ts_data(50)
    success = model.fit(df)
    assert success
    pred = model.predict(df)
    assert pred in [-1, 0, 1]
    probs = model.predict_proba(df)
    assert 1 in probs and -1 in probs and 0 in probs


def test_ensemble_predict_proba_summary():
    brain = AIEnsembleBrain()
    mock_model = MagicMock()
    mock_model.predict_proba.return_value = {1: 0.6, -1: 0.2, 0: 0.2}
    brain.add_model(mock_model, name="m1", weight=1.0)

    df = _create_ts_data(20)
    summary = brain.predict_proba_summary(df)
    assert "p_buy" in summary
    assert "p_sell" in summary
    assert summary["p_buy"] == 0.6
    assert summary["expected_signal"] == 0.4


def test_portfolio_risk_calculation():
    from nexus.risk.engine import RiskEngine

    risk = RiskEngine()
    weights = np.array([0.5, 0.5])
    returns_matrix = np.random.randn(50, 2) * 0.01
    res = risk.calculate_portfolio_risk(weights, returns_matrix)
    assert "portfolio_var" in res
    assert "portfolio_cvar" in res
    assert "portfolio_vol" in res
    assert "diversification_ratio" in res


def test_ensemble_record_calibration_outcome():
    brain = AIEnsembleBrain()
    brain.add_model(MagicMock(), name="m1")
    calib = brain.record_calibration_outcome(
        "m1", predicted_prob=0.8, predicted_signal=0.6, realized_return=0.02
    )
    assert calib["correct"] == 1
    assert "brier_score" in calib
    assert "signal_error" in calib
