"""Tests for Dukascopy CSV ingestion."""

from pathlib import Path

import pandas as pd
import pytest

from pyfx.data.dukascopy import (
    DUKASCOPY_INSTRUMENTS,
    FILENAME_TO_INSTRUMENT,
    INSTRUMENT_FROM_DUKASCOPY,
    canonical_parquet_name,
    ingest_to_parquet,
    read_dukascopy_csv,
)


@pytest.fixture()
def dukascopy_csv(tmp_path: Path) -> Path:
    """Create a minimal Dukascopy-format CSV file."""
    csv = tmp_path / "eurusd.csv"
    csv.write_text(
        "timestamp,open,high,low,close,volume\n"
        "1735689600000,1.03526,1.03530,1.03520,1.03528,100.5\n"
        "1735689660000,1.03528,1.03535,1.03525,1.03530,200.0\n"
        "1735689720000,1.03530,1.03540,1.03528,1.03538,150.0\n"
    )
    return csv


@pytest.fixture()
def dukascopy_csv_no_volume(tmp_path: Path) -> Path:
    """Dukascopy CSV without volume column."""
    csv = tmp_path / "eurusd_novol.csv"
    csv.write_text(
        "timestamp,open,high,low,close\n"
        "1735689600000,1.03526,1.03530,1.03520,1.03528\n"
        "1735689660000,1.03528,1.03535,1.03525,1.03530\n"
    )
    return csv


def test_read_dukascopy_csv(dukascopy_csv: Path) -> None:
    """Read a Dukascopy CSV and verify OHLCV DataFrame."""
    df = read_dukascopy_csv(dukascopy_csv)

    assert len(df) == 3
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.tz is not None  # UTC
    assert df.iloc[0]["open"] == pytest.approx(1.03526)
    assert df.iloc[0]["volume"] == pytest.approx(100.5)


def test_read_dukascopy_csv_no_volume(dukascopy_csv_no_volume: Path) -> None:
    """Volume defaults to 0 when column is missing."""
    df = read_dukascopy_csv(dukascopy_csv_no_volume)

    assert len(df) == 2
    assert "volume" in df.columns
    assert df["volume"].sum() == 0.0


def test_read_dukascopy_csv_missing_column(tmp_path: Path) -> None:
    """Raises ValueError when a required column is absent."""
    csv = tmp_path / "bad.csv"
    csv.write_text("timestamp,open,high,low\n1735689600000,1.0,1.1,0.9\n")

    with pytest.raises(ValueError, match="Missing required column: close"):
        read_dukascopy_csv(csv)


def test_ingest_to_parquet(dukascopy_csv: Path, tmp_path: Path) -> None:
    """Ingested Parquet matches the source CSV data."""
    out = ingest_to_parquet(dukascopy_csv, tmp_path / "output.parquet")

    assert out.exists()
    df = pd.read_parquet(out)
    assert len(df) == 3
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_ingest_to_parquet_default_path(dukascopy_csv: Path) -> None:
    """Default output path is .parquet alongside the CSV."""
    out = ingest_to_parquet(dukascopy_csv)

    assert out == dukascopy_csv.with_suffix(".parquet")
    assert out.exists()


# ── Instrument mapping tests ──


def test_dukascopy_instruments_roundtrip() -> None:
    """Forward and reverse mappings are consistent."""
    for instrument, duka_id in DUKASCOPY_INSTRUMENTS.items():
        assert INSTRUMENT_FROM_DUKASCOPY[duka_id] == instrument


def test_filename_to_instrument_mapping() -> None:
    """Filename prefix maps back to full instrument name."""
    assert FILENAME_TO_INSTRUMENT["EURUSD"] == "EUR/USD"
    assert FILENAME_TO_INSTRUMENT["XAUUSD"] == "XAU/USD"
    assert FILENAME_TO_INSTRUMENT["OILUSD"] == "OIL/USD"


def test_canonical_parquet_name() -> None:
    """Canonical filename follows expected pattern."""
    import datetime

    name = canonical_parquet_name(
        "EUR/USD",
        datetime.date(2025, 1, 1),
        datetime.date(2025, 12, 31),
        "M1",
    )
    assert name == "EURUSD_2025-01-01_2025-12-31_M1.parquet"


def test_canonical_parquet_name_different_tf() -> None:
    """Timeframe is included in filename."""
    import datetime

    name = canonical_parquet_name(
        "GBP/USD",
        datetime.date(2025, 3, 1),
        datetime.date(2025, 6, 30),
        "H1",
    )
    assert name == "GBPUSD_2025-03-01_2025-06-30_H1.parquet"
