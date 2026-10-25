import logging
import numpy as np
import pandas as pd
from typing import Dict, Tuple, Any
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim

    TORCH_AVAILABLE = True
except ImportError:
    # Keep optional torch names defined without redeclaring successful imports.
    torch = None
    nn = None
    optim = None
    TORCH_AVAILABLE = False

logger = logging.getLogger(__name__)


class GradientBoostedTimeSeriesModel:
    """
    Real Gradient Boosted Decision Tree model for time-series directional forecasting.
    Uses technical indicator feature vectors to predict price direction.
    """

    FEATURE_COLS = [
        "returns",
        "rsi_14",
        "macd",
        "macd_signal",
        "macd_hist",
        "roc_10",
        "bb_mean",
        "bb_std",
        "atr_14",
    ]

    def __init__(self, confidence_threshold: float = 0.55):
        self.confidence_threshold = confidence_threshold
        self.model = HistGradientBoostingClassifier(
            max_iter=100, learning_rate=0.05, max_depth=5, random_state=42
        )
        self.scaler = StandardScaler()
        self.is_trained = False

    def _prepare_features(self, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        """Extract feature matrix and target vector from DataFrame."""
        df_copy = df.copy()

        # Generate target: +1 if next close > current close, else -1
        if "close" in df_copy.columns:
            df_copy["target"] = np.where(
                df_copy["close"].shift(-1) > df_copy["close"], 1, -1
            )
        else:
            df_copy["target"] = 0

        # Ensure all feature columns exist
        available_cols = [c for c in self.FEATURE_COLS if c in df_copy.columns]
        if not available_cols:
            # Fallback to numeric columns
            numeric_cols = df_copy.select_dtypes(include=[np.number]).columns.tolist()
            available_cols = [c for c in numeric_cols if c != "target"]

        df_clean = df_copy.dropna(subset=available_cols)
        if len(df_clean) < 20:
            return np.array([]), np.array([])

        X = df_clean[available_cols].to_numpy()
        y = df_clean["target"].to_numpy()
        return X, y

    def fit(self, df: pd.DataFrame) -> bool:
        """Trains the model on historical bar data."""
        X, y = self._prepare_features(df)
        if len(X) < 30:
            logger.warning("Insufficient data samples to train TimeSeriesModel.")
            return False

        try:
            X_scaled = self.scaler.fit_transform(X)
            self.model.fit(X_scaled, y)
            self.is_trained = True
            logger.info(
                "GradientBoostedTimeSeriesModel successfully trained on %d samples.",
                len(X),
            )
            return True
        except Exception as e:
            logger.error("Failed to fit TimeSeriesModel: %s", e)
            return False

    def predict(self, df: pd.DataFrame) -> int:
        """
        Returns signal:
          1  (Buy)
         -1  (Sell)
          0  (Hold / No Trade)
        """
        if not self.is_trained:
            # Attempt quick fit if untrained
            success = self.fit(df)
            if not success:
                return 0

        available_cols = [c for c in self.FEATURE_COLS if c in df.columns]
        if not available_cols or len(df) < 1:
            return 0

        try:
            latest_row = df[available_cols].iloc[-1:].dropna()
            if latest_row.empty:
                return 0

            X_last = self.scaler.transform(latest_row.to_numpy())
            probs = self.model.predict_proba(X_last)[0]
            classes = self.model.classes_

            prob_map = {c: p for c, p in zip(classes, probs)}
            prob_buy = prob_map.get(1, 0.0)
            prob_sell = prob_map.get(-1, 0.0)

            # Confidence gating: Only issue signal if probability exceeds
            # threshold
            if prob_buy > self.confidence_threshold:
                return 1
            elif prob_sell > self.confidence_threshold:
                return -1
            else:
                return 0  # NO_TRADE / HOLD
        except Exception as e:
            logger.debug("TimeSeriesModel prediction error: %s", e)
            return 0

    def predict_proba(self, df: pd.DataFrame) -> Dict[int, float]:
        """Returns probability dictionary {1: p_buy, -1: p_sell, 0: p_hold}."""
        default_probs = {1: 0.33, -1: 0.33, 0: 0.34}
        if not self.is_trained:
            if not self.fit(df):
                return default_probs

        available_cols = [c for c in self.FEATURE_COLS if c in df.columns]
        if not available_cols or len(df) < 1:
            return default_probs

        try:
            latest_row = df[available_cols].iloc[-1:].dropna()
            if latest_row.empty:
                return default_probs

            X_last = self.scaler.transform(latest_row.to_numpy())
            probs = self.model.predict_proba(X_last)[0]
            classes = self.model.classes_

            prob_map = {c: float(p) for c, p in zip(classes, probs)}
            p_buy = prob_map.get(1, 0.0)
            p_sell = prob_map.get(-1, 0.0)
            p_hold = max(0.0, 1.0 - p_buy - p_sell)
            return {1: p_buy, -1: p_sell, 0: p_hold}
        except Exception as e:
            logger.debug("TimeSeriesModel predict_proba error: %s", e)
            return default_probs


class _LSTMNet:
    """Helper PyTorch LSTM Module wrapper."""


class PyTorchLSTMModel:
    """
    Sequence-based Deep Learning model using PyTorch LSTM.
    Predicts directional probabilities over sequential feature windows.
    Falls back gracefully to Gradient Boosted Tree if PyTorch is absent.
    """

    FEATURE_COLS = GradientBoostedTimeSeriesModel.FEATURE_COLS

    def __init__(
        self, sequence_length: int = 10, hidden_dim: int = 32, epochs: int = 15
    ):
        self.sequence_length = sequence_length
        self.hidden_dim = hidden_dim
        self.epochs = epochs
        self.scaler = StandardScaler()
        self.tree_fallback = GradientBoostedTimeSeriesModel()
        self.model: Any = None
        self.is_trained = False

    def _build_sequences(self, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        available_cols = [c for c in self.FEATURE_COLS if c in df.columns]
        if not available_cols or len(df) < self.sequence_length + 5:
            return np.array([]), np.array([])

        df_copy = df.copy()
        df_copy["target"] = np.where(
            df_copy["close"].shift(-1) > df_copy["close"], 1, 0
        )
        df_clean = df_copy.dropna(subset=available_cols + ["target"])

        if len(df_clean) < self.sequence_length + 5:
            return np.array([]), np.array([])

        X_raw = self.scaler.fit_transform(df_clean[available_cols].to_numpy())
        y_raw = df_clean["target"].to_numpy()

        X_seq, y_seq = [], []
        for i in range(len(X_raw) - self.sequence_length):
            X_seq.append(X_raw[i : i + self.sequence_length])
            y_seq.append(y_raw[i + self.sequence_length - 1])

        return np.array(X_seq, dtype=np.float32), np.array(y_seq, dtype=np.int64)

    def fit(self, df: pd.DataFrame) -> bool:
        if not TORCH_AVAILABLE:
            return self.tree_fallback.fit(df)

        X_seq, y_seq = self._build_sequences(df)
        if len(X_seq) < 20:
            logger.warning(
                "Insufficient sequence samples for LSTM; falling back to tree model."
            )
            return self.tree_fallback.fit(df)

        try:
            input_dim = X_seq.shape[2]

            class LSTMModule(nn.Module):
                def __init__(self, in_d, h_d):
                    super().__init__()
                    self.lstm = nn.LSTM(
                        in_d, h_d, num_layers=2, batch_first=True, dropout=0.1
                    )
                    self.fc = nn.Linear(h_d, 2)

                def forward(self, x):
                    out, _ = self.lstm(x)
                    return self.fc(out[:, -1, :])

            self.model = LSTMModule(input_dim, self.hidden_dim)
            optimizer = optim.Adam(self.model.parameters(), lr=0.005)
            criterion = nn.CrossEntropyLoss()

            X_tensor = torch.tensor(X_seq)
            y_tensor = torch.tensor(y_seq)

            self.model.train()
            for _ in range(self.epochs):
                optimizer.zero_grad()
                logits = self.model(X_tensor)
                loss = criterion(logits, y_tensor)
                loss.backward()
                optimizer.step()

            self.is_trained = True
            logger.info(
                "PyTorchLSTMModel trained successfully on %d sequence samples.",
                len(X_seq),
            )
            return True
        except Exception as exc:
            logger.warning("PyTorch LSTM training failed: %s. Using fallback.", exc)
            return self.tree_fallback.fit(df)

    def predict_proba(self, df: pd.DataFrame) -> Dict[int, float]:
        if not TORCH_AVAILABLE or not self.is_trained or self.model is None:
            return self.tree_fallback.predict_proba(df)

        available_cols = [c for c in self.FEATURE_COLS if c in df.columns]
        if len(df) < self.sequence_length:
            return self.tree_fallback.predict_proba(df)

        try:
            recent_cols = df[available_cols].iloc[-self.sequence_length :].dropna()
            if len(recent_cols) < self.sequence_length:
                return self.tree_fallback.predict_proba(df)

            seq_scaled = self.scaler.transform(recent_cols.to_numpy())
            X_in = torch.tensor(seq_scaled, dtype=torch.float32).unsqueeze(0)

            self.model.eval()
            with torch.no_grad():
                logits = self.model(X_in)
                probs = torch.softmax(logits, dim=1)[0].numpy()

            p_sell = float(probs[0])
            p_buy = float(probs[1])
            return {1: p_buy, -1: p_sell, 0: max(0.0, 1.0 - p_buy - p_sell)}
        except Exception as exc:
            logger.debug("LSTM predict_proba error: %s", exc)
            return self.tree_fallback.predict_proba(df)

    def predict(self, df: pd.DataFrame) -> int:
        probs = self.predict_proba(df)
        if probs.get(1, 0.0) > 0.55:
            return 1
        elif probs.get(-1, 0.0) > 0.55:
            return -1
        return 0


class TemporalTransformerModel:
    """
    Self-Attention based Transformer for sequence forecasting.
    Falls back gracefully to Gradient Boosted Tree if PyTorch is absent.
    """

    FEATURE_COLS = GradientBoostedTimeSeriesModel.FEATURE_COLS

    def __init__(self, sequence_length: int = 10, d_model: int = 32, epochs: int = 10):
        self.sequence_length = sequence_length
        self.d_model = d_model
        self.epochs = epochs
        self.tree_fallback = GradientBoostedTimeSeriesModel()
        self.is_trained = False
        self.scaler = StandardScaler()
        self.model: Any = None

    def fit(self, df: pd.DataFrame) -> bool:
        if not TORCH_AVAILABLE:
            return self.tree_fallback.fit(df)

        try:
            available_cols = [c for c in self.FEATURE_COLS if c in df.columns]
            if len(df) < self.sequence_length + 5:
                return self.tree_fallback.fit(df)

            df_copy = df.copy()
            df_copy["target"] = np.where(
                df_copy["close"].shift(-1) > df_copy["close"], 1, 0
            )
            df_clean = df_copy.dropna(subset=available_cols + ["target"])

            if len(df_clean) < self.sequence_length + 5:
                return self.tree_fallback.fit(df)

            X_raw = self.scaler.fit_transform(df_clean[available_cols].to_numpy())
            y_raw = df_clean["target"].to_numpy()

            X_seq_list, y_seq_list = [], []
            for i in range(len(X_raw) - self.sequence_length):
                X_seq_list.append(X_raw[i : i + self.sequence_length])
                y_seq_list.append(y_raw[i + self.sequence_length - 1])

            X_seq = np.array(X_seq_list, dtype=np.float32)
            y_seq = np.array(y_seq_list, dtype=np.int64)

            in_dim = X_seq.shape[2]

            class TransformerModule(nn.Module):
                def __init__(self, in_d, d_m):
                    super().__init__()
                    self.proj = nn.Linear(in_d, d_m)
                    encoder_layer = nn.TransformerEncoderLayer(
                        d_model=d_m,
                        nhead=2,
                        dim_feedforward=32,
                        batch_first=True,
                    )
                    self.transformer = nn.TransformerEncoder(
                        encoder_layer, num_layers=1
                    )
                    self.fc = nn.Linear(d_m, 2)

                def forward(self, x):
                    h = self.proj(x)
                    out = self.transformer(h)
                    return self.fc(out[:, -1, :])

            self.model = TransformerModule(in_dim, self.d_model)
            optimizer = optim.Adam(self.model.parameters(), lr=0.005)
            criterion = nn.CrossEntropyLoss()

            X_tensor = torch.tensor(X_seq)
            y_tensor = torch.tensor(y_seq)

            self.model.train()
            for _ in range(self.epochs):
                optimizer.zero_grad()
                logits = self.model(X_tensor)
                loss = criterion(logits, y_tensor)
                loss.backward()
                optimizer.step()

            self.is_trained = True
            logger.info("TemporalTransformerModel trained successfully.")
            return True
        except Exception as exc:
            logger.warning(
                "TemporalTransformerModel fit error: %s. Using fallback.", exc
            )
            return self.tree_fallback.fit(df)

    def predict_proba(self, df: pd.DataFrame) -> Dict[int, float]:
        if not TORCH_AVAILABLE or not self.is_trained or self.model is None:
            return self.tree_fallback.predict_proba(df)

        available_cols = [c for c in self.FEATURE_COLS if c in df.columns]
        if len(df) < self.sequence_length:
            return self.tree_fallback.predict_proba(df)

        try:
            recent_cols = df[available_cols].iloc[-self.sequence_length :].dropna()
            if len(recent_cols) < self.sequence_length:
                return self.tree_fallback.predict_proba(df)

            seq_scaled = self.scaler.transform(recent_cols.to_numpy())
            X_in = torch.tensor(seq_scaled, dtype=torch.float32).unsqueeze(0)

            self.model.eval()
            with torch.no_grad():
                logits = self.model(X_in)
                probs = torch.softmax(logits, dim=1)[0].numpy()

            p_sell = float(probs[0])
            p_buy = float(probs[1])
            return {1: p_buy, -1: p_sell, 0: max(0.0, 1.0 - p_buy - p_sell)}
        except Exception as exc:
            logger.debug("Transformer predict_proba error: %s", exc)
            return self.tree_fallback.predict_proba(df)

    def predict(self, df: pd.DataFrame) -> int:
        probs = self.predict_proba(df)
        if probs.get(1, 0.0) > 0.55:
            return 1
        elif probs.get(-1, 0.0) > 0.55:
            return -1
        return 0
