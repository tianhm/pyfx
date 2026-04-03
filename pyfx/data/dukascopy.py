"""Ingest Dukascopy CSV data into pyfx-compatible Parquet format.

Dukascopy CSV format (from dukascopy-node):
  timestamp (epoch ms), open, high, low, close[, volume]

This module converts that into a Parquet file with a UTC DatetimeIndex
and OHLCV columns suitable for NautilusTrader bar ingestion.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pandas as pd

# Mapping from pyfx instrument name to dukascopy-node instrument ID
DUKASCOPY_INSTRUMENTS: dict[str, str] = {
    "EUR/USD": "eurusd",
    "GBP/USD": "gbpusd",
    "USD/JPY": "usdjpy",
    "AUD/USD": "audusd",
    "USD/CHF": "usdchf",
    "USD/CAD": "usdcad",
    "NZD/USD": "nzdusd",
    "XAU/USD": "xauusd",
    "OIL/USD": "lightcmdusd",
}

# Reverse: dukascopy ID -> pyfx instrument
INSTRUMENT_FROM_DUKASCOPY: dict[str, str] = {v: k for k, v in DUKASCOPY_INSTRUMENTS.items()}

# Filename prefix (no slash) -> pyfx instrument
FILENAME_TO_INSTRUMENT: dict[str, str] = {
    k.replace("/", ""): k for k in DUKASCOPY_INSTRUMENTS
}


def canonical_parquet_name(
    instrument: str,
    start: datetime.date,
    end: datetime.date,
    timeframe: str = "M1",
) -> str:
    """Build canonical Parquet filename for a dataset.

    Example: ``EURUSD_2025-01-01_2026-03-31_M1.parquet``
    """
    prefix = instrument.replace("/", "")
    return f"{prefix}_{start.isoformat()}_{end.isoformat()}_{timeframe}.parquet"


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
