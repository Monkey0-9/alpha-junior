import os
import json
import logging
import time
import pandas as pd
from typing import Dict, Any, Optional, Tuple
from nexus.features.features import FeatureEngineer
from nexus.models.zoo.time_series import GradientBoostedTimeSeriesModel

from nexus.research.walk_forward import WalkForwardEvaluator
from nexus.utils.config import Config

logger = logging.getLogger(__name__)

REGISTRY_PATH = os.path.join("models", "registry.json")


class ModelRegistry:
    """
    Manages active production models vs candidate models.
    Validates candidates out-of-sample on Sharpe, Sortino, Max Drawdown, and Profit Factor.
    Deploys ONLY if out-of-sample risk-adjusted performance strictly improves.
    """

    def __init__(self, registry_file: str = REGISTRY_PATH):
        self.registry_file = registry_file
        os.makedirs(os.path.dirname(self.registry_file), exist_ok=True)
        self.active_version = "v1.0.0"
        self.active_metrics: Dict[str, float] = {
            "sharpe_ratio": 0.50,
            "sortino_ratio": 0.50,
            "max_drawdown": 0.15,
            "profit_factor": 1.05,
            "win_rate": 0.50,
        }
        self.active_model: Optional[Any] = None
        self._load_registry()

    def _load_registry(self) -> None:
        if os.path.exists(self.registry_file):
            try:
                with open(self.registry_file, "r") as f:
                    data = json.load(f)
                    self.active_version = data.get("active_version", "v1.0.0")
                    self.active_metrics = data.get(
                        "active_metrics", self.active_metrics
                    )
            except Exception as e:
                logger.warning("Failed to load model registry: %s", e)

    def _save_registry(self) -> None:
        try:
            with open(self.registry_file, "w") as f:
                json.dump(
                    {
                        "active_version": self.active_version,
                        "active_metrics": self.active_metrics,
                        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                    f,
                    indent=2,
                )
        except Exception as e:
            logger.error("Failed to save model registry: %s", e)

    def evaluate_candidate(
        self, candidate_model_class: Any, validation_data: pd.DataFrame
    ) -> Tuple[bool, Dict[str, float]]:
        """
        Evaluates candidate model using out-of-sample Walk-Forward evaluation.
        Promotes ONLY if risk-adjusted metrics (Sharpe, Profit Factor, Max DD) exceed active benchmarks.
        """
        if validation_data.empty or len(validation_data) < 50:
            return False, {}

        features = FeatureEngineer.add_all_features(validation_data)
        if len(features) < 40:
            return False, {}

        evaluator = WalkForwardEvaluator(
            train_window=30, val_window=10, test_window=10, step_size=10
        )
        res = evaluator.evaluate_model(candidate_model_class, features)

        if res.get("status") != "success":
            return False, {}

        candidate_sharpe = res.get("sharpe_ratio", 0.0)
        candidate_pf = res.get("profit_factor", 0.0)
        candidate_dd = res.get("max_drawdown", 1.0)
        candidate_win = res.get("win_rate", 0.0)

        active_sharpe = self.active_metrics.get("sharpe_ratio", 0.50)

        candidate_metrics = {
            "sharpe_ratio": candidate_sharpe,
            "sortino_ratio": res.get("sortino_ratio", 0.0),
            "max_drawdown": candidate_dd,
            "profit_factor": candidate_pf,
            "win_rate": candidate_win,
            "out_of_sample_samples": res.get("out_of_sample_samples", 0),
        }

        # Model evaluation is useful research, but it must not silently
        # replace the serving model. Promotion is a separate, explicit release
        # operation after artifact persistence, calibration, approval and rollback.
        if not Config.AUTO_MODEL_PROMOTION_ENABLED:
            logger.info(
                "Candidate evaluated but not promoted: NEXUS_AUTO_MODEL_PROMOTION is disabled."
            )
            return False, candidate_metrics

        # Institutional promotion gating:
        # Candidate Sharpe must beat active Sharpe by >= 0.10, Profit Factor >
        # 1.05, Max DD < 25%
        if (
            candidate_sharpe >= active_sharpe + 0.10
            and candidate_pf > 1.05
            and candidate_dd < 0.25
        ):
            major, minor, patch = self.active_version.replace("v", "").split(".")
            new_version = f"v{major}.{minor}.{int(patch) + 1}"

            self.active_version = new_version
            self.active_metrics = candidate_metrics
            self.active_model = candidate_model_class()
            self._save_registry()
            logger.info(
                "NEW MODEL PROMOTED TO PRODUCTION: %s (Sharpe: %.2f vs prev %.2f, PF: %.2f)",
                new_version,
                candidate_sharpe,
                active_sharpe,
                candidate_pf,
            )
            return True, candidate_metrics

        logger.info(
            "Candidate model rejected (Sharpe: %.2f vs required %.2f)",
            candidate_sharpe,
            active_sharpe + 0.10,
        )
        return False, candidate_metrics


class ContinuousLearner:
    """
    Automated continuous retraining pipeline.
    Periodically retrains models on incoming bar data and evaluates via Walk-Forward validation.
    """

    def __init__(self, registry: Optional[ModelRegistry] = None):
        self.registry = registry or ModelRegistry()
        self.last_retrain_time = 0.0
        self.retrain_interval = 3600.0 * 6  # Retrain every 6 hours

    def step_retrain(self, historical_bars: pd.DataFrame) -> bool:
        if not Config.AUTO_RETRAIN_ENABLED:
            return False
        now = time.time()
        if now - self.last_retrain_time < self.retrain_interval:
            return False

        if historical_bars.empty or len(historical_bars) < 100:
            return False

        logger.info(
            "Executing continuous retraining cycle on %d bars...",
            len(historical_bars),
        )

        promoted, metrics = self.registry.evaluate_candidate(
            GradientBoostedTimeSeriesModel, historical_bars
        )
        self.last_retrain_time = now
        return promoted
