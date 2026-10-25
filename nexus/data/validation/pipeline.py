import pandas as pd
import logging
from typing import Tuple, List, Callable
from .rules import (
    check_timestamp_monotonicity,
    check_duplicates,
    check_missing_values,
    check_negative_prices,
)

logger = logging.getLogger(__name__)


class ValidationPipeline:
    """
    Runs a batch of market data through all validation rules.
    """

    def __init__(self) -> None:
        self.rules: List[Callable[[pd.DataFrame], Tuple[bool, pd.DataFrame]]] = [
            check_duplicates,
            check_timestamp_monotonicity,
            check_negative_prices,
            check_missing_values,
        ]

    def validate_batch(self, df: pd.DataFrame) -> Tuple[bool, pd.DataFrame]:
        """
        Validates a dataframe and returns a cleaned version.

        Args:
            df: DataFrame to validate.

        Returns:
            Tuple (all_passed: bool, clean_df: pd.DataFrame)
        """
        if df is None or df.empty:
            logger.warning("ValidationPipeline received empty DataFrame.")
            return True, df

        all_passed = True
        clean_df = df.copy()

        # Ensure timestamp is datetime before validation
        if "timestamp" in clean_df.columns:
            clean_df["timestamp"] = pd.to_datetime(clean_df["timestamp"])

        for rule in self.rules:
            passed, clean_df = rule(clean_df)
            if not passed:
                all_passed = False

        return all_passed, clean_df
