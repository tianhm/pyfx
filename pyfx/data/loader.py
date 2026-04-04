"""Unified data loading for backtests (CSV and Parquet with UTC handling)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import pandas as pd


def load_backtest_data(
    data_file: Path,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """Load bar data from CSV or Parquet, filter to the date range.

    Args:
        data_file: Path to CSV or Parquet file with OHLCV columns.
        start: Start of the date range (inclusive).
        end: End of the date range (inclusive).

    Returns:
        Filtered DataFrame with a UTC-aware DatetimeIndex.

    Raises:
        FileNotFoundError: If data_file does not exist.
        ValueError: If the filtered data is empty.
    """
    import pandas as pd

    if not data_file.exists():
        raise FileNotFoundError(f"Data file not found: {data_file}")

    if data_file.suffix == ".parquet":
        bars_df = pd.read_parquet(data_file)
    else:
        bars_df = pd.read_csv(data_file, index_col=0, parse_dates=True)

    idx = cast("pd.DatetimeIndex", bars_df.index)
    if idx.tz is None:
        bars_df.index = idx.tz_localize("UTC")

    # Ensure start/end are tz-aware to match the data index
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)

    bars_df = bars_df.loc[start:end]  # type: ignore[misc]

    if bars_df.empty:
        raise ValueError("No data in the specified date range")

    return bars_df
