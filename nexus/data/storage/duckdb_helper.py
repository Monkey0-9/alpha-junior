import duckdb
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class DuckDBHelper:
    """
    Helper to create DuckDB connections and register the Parquet data lake as virtual tables.
    """

    def __init__(self, db_path: str = ":memory:", lake_base_path: str = "data/lake"):
        self.db_path = db_path
        self.lake_base_path = Path(lake_base_path)
        self.conn = duckdb.connect(database=self.db_path)
        self._register_lake()

    def _register_lake(self) -> None:
        """
        Registers the data lake parquet files as a DuckDB view/table using Hive partitioning.
        """
        if not self.lake_base_path.exists():
            logger.warning(
                f"Data lake path {self.lake_base_path} does not exist yet. View not created."
            )
            return

        try:
            # Create a view that reads all parquet files in the lake and infers hive partitioning
            # source={source}/symbol={symbol}/...
            query = f"""
            CREATE OR REPLACE VIEW market_data AS
            SELECT * FROM read_parquet('{self.lake_base_path}/*/*/*.parquet', hive_partitioning=true);
            """  # noqa: S608 # nosec B608
            self.conn.execute(query)
            logger.info("Registered 'market_data' view in DuckDB.")
        except Exception as e:
            logger.error(f"Failed to register DuckDB view: {e}")

    def query(self, sql: str) -> duckdb.DuckDBPyConnection:
        """Execute a query and return a DuckDB connection/relation."""
        return self.conn.execute(sql)

    def close(self) -> None:
        """Close the DuckDB connection."""
        self.conn.close()
