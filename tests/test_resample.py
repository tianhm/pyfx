"""Tests for the shared bar resampling and indicator computation module."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pyfx.data.resample import compute_indicator, load_bars, resample_bars


def _make_m1_bars(n: int = 300) -> pd.DataFrame:
    """Create synthetic 1-minute OHLCV bars."""
    rng = np.random.default_rng(42)
    timestamps = pd.date_range("2024-01-02", periods=n, freq="min", tz="UTC")
    close = 1.1000 + np.cumsum(rng.normal(0, 0.0001, n))
    return pd.DataFrame(
        {
            "open": close - rng.uniform(0, 0.0002, n),
            "high": close + rng.uniform(0, 0.0003, n),
            "low": close - rng.uniform(0, 0.0003, n),
            "close": close,
            "volume": rng.uniform(100, 1000, n),
        },
        index=timestamps,
    )


class TestResampleBars:
    def test_resample_5min(self) -> None:
        df = _make_m1_bars(300)
        result = resample_bars(df, "5-MINUTE-LAST-EXTERNAL")
        assert len(result) == 60
        assert list(result.columns) == ["open", "high", "low", "close", "volume"]

    def test_resample_60min(self) -> None:
        df = _make_m1_bars(300)
        result = resample_bars(df, "60-MINUTE-LAST-EXTERNAL")
        assert len(result) == 5

    def test_resample_preserves_ohlc_semantics(self) -> None:
        df = _make_m1_bars(300)
        result = resample_bars(df, "5-MINUTE-LAST-EXTERNAL")
        # High should be >= low for every candle
        assert (result["high"] >= result["low"]).all()
        # Volume should be positive
        assert (result["volume"] > 0).all()

    def test_resample_unsupported_aggregation(self) -> None:
        df = _make_m1_bars(10)
        with pytest.raises(ValueError, match="Unsupported aggregation"):
            resample_bars(df, "5-TICK-LAST-EXTERNAL")

    def test_resample_no_volume(self) -> None:
        df = _make_m1_bars(60).drop(columns=["volume"])
        result = resample_bars(df, "5-MINUTE-LAST-EXTERNAL")
        assert "volume" not in result.columns
        assert len(result) == 12

    def test_resample_hour(self) -> None:
        df = _make_m1_bars(300)
        result = resample_bars(df, "1-HOUR-LAST-EXTERNAL")
        assert len(result) == 5

    def test_resample_day(self) -> None:
        timestamps = pd.date_range("2024-01-02", periods=1440, freq="min", tz="UTC")
        rng = np.random.default_rng(42)
        close = 1.1 + np.cumsum(rng.normal(0, 0.0001, 1440))
        df = pd.DataFrame(
            {
                "open": close,
                "high": close + 0.001,
                "low": close - 0.001,
                "close": close,
                "volume": rng.uniform(100, 1000, 1440),
            },
            index=timestamps,
        )
        result = resample_bars(df, "1-DAY-LAST-EXTERNAL")
        assert len(result) == 1


class TestLoadBars:
    def test_load_parquet(self, tmp_path: Path) -> None:
        df = _make_m1_bars(100)
        path = tmp_path / "test.parquet"
        df.to_parquet(path)
        loaded = load_bars(str(path))
        assert len(loaded) == 100
        assert isinstance(loaded.index, pd.DatetimeIndex)
        assert loaded.index.tz is not None

    def test_load_csv(self, tmp_path: Path) -> None:
        df = _make_m1_bars(100)
        path = tmp_path / "test.csv"
        df.to_csv(path)
        loaded = load_bars(str(path))
        assert len(loaded) == 100

    def test_load_with_resample(self, tmp_path: Path) -> None:
        df = _make_m1_bars(300)
        path = tmp_path / "test.parquet"
        df.to_parquet(path)
        loaded = load_bars(str(path), timeframe="5-MINUTE-LAST-EXTERNAL")
        assert len(loaded) == 60

    def test_load_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_bars("/nonexistent/path.parquet")

    def test_load_non_datetime_index(self, tmp_path: Path) -> None:
        """Load parquet where index is object type (not DatetimeIndex)."""
        rng = np.random.default_rng(42)
        n = 10
        close = 1.1 + np.cumsum(rng.normal(0, 0.0001, n))
        df = pd.DataFrame(
            {
                "open": close,
                "high": close + 0.001,
                "low": close - 0.001,
                "close": close,
                "volume": rng.uniform(100, 1000, n),
            },
        )
        # Write with string index that looks like timestamps
        df.index = [f"2024-01-02 00:0{i}:00" for i in range(n)]
        path = tmp_path / "string_index.parquet"
        df.to_parquet(path)
        loaded = load_bars(str(path))
        assert isinstance(loaded.index, pd.DatetimeIndex)
        assert loaded.index.tz is not None

    def test_load_naive_timestamps(self, tmp_path: Path) -> None:
        """Load bars with naive (no timezone) timestamps — should auto-localize."""
        timestamps = pd.date_range("2024-01-02", periods=60, freq="min")
        rng = np.random.default_rng(42)
        close = 1.1 + np.cumsum(rng.normal(0, 0.0001, 60))
        df = pd.DataFrame(
            {
                "open": close,
                "high": close + 0.001,
                "low": close - 0.001,
                "close": close,
                "volume": rng.uniform(100, 1000, 60),
            },
            index=timestamps,
        )
        path = tmp_path / "naive.parquet"
        df.to_parquet(path)
        loaded = load_bars(str(path))
        assert loaded.index.tz is not None


class TestComputeIndicator:
    def setup_method(self) -> None:
        self.df = _make_m1_bars(300)

    def test_sma(self) -> None:
        result = compute_indicator(self.df, "sma", 20)
        assert isinstance(result, pd.Series)
        assert len(result) == 300
        # First 19 values should be NaN
        assert result.iloc[:19].isna().all()
        assert result.iloc[19:].notna().all()

    def test_ema(self) -> None:
        result = compute_indicator(self.df, "ema", 20)
        assert isinstance(result, pd.Series)
        assert len(result) == 300
        # EMA should have values from the start (ewm doesn't produce NaN)
        assert result.notna().all()

    def test_rsi(self) -> None:
        result = compute_indicator(self.df, "rsi", 14)
        assert isinstance(result, pd.Series)
        # RSI values should be between 0 and 100 (after initial NaN)
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_macd(self) -> None:
        result = compute_indicator(self.df, "macd", 12)
        assert isinstance(result, dict)
        assert "macd" in result
        assert "signal" in result
        assert "histogram" in result
        assert len(result["macd"]) == 300
        assert len(result["signal"]) == 300
        assert len(result["histogram"]) == 300

    def test_atr(self) -> None:
        result = compute_indicator(self.df, "atr", 14)
        assert isinstance(result, pd.Series)
        # ATR should be positive (after initial NaN)
        valid = result.dropna()
        assert (valid >= 0).all()

    def test_unknown_indicator(self) -> None:
        with pytest.raises(ValueError, match="Unknown indicator"):
            compute_indicator(self.df, "bollinger", 20)
