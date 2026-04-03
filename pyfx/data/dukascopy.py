"""Ingest Dukascopy CSV data into pyfx-compatible Parquet format.

Dukascopy CSV format (from dukascopy-node):
  timestamp (epoch ms), open, high, low, close[, volume]

This module converts that into a Parquet file with a UTC DatetimeIndex
and OHLCV columns suitable for NautilusTrader bar ingestion.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def read_dukascopy_csv(path: Path) -> pd.DataFrame:
    """Read a Dukascopy CSV file and return a normalized OHLCV DataFrame.

    Args:
        path: Path to the CSV file from dukascopy-node.

    Returns:
        DataFrame with columns [open, high, low, close, volume]
        and a UTC DatetimeIndex.
    """
    df = pd.read_csv(path)

    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    df = df.sort_index()

    # Ensure required columns exist
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    # Add volume column if missing (dukascopy-node may omit it)
    if "volume" not in df.columns:
        df["volume"] = 0.0

    # Keep only OHLCV columns
    df = df[["open", "high", "low", "close", "volume"]]

    # Drop any rows where all OHLC values are identical and zero (flat/no-trade bars)
    # but keep flat bars where price is non-zero (valid market data)

    return df


def ingest_to_parquet(
    csv_path: Path,
    output_path: Path | None = None,
) -> Path:
    """Read a Dukascopy CSV and save as Parquet.

    Args:
        csv_path: Path to the Dukascopy CSV file.
        output_path: Output Parquet path. If None, uses same name with .parquet extension.

    Returns:
        Path to the saved Parquet file.
    """
    df = read_dukascopy_csv(csv_path)

    if output_path is None:
        output_path = csv_path.with_suffix(".parquet")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path)

    return output_path
