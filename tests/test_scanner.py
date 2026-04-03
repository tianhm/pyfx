"""Tests for the data directory scanner."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from pyfx.data.scanner import (
    _is_generated,
    _parse_instrument_from_filename,
    _parse_timeframe_from_filename,
)


class TestParseInstrument:
    def test_eurusd(self) -> None:
        assert _parse_instrument_from_filename("EURUSD_2025_M1.parquet") == "EUR/USD"

    def test_xauusd(self) -> None:
        assert _parse_instrument_from_filename("XAUUSD_2025_M1.parquet") == "XAU/USD"

    def test_gbpusd(self) -> None:
        assert _parse_instrument_from_filename("GBPUSD_test.parquet") == "GBP/USD"

    def test_oilusd(self) -> None:
        assert _parse_instrument_from_filename("OILUSD_data.parquet") == "OIL/USD"

    def test_unknown(self) -> None:
        assert _parse_instrument_from_filename("random_file.parquet") is None

    def test_case_insensitive(self) -> None:
        assert _parse_instrument_from_filename("eurusd_data.parquet") == "EUR/USD"


class TestParseTimeframe:
    def test_m1(self) -> None:
        assert _parse_timeframe_from_filename("EURUSD_2025_M1.parquet") == "M1"

    def test_m5(self) -> None:
        assert _parse_timeframe_from_filename("EURUSD_2025_M5.parquet") == "M5"

    def test_default(self) -> None:
        assert _parse_timeframe_from_filename("EURUSD_2025.parquet") == "M1"


class TestIsGenerated:
    def test_generated(self) -> None:
        assert _is_generated("EURUSD_90d_M1.parquet") is True
        assert _is_generated("EURUSD_365d_M1.parquet") is True

    def test_not_generated(self) -> None:
        assert _is_generated("EURUSD_2025-01-01_2025-12-31_M1.parquet") is False
        assert _is_generated("EURUSD_test_M1.parquet") is False


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """Create a temp data directory with sample Parquet files."""
    d = tmp_path / "data"
    d.mkdir()

    # Create a minimal Parquet file
    df = pd.DataFrame(
        {
            "open": [1.10, 1.11],
            "high": [1.12, 1.13],
            "low": [1.09, 1.10],
            "close": [1.11, 1.12],
            "volume": [100.0, 200.0],
        },
        index=pd.date_range("2025-01-01", periods=2, freq="1min", tz="UTC"),
    )
    df.to_parquet(d / "EURUSD_2025_M1.parquet")
    df.to_parquet(d / "GBPUSD_90d_M1.parquet")

    return d


@pytest.mark.django_db()
def test_scan_registers_new_files(data_dir: Path) -> None:
    from pyfx.data.scanner import scan_data_directory
    from pyfx.web.dashboard.models import Dataset

    registered, already = scan_data_directory(data_dir)
    assert registered == 2
    assert already == 0
    assert Dataset.objects.count() == 2


@pytest.mark.django_db()
def test_scan_skips_already_tracked(data_dir: Path) -> None:
    from pyfx.data.scanner import scan_data_directory

    scan_data_directory(data_dir)
    registered, already = scan_data_directory(data_dir)
    assert registered == 0
    assert already == 2


@pytest.mark.django_db()
def test_scan_empty_directory(tmp_path: Path) -> None:
    from pyfx.data.scanner import scan_data_directory

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    registered, already = scan_data_directory(empty_dir)
    assert registered == 0
    assert already == 0


@pytest.mark.django_db()
def test_scan_detects_source_generated(data_dir: Path) -> None:
    from pyfx.data.scanner import scan_data_directory
    from pyfx.web.dashboard.models import Dataset

    scan_data_directory(data_dir)
    gbp = Dataset.objects.get(instrument="GBP/USD")
    assert gbp.source == Dataset.SOURCE_GENERATED

    eur = Dataset.objects.get(instrument="EUR/USD")
    assert eur.source == Dataset.SOURCE_MANUAL


@pytest.mark.django_db()
def test_scan_reads_metadata(data_dir: Path) -> None:
    from pyfx.data.scanner import scan_data_directory
    from pyfx.web.dashboard.models import Dataset

    scan_data_directory(data_dir)
    ds = Dataset.objects.get(instrument="EUR/USD")
    assert ds.row_count == 2
    assert ds.file_size_bytes > 0
    assert ds.status == Dataset.STATUS_READY


@pytest.mark.django_db()
def test_scan_skips_empty_parquet(tmp_path: Path) -> None:
    from pyfx.data.scanner import scan_data_directory
    from pyfx.web.dashboard.models import Dataset

    d = tmp_path / "data"
    d.mkdir()
    # Create empty Parquet
    df = pd.DataFrame(
        {"open": pd.Series(dtype=float), "high": pd.Series(dtype=float),
         "low": pd.Series(dtype=float), "close": pd.Series(dtype=float),
         "volume": pd.Series(dtype=float)},
        index=pd.DatetimeIndex([], tz="UTC"),
    )
    df.to_parquet(d / "EURUSD_empty_M1.parquet")

    registered, _ = scan_data_directory(d)
    assert registered == 0
    assert Dataset.objects.count() == 0


@pytest.mark.django_db()
def test_scan_default_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scanner uses settings.data_dir when data_dir is None."""
    from pyfx.data.scanner import scan_data_directory

    # Point settings.data_dir at our temp dir
    monkeypatch.setattr("pyfx.core.config.settings.data_dir", tmp_path)

    df = pd.DataFrame(
        {"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.0], "volume": [0.0]},
        index=pd.date_range("2025-06-01", periods=1, freq="1min", tz="UTC"),
    )
    df.to_parquet(tmp_path / "EURUSD_test_M1.parquet")

    registered, _ = scan_data_directory(data_dir=None)
    assert registered == 1


@pytest.mark.django_db()
def test_scan_skips_unknown_instruments(tmp_path: Path) -> None:
    from pyfx.data.scanner import scan_data_directory
    from pyfx.web.dashboard.models import Dataset

    d = tmp_path / "data"
    d.mkdir()
    df = pd.DataFrame(
        {"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.0], "volume": [0.0]},
        index=pd.date_range("2025-01-01", periods=1, freq="1min", tz="UTC"),
    )
    df.to_parquet(d / "UNKNOWN_data_M1.parquet")

    registered, _ = scan_data_directory(d)
    assert registered == 0
    assert Dataset.objects.count() == 0
