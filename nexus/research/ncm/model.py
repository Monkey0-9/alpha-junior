"""Research-only Neural Causal Model prototype.

The graph is supplied explicitly by the researcher. This module does not infer
causal structure, guarantee causal identification, or emit trading instructions.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import json
import hashlib

import numpy as np
import pandas as pd
from sklearn.neural_network import MLPRegressor
import joblib


@dataclass(frozen=True)
class _Mechanism:
    model: MLPRegressor
    x_mean: np.ndarray
    x_scale: np.ndarray
    y_mean: float
    y_scale: float
    residuals: np.ndarray


class NeuralCausalModel:
    """A declared-DAG structural causal model with neural mechanisms.

    Example graph:
        {
            "market_return_lag1": (),
            "sector_return_lag1": (),
            "volatility_lag1": (),
            "asset_return_next": (
                "market_return_lag1",
                "sector_return_lag1",
                "volatility_lag1",
            ),
        }

    Root variables are sampled from their empirical training distributions.
    Non-root variables are generated from a fitted MLP conditional mechanism
    plus a bootstrap sample of residuals. Use lagged, point-in-time variables.
    """

    MODEL_FORMAT_VERSION = 1

    def __init__(
        self,
        graph: Mapping[str, Sequence[str]],
        hidden_layer_sizes: tuple[int, ...] = (32, 16),
        max_iter: int = 500,
        random_state: int = 42,
        alpha: float = 1e-3,
    ) -> None:
        if not graph:
            raise ValueError("The causal graph cannot be empty.")

        normalized: dict[str, tuple[str, ...]] = {}
        for raw_node, raw_parents in graph.items():
            if not isinstance(raw_node, str) or not raw_node.strip():
                raise ValueError("Graph nodes must be non-empty strings.")
            node = raw_node.strip()
            parents = tuple(raw_parents)
            if any(not isinstance(parent, str) or not parent.strip() for parent in parents):
                raise ValueError(f"Parents for {node!r} must be non-empty strings.")
            parents = tuple(parent.strip() for parent in parents)
            if len(set(parents)) != len(parents):
                raise ValueError(f"Parents for {node!r} must be unique.")
            if node in parents:
                raise ValueError(f"Node {node!r} cannot be its own parent.")
            normalized[node] = parents

        unknown = {
            parent
            for parents in normalized.values()
            for parent in parents
            if parent not in normalized
        }
        if unknown:
            raise ValueError(f"Graph references unknown parents: {sorted(unknown)}")
        if not hidden_layer_sizes or any(
            not isinstance(width, int) or width < 1 for width in hidden_layer_sizes
        ):
            raise ValueError("hidden_layer_sizes must contain positive integers.")
        if not isinstance(max_iter, int) or max_iter < 1:
            raise ValueError("max_iter must be a positive integer.")
        if not np.isfinite(alpha) or alpha < 0:
            raise ValueError("alpha must be finite and non-negative.")

        self.graph = normalized
        self.order = self._topological_order()
        self.hidden_layer_sizes = tuple(hidden_layer_sizes)
        self.max_iter = max_iter
        self.random_state = int(random_state)
        self.alpha = float(alpha)

        self._root_samples: dict[str, np.ndarray] = {}
        self._mechanisms: dict[str, _Mechanism] = {}
        self.training_rows = 0
        self.training_columns: tuple[str, ...] = tuple(self.graph)
        self.is_fitted = False

    def _topological_order(self) -> tuple[str, ...]:
        order: list[str] = []
        remaining = list(self.graph)
        while remaining:
            ready = [
                node for node in remaining
                if all(parent in order for parent in self.graph[node])
            ]
            if not ready:
                raise ValueError("The causal graph contains a directed cycle.")
            for node in ready:
                order.append(node)
                remaining.remove(node)
        return tuple(order)

    def fit(self, data: pd.DataFrame) -> "NeuralCausalModel":
        """Fit mechanisms on a training-only slice supplied by the caller."""
        if not isinstance(data, pd.DataFrame):
            raise TypeError("data must be a pandas DataFrame.")
        if len(data) < 50:
            raise ValueError("At least 50 training rows are required.")
        missing = [node for node in self.graph if node not in data.columns]
        if missing:
            raise ValueError(f"Training data is missing graph nodes: {missing}")

        frame = data.loc[:, list(self.graph)].apply(pd.to_numeric, errors="coerce")
        values = frame.to_numpy(dtype=np.float64)
        if not np.isfinite(values).all():
            raise ValueError("Training data contains missing or non-finite causal variables.")

        roots: dict[str, np.ndarray] = {}
        mechanisms: dict[str, _Mechanism] = {}
        for node_index, node in enumerate(self.order):
            parents = self.graph[node]
            target = frame[node].to_numpy(dtype=np.float64)

            if not parents:
                roots[node] = target.copy()
                continue

            x = frame.loc[:, list(parents)].to_numpy(dtype=np.float64)
            x_mean = x.mean(axis=0)
            x_scale = x.std(axis=0)
            x_scale[x_scale < 1e-12] = 1.0
            y_mean = float(target.mean())
            y_scale = float(target.std())
            if y_scale < 1e-12:
                y_scale = 1.0

            model = MLPRegressor(
                hidden_layer_sizes=self.hidden_layer_sizes,
                activation="tanh",
                solver="adam",
                alpha=self.alpha,
                batch_size=min(64, len(frame)),
                learning_rate_init=1e-3,
                max_iter=self.max_iter,
                random_state=self.random_state + node_index,
                shuffle=True,
                early_stopping=False,
                n_iter_no_change=30,
            )
            scaled_x = (x - x_mean) / x_scale
            scaled_y = (target - y_mean) / y_scale
            model.fit(scaled_x, scaled_y)

            fitted = model.predict(scaled_x) * y_scale + y_mean
            residuals = target - fitted
            mechanisms[node] = _Mechanism(
                model=model,
                x_mean=x_mean,
                x_scale=x_scale,
                y_mean=y_mean,
                y_scale=y_scale,
                residuals=residuals.astype(np.float64, copy=True),
            )

        # Assign only after every mechanism succeeds; a partial fit is not usable.
        self._root_samples = roots
        self._mechanisms = mechanisms
        self.training_rows = len(frame)
        self.is_fitted = True
        return self

    def predict_target(self, observations: pd.DataFrame, target: str) -> np.ndarray:
        """Return conditional mean predictions for a non-root target node."""
        self._require_fitted()
        if target not in self.graph:
            raise ValueError(f"Unknown target node: {target!r}")
        parents = self.graph[target]
        if not parents:
            raise ValueError(f"Root node {target!r} has no conditional mechanism.")

        missing = [parent for parent in parents if parent not in observations.columns]
        if missing:
            raise ValueError(f"Prediction data is missing parent columns: {missing}")

        x = observations.loc[:, list(parents)].apply(
            pd.to_numeric, errors="coerce"
        ).to_numpy(dtype=np.float64)
        if not np.isfinite(x).all():
            raise ValueError("Prediction parents contain missing or non-finite values.")

        mechanism = self._mechanisms[target]
        scaled_x = (x - mechanism.x_mean) / mechanism.x_scale
        prediction = (
            mechanism.model.predict(scaled_x) * mechanism.y_scale + mechanism.y_mean
        )
        if not np.isfinite(prediction).all():
            raise ValueError("NCM generated non-finite predictions.")
        return np.asarray(prediction, dtype=np.float64)

    def simulate(
        self,
        n_samples: int = 1000,
        interventions: Mapping[
            str, float | Sequence[float] | np.ndarray
        ] | None = None,
        random_state: int | None = None,
    ) -> pd.DataFrame:
        """Generate reproducible SCM samples under optional do-interventions."""
        self._require_fitted()
        if not isinstance(n_samples, int) or n_samples < 1:
            raise ValueError("n_samples must be a positive integer.")

        intervention_values = dict(interventions or {})
        unknown = set(intervention_values) - set(self.graph)
        if unknown:
            raise ValueError(f"Interventions target unknown graph nodes: {sorted(unknown)}")

        rng = np.random.default_rng(
            self.random_state if random_state is None else random_state
        )
        generated: dict[str, np.ndarray] = {}

        for node in self.order:
            if node in intervention_values:
                raw = np.asarray(intervention_values[node], dtype=np.float64)
                if raw.ndim == 0:
                    values = np.full(n_samples, float(raw), dtype=np.float64)
                elif raw.ndim == 1 and len(raw) == n_samples:
                    values = raw.astype(np.float64, copy=True)
                else:
                    raise ValueError(
                        f"Intervention for {node!r} must be scalar or length {n_samples}."
                    )
                if not np.isfinite(values).all():
                    raise ValueError(f"Intervention for {node!r} contains non-finite values.")
                generated[node] = values
                continue

            parents = self.graph[node]
            if not parents:
                generated[node] = rng.choice(
                    self._root_samples[node], size=n_samples, replace=True
                )
                continue

            mechanism = self._mechanisms[node]
            x = np.column_stack([generated[parent] for parent in parents])
            scaled_x = (x - mechanism.x_mean) / mechanism.x_scale
            conditional_mean = (
                mechanism.model.predict(scaled_x) * mechanism.y_scale + mechanism.y_mean
            )
            noise = rng.choice(mechanism.residuals, size=n_samples, replace=True)
            generated[node] = conditional_mean + noise

        result = pd.DataFrame(generated, columns=list(self.order))
        if not np.isfinite(result.to_numpy(dtype=np.float64)).all():
            raise ValueError("SCM simulation produced non-finite values.")
        return result

    def metadata(self) -> dict[str, Any]:
        return {
            "format_version": self.MODEL_FORMAT_VERSION,
            "model_type": "NeuralCausalModel",
            "graph": {node: list(parents) for node, parents in self.graph.items()},
            "topological_order": list(self.order),
            "hidden_layer_sizes": list(self.hidden_layer_sizes),
            "max_iter": self.max_iter,
            "alpha": self.alpha,
            "random_state": self.random_state,
            "training_rows": self.training_rows,
            "training_columns": list(self.training_columns),
            "fitted": self.is_fitted,
            "research_only": True,
        }

    def save(self, artifact_path: str | Path) -> dict[str, Any]:
        """Persist a trusted local research artifact and an integrity manifest."""
        self._require_fitted()
        path = Path(artifact_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = {
            "artifact": path.name,
            "sha256": digest,
            "metadata": self.metadata(),
            "warning": "Only load artifacts from trusted, verified sources; joblib uses pickle.",
        }
        manifest_path = path.with_suffix(path.suffix + ".json")
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    @classmethod
    def load_verified(cls, artifact_path: str | Path) -> "NeuralCausalModel":
        """Load only if a colocated SHA256 manifest matches the artifact bytes."""
        path = Path(artifact_path)
        manifest_path = path.with_suffix(path.suffix + ".json")
        if not path.is_file() or not manifest_path.is_file():
            raise FileNotFoundError("NCM artifact or integrity manifest is missing.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != manifest.get("sha256"):
            raise ValueError("NCM artifact SHA256 does not match its manifest.")
        model = joblib.load(path)
        if not isinstance(model, cls) or not model.is_fitted:
            raise ValueError("Artifact is not a fitted NeuralCausalModel.")
        return model

    def _require_fitted(self) -> None:
        if not self.is_fitted:
            raise RuntimeError("NCM must be fitted on a training-only slice before use.")
