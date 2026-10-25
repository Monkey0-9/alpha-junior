"""Market-data contract tests for rejecting fabricated and malformed prices."""

import pandas as pd

from nexus.core.alpha import AlphaEngine


def test_normalizer_rejects_missing_ohlcv_columns():
    engine = AlphaEngine()
    malformed = pd.DataFrame({"close": [100.0, 101.0], "volume": [1000, 1100]})
    normalized = engine._normalize_columns(malformed, limit=10)
    assert normalized.empty
    assert {"open", "high", "low", "close", "volume"}.issubset(normalized.columns)


def test_normalizer_drops_nonfinite_and_nonpositive_rows():
    engine = AlphaEngine()
    data = pd.DataFrame(
        {
            "open": [100.0, float("nan"), -1.0],
            "high": [102.0, 103.0, 104.0],
            "low": [99.0, 99.0, 100.0],
            "close": [101.0, 102.0, 103.0],
            "volume": [1000.0, 1100.0, 1200.0],
        }
    )
    normalized = engine._normalize_columns(data, limit=10)
    assert len(normalized) == 1
    assert (normalized[["open", "high", "low", "close"]] > 0).all().all()
    assert normalized["volume"].ge(0).all()
