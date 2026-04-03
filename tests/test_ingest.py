"""Tests for Dukascopy CSV ingestion."""

from pathlib import Path

import pandas as pd
import pytest

from pyfx.data.dukascopy import ingest_to_parquet, read_dukascopy_csv


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
