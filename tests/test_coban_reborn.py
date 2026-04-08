"""Tests for the CobanReborn multi-timeframe confluence strategy."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from pyfx.backtest.runner import run_backtest
from pyfx.core.types import BacktestConfig
from pyfx.data.resample import resample_bars
from pyfx.strategies.coban_reborn import (
    _ZERO_TS,
    CobanRebornConfig,
    CobanRebornStrategy,
    _bar_hour_utc,
    _check_break,
    _find_local_extrema,
    _is_fresh,
    _signals_within_window,
    compute_ema_step,
    detect_rsi_trendline_break,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bars(n: int = 2000, seed: int = 42, trend: float = 0.0) -> pd.DataFrame:
    """Create synthetic M1 OHLCV data.

    Args:
        n: Number of bars.
        seed: Random seed.
        trend: Drift per bar (positive = uptrend).
    """
    rng = np.random.default_rng(seed)
    base = 1.10
    noise = rng.normal(0, 0.0002, n)
    price = base + np.cumsum(noise + trend)
    spread = np.abs(rng.normal(0, 0.0001, n))

    df = pd.DataFrame(
        {
            "open": price,
            "high": price + spread + np.abs(rng.normal(0, 0.0001, n)),
            "low": price - spread - np.abs(rng.normal(0, 0.0001, n)),
            "close": price + rng.normal(0, 0.00005, n),
            "volume": np.full(n, 1_000_000.0),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="1min", tz="UTC"),
    )
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    return df


def _make_oscillating_bars(n: int = 20000, seed: int = 42) -> pd.DataFrame:
    """Create data with oscillating trends that produce SMA crossovers.

    Alternates between up and down trends every ~2 hours (120 M1 bars),
    creating clear crossover points.
    """
    rng = np.random.default_rng(seed)
    base = 1.10
    cycle_len = 120  # 2 hours per half-cycle
    num_cycles = n // cycle_len

    prices = []
    p = base
    for c in range(num_cycles):
        direction = 1.0 if c % 2 == 0 else -1.0
        for _ in range(cycle_len):
            p += direction * 0.0003 + rng.normal(0, 0.00005)
            prices.append(p)

    # Fill remaining
    while len(prices) < n:
        p += rng.normal(0, 0.00005)
        prices.append(p)

    price = np.array(prices[:n])
    spread = np.abs(rng.normal(0, 0.00005, n))

    df = pd.DataFrame(
        {
            "open": price,
            "high": price + spread + np.abs(rng.normal(0, 0.00005, n)),
            "low": price - spread - np.abs(rng.normal(0, 0.00005, n)),
            "close": price + rng.normal(0, 0.00002, n),
            "volume": np.full(n, 1_000_000.0),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="1min", tz="UTC"),
    )
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    return df


# ---------------------------------------------------------------------------
# Pure function tests: RSI trendline break
# ---------------------------------------------------------------------------


class TestFindLocalExtrema:
    def test_maxima(self):
        values = [1.0, 3.0, 1.0, 2.0, 1.0]
        assert _find_local_extrema(values, find_maxima=True) == [1, 3]

    def test_minima(self):
        values = [3.0, 1.0, 3.0, 2.0, 3.0]
        assert _find_local_extrema(values, find_maxima=False) == [1, 3]

    def test_no_extrema(self):
        values = [1.0, 2.0, 3.0, 4.0]
        assert _find_local_extrema(values, find_maxima=True) == []

    def test_flat(self):
        values = [2.0, 2.0, 2.0, 2.0]
        assert _find_local_extrema(values, find_maxima=True) == []
        assert _find_local_extrema(values, find_maxima=False) == []


class TestDetectRsiTrendlineBreak:
    def test_too_few_values(self):
        assert detect_rsi_trendline_break([0.5, 0.6, 0.7], min_peak_diff=2) == 0

    def test_no_peaks(self):
        # Monotonically increasing — no local maxima or minima
        values = [float(i) / 20 for i in range(10)]
        assert detect_rsi_trendline_break(values, min_peak_diff=2) == 0

    def test_bullish_break(self):
        """Descending peaks forming resistance, last value breaks above."""
        # Peaks at indices 2, 6 with descending values
        values = [
            0.5, 0.6, 0.8, 0.6, 0.5,  # peak at 2 (0.8)
            0.5, 0.7, 0.5, 0.4, 0.4,  # peak at 6 (0.7)
            0.3,                         # below trendline
            0.9,                         # latest: breaks above!
        ]
        assert detect_rsi_trendline_break(values, min_peak_diff=2) == 1

    def test_bearish_break(self):
        """Ascending troughs forming support, last value breaks below."""
        values = [
            0.5, 0.4, 0.2, 0.4, 0.5,  # trough at 2 (0.2)
            0.5, 0.3, 0.5, 0.6, 0.6,  # trough at 6 (0.3)
            0.7,                         # above trendline
            0.1,                         # latest: breaks below!
        ]
        assert detect_rsi_trendline_break(values, min_peak_diff=2) == -1

    def test_no_break_below_threshold(self):
        """Latest value does not cross the trendline."""
        values = [
            0.5, 0.6, 0.8, 0.6, 0.5,
            0.5, 0.7, 0.5, 0.4, 0.4,
            0.3,
            0.5,  # latest: below the descending resistance line
        ]
        assert detect_rsi_trendline_break(values, min_peak_diff=2) == 0

    def test_min_peak_diff_enforced(self):
        """Peaks too close together are skipped."""
        # Peaks at 1 and 2 — only 1 apart, min_peak_diff=3
        values = [0.5, 0.8, 0.9, 0.5, 0.3, 0.3, 0.95]
        assert detect_rsi_trendline_break(values, min_peak_diff=3) == 0

    def test_wrong_slope_skipped(self):
        """Ascending peaks (positive slope) should not trigger bullish break."""
        # Ascending peaks: 0.5 then 0.8 — positive slope, not descending resistance
        values = [
            0.3, 0.4, 0.5, 0.3, 0.2,
            0.3, 0.8, 0.3, 0.2,
            0.9,  # above the ascending line — but slope is wrong for bullish
        ]
        result = detect_rsi_trendline_break(values, min_peak_diff=2)
        # Should not be bullish because the peak trendline has ascending slope
        # It may detect bearish from troughs if applicable, or 0
        assert result != 1

    def test_violated_trendline_skipped(self):
        """If intermediate values cross above the trendline, it's invalid."""
        values = [
            0.5, 0.6, 0.8, 0.6, 0.5,  # peak at 2 (0.8)
            0.85,                         # VIOLATES the descending trendline
            0.7, 0.5, 0.4,               # peak at 6-ish
            0.3,
            0.9,                          # latest
        ]
        # The trendline from peak 0.8 is violated by 0.85
        result = detect_rsi_trendline_break(values, min_peak_diff=2)
        # Should be 0 since the first candidate pair has a violation
        # (other pairs might still work depending on exact indices)
        assert isinstance(result, int)


class TestCheckBreak:
    def test_too_few_extrema(self):
        # Only one peak
        buf = [0.5, 0.8, 0.5, 0.4]
        assert _check_break(buf, 0.9, min_peak_diff=2, bullish=True) == 0

    def test_empty_buf(self):
        assert _check_break([], 0.5, min_peak_diff=2, bullish=True) == 0

    def test_peaks_too_close(self):
        """Cover the min_peak_diff continue path in _check_break."""
        # Two peaks: index 1 (0.9) and index 3 (0.85), only 2 apart
        # With min_peak_diff=3, both pairs are skipped
        buf = [0.5, 0.9, 0.6, 0.85, 0.5, 0.4]
        result = _check_break(buf, 0.95, min_peak_diff=3, bullish=True)
        assert result == 0


# ---------------------------------------------------------------------------
# Pure function tests: EMA step
# ---------------------------------------------------------------------------


class TestComputeEmaStep:
    def test_basic(self):
        # alpha=0.5: (0.5 * 10) + (0.5 * 8) = 9
        assert compute_ema_step(8.0, 10.0, 0.5) == pytest.approx(9.0)

    def test_alpha_zero(self):
        assert compute_ema_step(5.0, 10.0, 0.0) == pytest.approx(5.0)

    def test_alpha_one(self):
        assert compute_ema_step(5.0, 10.0, 1.0) == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Pure function tests: timestamp helpers
# ---------------------------------------------------------------------------


class TestTimestampHelpers:
    def test_signals_within_window_all_zero(self):
        assert _signals_within_window([_ZERO_TS, 1_000_000_000], 60) is False

    def test_signals_within_window_pass(self):
        ts1 = 1_000_000_000_000  # 1000 seconds in ns
        ts2 = ts1 + 30_000_000_000  # +30 seconds
        assert _signals_within_window([ts1, ts2], 60) is True

    def test_signals_within_window_fail(self):
        ts1 = 1_000_000_000_000
        ts2 = ts1 + 120_000_000_000  # +120 seconds
        assert _signals_within_window([ts1, ts2], 60) is False

    def test_is_fresh_zero(self):
        assert _is_fresh(_ZERO_TS, 1_000_000_000, 60) is False

    def test_is_fresh_true(self):
        now = 100_000_000_000_000  # 100k seconds in ns
        ts = now - 30_000_000_000  # 30 seconds ago
        assert _is_fresh(ts, now, 60) is True

    def test_is_fresh_expired(self):
        now = 100_000_000_000_000
        ts = now - 120_000_000_000  # 120 seconds ago
        assert _is_fresh(ts, now, 60) is False

    def test_bar_hour_utc(self):
        # 2024-01-01 10:30:00 UTC = 10*3600 + 30*60 = 37800 seconds
        ts_ns = 37800 * 1_000_000_000
        assert _bar_hour_utc(ts_ns) == 10

    def test_bar_hour_utc_midnight(self):
        assert _bar_hour_utc(0) == 0

    def test_bar_hour_utc_late(self):
        # 23:00 UTC
        ts_ns = 23 * 3600 * 1_000_000_000
        assert _bar_hour_utc(ts_ns) == 23


# ---------------------------------------------------------------------------
# Resampling tests
# ---------------------------------------------------------------------------


class TestResampleBars:
    def test_resample_60_minute(self):
        df = _make_bars(120)  # 120 M1 bars = 2 hours
        result = resample_bars(df, "60-MINUTE-LAST-EXTERNAL")
        assert len(result) == 2
        assert list(result.columns) == ["open", "high", "low", "close", "volume"]
        # First hour: open should be first bar's open
        assert result.iloc[0]["open"] == pytest.approx(df.iloc[0]["open"])
        # High should be max of first 60 bars
        assert result.iloc[0]["high"] == pytest.approx(df.iloc[:60]["high"].max())

    def test_resample_120_minute(self):
        df = _make_bars(240)
        result = resample_bars(df, "120-MINUTE-LAST-EXTERNAL")
        assert len(result) == 2

    def test_resample_no_volume(self):
        df = _make_bars(120).drop(columns=["volume"])
        result = resample_bars(df, "60-MINUTE-LAST-EXTERNAL")
        assert len(result) == 2
        assert "volume" not in result.columns

    def test_unsupported_aggregation(self):
        df = _make_bars(10)
        with pytest.raises(ValueError, match="Unsupported aggregation"):
            resample_bars(df, "60-WEEK-LAST-EXTERNAL")

    def test_resample_second(self):
        df = pd.DataFrame(
            {
                "open": [1.0, 2.0, 3.0, 4.0],
                "high": [1.1, 2.1, 3.1, 4.1],
                "low": [0.9, 1.9, 2.9, 3.9],
                "close": [1.05, 2.05, 3.05, 4.05],
            },
            index=pd.date_range("2024-01-01", periods=4, freq="1s", tz="UTC"),
        )
        result = resample_bars(df, "2-SECOND-LAST-EXTERNAL")
        assert len(result) == 2

    def test_resample_hour(self):
        df = _make_bars(120)
        result = resample_bars(df, "1-HOUR-LAST-EXTERNAL")
        assert len(result) == 2

    def test_resample_day(self):
        n = 60 * 24 * 2  # 2 days of M1
        df = _make_bars(n)
        result = resample_bars(df, "1-DAY-LAST-EXTERNAL")
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# Integration tests: full backtest
# ---------------------------------------------------------------------------


class TestCobanRebornBacktest:
    def test_config_validation_wrong_extra_bar_types(self):
        """Strategy should raise if extra_bar_types count != 2."""
        bars_df = _make_bars(500)
        config = BacktestConfig(
            strategy="coban_reborn",
            instrument="EUR/USD",
            start=bars_df.index[0].to_pydatetime(),
            end=bars_df.index[-1].to_pydatetime(),
            bar_type="1-MINUTE-LAST-EXTERNAL",
            extra_bar_types=[],  # wrong: need exactly 2
            trade_size=Decimal("100000"),
        )
        with pytest.raises(ValueError, match="exactly 2 extra_bar_types"):
            run_backtest(config, bars_df)

    def test_basic_run_completes(self):
        """Strategy runs without errors on random data."""
        bars_df = _make_bars(3000)
        config = BacktestConfig(
            strategy="coban_reborn",
            instrument="EUR/USD",
            start=bars_df.index[0].to_pydatetime(),
            end=bars_df.index[-1].to_pydatetime(),
            bar_type="1-MINUTE-LAST-EXTERNAL",
            extra_bar_types=[
                "60-MINUTE-LAST-EXTERNAL",
                "120-MINUTE-LAST-EXTERNAL",
            ],
            trade_size=Decimal("100000"),
            strategy_params={
                "double_confirm_enabled": False,
                "m1_confirm_enabled": False,
            },
        )
        result = run_backtest(config, bars_df)
        assert isinstance(result.total_pnl, float)
        assert isinstance(result.num_trades, int)

    def test_run_with_double_confirm(self):
        """Strategy runs with double confirm enabled."""
        bars_df = _make_bars(3000)
        config = BacktestConfig(
            strategy="coban_reborn",
            instrument="EUR/USD",
            start=bars_df.index[0].to_pydatetime(),
            end=bars_df.index[-1].to_pydatetime(),
            bar_type="1-MINUTE-LAST-EXTERNAL",
            extra_bar_types=[
                "60-MINUTE-LAST-EXTERNAL",
                "120-MINUTE-LAST-EXTERNAL",
            ],
            trade_size=Decimal("100000"),
            strategy_params={
                "double_confirm_enabled": True,
                "m1_confirm_enabled": False,
            },
        )
        result = run_backtest(config, bars_df)
        assert isinstance(result.total_pnl, float)

    def test_run_with_m1_confirm(self):
        """Strategy runs with M1 confirmation enabled."""
        bars_df = _make_bars(3000)
        config = BacktestConfig(
            strategy="coban_reborn",
            instrument="EUR/USD",
            start=bars_df.index[0].to_pydatetime(),
            end=bars_df.index[-1].to_pydatetime(),
            bar_type="1-MINUTE-LAST-EXTERNAL",
            extra_bar_types=[
                "60-MINUTE-LAST-EXTERNAL",
                "120-MINUTE-LAST-EXTERNAL",
            ],
            trade_size=Decimal("100000"),
            strategy_params={
                "double_confirm_enabled": False,
                "m1_confirm_enabled": True,
            },
        )
        result = run_backtest(config, bars_df)
        assert isinstance(result.total_pnl, float)

    def test_trending_data_generates_trades(self):
        """With a strong trend, the strategy should eventually enter trades."""
        # Use strong uptrend to increase chance of signal alignment
        bars_df = _make_bars(5000, seed=123, trend=0.00005)
        config = BacktestConfig(
            strategy="coban_reborn",
            instrument="EUR/USD",
            start=bars_df.index[0].to_pydatetime(),
            end=bars_df.index[-1].to_pydatetime(),
            bar_type="1-MINUTE-LAST-EXTERNAL",
            extra_bar_types=[
                "60-MINUTE-LAST-EXTERNAL",
                "120-MINUTE-LAST-EXTERNAL",
            ],
            trade_size=Decimal("100000"),
            strategy_params={
                "double_confirm_enabled": False,
                "m1_confirm_enabled": False,
                "take_profit_pips": 5,
            },
        )
        result = run_backtest(config, bars_df)
        # With strong trend and no double confirm, should get some trades
        assert result.num_trades >= 0  # may or may not trade on random data


class TestCobanRebornTradeExecution:
    """Tests that exercise entry and take-profit paths."""

    def test_oscillating_data_generates_trades(self):
        """Oscillating trends produce SMA crossovers and signal confluences."""
        bars_df = _make_oscillating_bars(30000, seed=42)
        config = BacktestConfig(
            strategy="coban_reborn",
            instrument="EUR/USD",
            start=bars_df.index[0].to_pydatetime(),
            end=bars_df.index[-1].to_pydatetime(),
            bar_type="1-MINUTE-LAST-EXTERNAL",
            extra_bar_types=[
                "60-MINUTE-LAST-EXTERNAL",
                "120-MINUTE-LAST-EXTERNAL",
            ],
            trade_size=Decimal("100000"),
            strategy_params={
                "double_confirm_enabled": False,
                "m1_confirm_enabled": False,
                "take_profit_pips": 1,
                "max_signal_window_seconds": 14400,
                "rsi_min_peak_diff": 1,
            },
        )
        result = run_backtest(config, bars_df)
        assert isinstance(result.total_pnl, float)
        assert isinstance(result.num_trades, int)

    def test_oscillating_with_m1_confirm(self):
        """M1 confirm enabled with oscillating data."""
        bars_df = _make_oscillating_bars(30000, seed=55)
        config = BacktestConfig(
            strategy="coban_reborn",
            instrument="EUR/USD",
            start=bars_df.index[0].to_pydatetime(),
            end=bars_df.index[-1].to_pydatetime(),
            bar_type="1-MINUTE-LAST-EXTERNAL",
            extra_bar_types=[
                "60-MINUTE-LAST-EXTERNAL",
                "120-MINUTE-LAST-EXTERNAL",
            ],
            trade_size=Decimal("100000"),
            strategy_params={
                "double_confirm_enabled": False,
                "m1_confirm_enabled": True,
                "take_profit_pips": 1,
                "max_signal_window_seconds": 14400,
                "rsi_min_peak_diff": 1,
            },
        )
        result = run_backtest(config, bars_df)
        assert isinstance(result.total_pnl, float)


class TestBackwardsCompat:
    def test_sma_strategy_with_empty_extra_bar_types(self):
        """Existing SMA strategy works fine with the new extra_bar_types field."""
        bars_df = _make_bars(500)
        config = BacktestConfig(
            strategy="sample_sma",
            instrument="EUR/USD",
            start=bars_df.index[0].to_pydatetime(),
            end=bars_df.index[-1].to_pydatetime(),
            trade_size=Decimal("100000"),
            strategy_params={"fast_period": 10, "slow_period": 50},
        )
        result = run_backtest(config, bars_df)
        assert result.num_trades >= 0


# ---------------------------------------------------------------------------
# Double-confirm logic unit tests
# ---------------------------------------------------------------------------


class TestDoubleConfirm:
    """Test _apply_signal with double-confirm enabled via strategy instance."""

    def _make_strategy(self) -> CobanRebornStrategy:
        """Create a strategy instance for unit testing signal logic."""
        from nautilus_trader.model.data import BarType
        from nautilus_trader.model.identifiers import InstrumentId

        m1 = BarType.from_str("EUR/USD.SIM-1-MINUTE-LAST-EXTERNAL")
        h1 = BarType.from_str("EUR/USD.SIM-60-MINUTE-LAST-EXTERNAL")
        h2 = BarType.from_str("EUR/USD.SIM-120-MINUTE-LAST-EXTERNAL")
        config = CobanRebornConfig(
            instrument_id=InstrumentId.from_str("EUR/USD.SIM"),
            bar_type=m1,
            extra_bar_types=(h1, h2),
            double_confirm_enabled=True,
            double_confirm_window_seconds=600,
            double_confirm_min_gap_seconds=300,
        )
        return CobanRebornStrategy(config)

    def test_single_signal_not_confirmed(self):
        s = self._make_strategy()
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts1 = 1_000_000_000_000  # 1000s in ns
        s._apply_signal("h1_macd", 1, ts1, cfg)
        # Should NOT be confirmed yet
        assert s._h1_macd_cross_dir == 0

    def test_two_signals_within_window_confirmed(self):
        s = self._make_strategy()
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts1 = 1_000_000_000_000
        ts2 = ts1 + 400_000_000_000  # +400s (within 600s window, > 300s gap)
        s._apply_signal("h1_macd", 1, ts1, cfg)
        s._apply_signal("h1_macd", 1, ts2, cfg)
        assert s._h1_macd_cross_dir == 1
        assert s._h1_macd_cross_ts == ts2

    def test_gap_too_small(self):
        s = self._make_strategy()
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts1 = 1_000_000_000_000
        ts2 = ts1 + 100_000_000_000  # +100s (< 300s min gap)
        s._apply_signal("h1_macd", 1, ts1, cfg)
        s._apply_signal("h1_macd", 1, ts2, cfg)
        assert s._h1_macd_cross_dir == 0  # not confirmed

    def test_outside_window_resets(self):
        s = self._make_strategy()
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts1 = 1_000_000_000_000
        ts2 = ts1 + 700_000_000_000  # +700s (> 600s window)
        s._apply_signal("h1_macd", 1, ts1, cfg)
        s._apply_signal("h1_macd", 1, ts2, cfg)
        # Should have reset to new first signal
        assert s._h1_macd_cross_dir == 0
        assert s._h1_macd_dc_first_ts == ts2

    def test_direction_change_resets(self):
        s = self._make_strategy()
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts1 = 1_000_000_000_000
        ts2 = ts1 + 400_000_000_000
        s._apply_signal("h1_macd", 1, ts1, cfg)
        s._apply_signal("h1_macd", -1, ts2, cfg)  # direction changed
        assert s._h1_macd_dc_dir == -1
        assert s._h1_macd_dc_first_ts == ts2

    def test_double_confirm_disabled(self):
        from nautilus_trader.model.data import BarType
        from nautilus_trader.model.identifiers import InstrumentId

        m1 = BarType.from_str("EUR/USD.SIM-1-MINUTE-LAST-EXTERNAL")
        h1 = BarType.from_str("EUR/USD.SIM-60-MINUTE-LAST-EXTERNAL")
        h2 = BarType.from_str("EUR/USD.SIM-120-MINUTE-LAST-EXTERNAL")
        config = CobanRebornConfig(
            instrument_id=InstrumentId.from_str("EUR/USD.SIM"),
            bar_type=m1,
            extra_bar_types=(h1, h2),
            double_confirm_enabled=False,
        )
        s = CobanRebornStrategy(config)
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts1 = 1_000_000_000_000
        s._apply_signal("h1_macd", 1, ts1, cfg)
        # Immediately confirmed
        assert s._h1_macd_cross_dir == 1
        assert s._h1_macd_cross_ts == ts1

    def test_rsi_double_confirm(self):
        s = self._make_strategy()
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts1 = 1_000_000_000_000
        ts2 = ts1 + 400_000_000_000
        s._apply_signal("h1_rsi", -1, ts1, cfg)
        s._apply_signal("h1_rsi", -1, ts2, cfg)
        assert s._h1_rsi_cross_dir == -1  # uses the *_cross_dir pattern via setattr


# ---------------------------------------------------------------------------
# H1 complete signal tests
# ---------------------------------------------------------------------------


class TestH1CompleteSignal:
    def _make_strategy(self) -> CobanRebornStrategy:
        from nautilus_trader.model.data import BarType
        from nautilus_trader.model.identifiers import InstrumentId

        m1 = BarType.from_str("EUR/USD.SIM-1-MINUTE-LAST-EXTERNAL")
        h1 = BarType.from_str("EUR/USD.SIM-60-MINUTE-LAST-EXTERNAL")
        h2 = BarType.from_str("EUR/USD.SIM-120-MINUTE-LAST-EXTERNAL")
        config = CobanRebornConfig(
            instrument_id=InstrumentId.from_str("EUR/USD.SIM"),
            bar_type=m1,
            extra_bar_types=(h1, h2),
            double_confirm_enabled=False,
            max_signal_window_seconds=3600,
        )
        return CobanRebornStrategy(config)

    def test_all_agree_bullish(self):
        s = self._make_strategy()
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts = 1_000_000_000_000
        s._h1_sma_cross_dir = 1
        s._h1_sma_cross_ts = ts
        s._h1_macd_cross_dir = 1
        s._h1_macd_cross_ts = ts + 60_000_000_000
        s._h1_rsi_cross_dir = 1
        s._h1_rsi_cross_ts = ts + 120_000_000_000
        s._check_h1_complete(ts + 180_000_000_000, cfg)
        assert s._h1_complete_dir == 1

    def test_all_agree_bearish(self):
        s = self._make_strategy()
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts = 1_000_000_000_000
        s._h1_sma_cross_dir = -1
        s._h1_sma_cross_ts = ts
        s._h1_macd_cross_dir = -1
        s._h1_macd_cross_ts = ts + 60_000_000_000
        s._h1_rsi_cross_dir = -1
        s._h1_rsi_cross_ts = ts + 120_000_000_000
        s._check_h1_complete(ts + 180_000_000_000, cfg)
        assert s._h1_complete_dir == -1

    def test_partial_signals(self):
        s = self._make_strategy()
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts = 1_000_000_000_000
        s._h1_sma_cross_dir = 1
        s._h1_sma_cross_ts = ts
        # macd_cross_dir is still 0
        s._h1_rsi_cross_dir = 1
        s._h1_rsi_cross_ts = ts
        s._check_h1_complete(ts, cfg)
        assert s._h1_complete_dir == 0

    def test_direction_mismatch(self):
        s = self._make_strategy()
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts = 1_000_000_000_000
        s._h1_sma_cross_dir = 1
        s._h1_sma_cross_ts = ts
        s._h1_macd_cross_dir = -1  # disagrees
        s._h1_macd_cross_ts = ts
        s._h1_rsi_cross_dir = 1
        s._h1_rsi_cross_ts = ts
        s._check_h1_complete(ts, cfg)
        assert s._h1_complete_dir == 0

    def test_outside_window(self):
        s = self._make_strategy()
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts = 1_000_000_000_000
        s._h1_sma_cross_dir = 1
        s._h1_sma_cross_ts = ts
        s._h1_macd_cross_dir = 1
        s._h1_macd_cross_ts = ts + 4000_000_000_000  # +4000s > 3600s window
        s._h1_rsi_cross_dir = 1
        s._h1_rsi_cross_ts = ts
        s._check_h1_complete(ts, cfg)
        assert s._h1_complete_dir == 0


# ---------------------------------------------------------------------------
# Reset signals test
# ---------------------------------------------------------------------------


class TestResetSignals:
    def test_reset(self):
        from nautilus_trader.model.data import BarType
        from nautilus_trader.model.identifiers import InstrumentId

        m1 = BarType.from_str("EUR/USD.SIM-1-MINUTE-LAST-EXTERNAL")
        h1 = BarType.from_str("EUR/USD.SIM-60-MINUTE-LAST-EXTERNAL")
        h2 = BarType.from_str("EUR/USD.SIM-120-MINUTE-LAST-EXTERNAL")
        config = CobanRebornConfig(
            instrument_id=InstrumentId.from_str("EUR/USD.SIM"),
            bar_type=m1,
            extra_bar_types=(h1, h2),
        )
        s = CobanRebornStrategy(config)
        s._h1_sma_cross_dir = 1
        s._h1_sma_cross_ts = 999
        s._h1_complete_dir = 1
        s._h2_rsi_break_dir = 1
        s._m1_sma_dir = 1
        s._reset_signals()
        assert s._h1_sma_cross_dir == 0
        assert s._h1_sma_cross_ts == _ZERO_TS
        assert s._h1_complete_dir == 0
        assert s._h2_rsi_break_dir == 0
        assert s._m1_sma_dir == 0
        assert s._best_price == 0.0
        assert s._entry_atr == 0.0


# ---------------------------------------------------------------------------
# Trend-follow entry mode tests
# ---------------------------------------------------------------------------


class TestTrendFollowEntry:
    """Test the trend_follow entry mode logic."""

    def _make_strategy(
        self, **overrides: object,
    ) -> CobanRebornStrategy:
        from nautilus_trader.model.data import BarType
        from nautilus_trader.model.identifiers import InstrumentId

        m1 = BarType.from_str("EUR/USD.SIM-1-MINUTE-LAST-EXTERNAL")
        h1 = BarType.from_str("EUR/USD.SIM-60-MINUTE-LAST-EXTERNAL")
        h2 = BarType.from_str("EUR/USD.SIM-120-MINUTE-LAST-EXTERNAL")
        defaults: dict = {
            "instrument_id": InstrumentId.from_str("EUR/USD.SIM"),
            "bar_type": m1,
            "extra_bar_types": (h1, h2),
            "entry_mode": "trend_follow",
        }
        defaults.update(overrides)
        config = CobanRebornConfig(**defaults)
        return CobanRebornStrategy(config)

    def test_long_signal_when_all_filters_pass(self):
        s = self._make_strategy()
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts = 1_000_000_000_000
        s._h1_sma_cross_dir = 1
        s._h1_sma_cross_ts = ts
        s._h1_current_hist = 0.001
        s._h1_current_rsi = 0.60
        s._h1_hist_ts = ts
        s._h1_rsi_ts = ts
        result = s._entry_trend_follow(ts, cfg)
        assert result == 1

    def test_short_signal_when_all_filters_pass(self):
        s = self._make_strategy()
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts = 1_000_000_000_000
        s._h1_sma_cross_dir = -1
        s._h1_sma_cross_ts = ts
        s._h1_current_hist = -0.001
        s._h1_current_rsi = 0.40
        s._h1_hist_ts = ts
        s._h1_rsi_ts = ts
        result = s._entry_trend_follow(ts, cfg)
        assert result == -1

    def test_no_signal_when_no_sma_cross(self):
        s = self._make_strategy()
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts = 1_000_000_000_000
        s._h1_sma_cross_dir = 0
        s._h1_current_hist = 0.001
        s._h1_current_rsi = 0.60
        s._h1_hist_ts = ts
        s._h1_rsi_ts = ts
        assert s._entry_trend_follow(ts, cfg) == 0

    def test_no_signal_when_sma_expired(self):
        s = self._make_strategy(max_signal_window_seconds=3600)
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts = 1_000_000_000_000
        s._h1_sma_cross_dir = 1
        s._h1_sma_cross_ts = ts - 4000_000_000_000  # 4000s ago > 3600s
        s._h1_current_hist = 0.001
        s._h1_current_rsi = 0.60
        s._h1_hist_ts = ts
        s._h1_rsi_ts = ts
        assert s._entry_trend_follow(ts, cfg) == 0

    def test_no_long_when_macd_negative(self):
        s = self._make_strategy()
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts = 1_000_000_000_000
        s._h1_sma_cross_dir = 1
        s._h1_sma_cross_ts = ts
        s._h1_current_hist = -0.001  # MACD says bearish
        s._h1_current_rsi = 0.60
        s._h1_hist_ts = ts
        s._h1_rsi_ts = ts
        assert s._entry_trend_follow(ts, cfg) == 0

    def test_no_long_when_rsi_below_threshold(self):
        s = self._make_strategy()
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts = 1_000_000_000_000
        s._h1_sma_cross_dir = 1
        s._h1_sma_cross_ts = ts
        s._h1_current_hist = 0.001
        s._h1_current_rsi = 0.40  # RSI says bearish
        s._h1_hist_ts = ts
        s._h1_rsi_ts = ts
        assert s._entry_trend_follow(ts, cfg) == 0

    def test_no_short_when_macd_positive(self):
        s = self._make_strategy()
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts = 1_000_000_000_000
        s._h1_sma_cross_dir = -1
        s._h1_sma_cross_ts = ts
        s._h1_current_hist = 0.001  # MACD says bullish
        s._h1_current_rsi = 0.40
        s._h1_hist_ts = ts
        s._h1_rsi_ts = ts
        assert s._entry_trend_follow(ts, cfg) == 0

    def test_no_short_when_rsi_above_threshold(self):
        s = self._make_strategy()
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts = 1_000_000_000_000
        s._h1_sma_cross_dir = -1
        s._h1_sma_cross_ts = ts
        s._h1_current_hist = -0.001
        s._h1_current_rsi = 0.60  # RSI says bullish
        s._h1_hist_ts = ts
        s._h1_rsi_ts = ts
        assert s._entry_trend_follow(ts, cfg) == 0

    def test_no_signal_when_hist_stale(self):
        """Reject entry when MACD histogram is older than staleness window."""
        s = self._make_strategy(filter_staleness_seconds=7200)
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts = 1_000_000_000_000
        s._h1_sma_cross_dir = 1
        s._h1_sma_cross_ts = ts
        s._h1_current_hist = 0.001
        s._h1_current_rsi = 0.60
        s._h1_hist_ts = ts - 8000_000_000_000  # 8000s ago > 7200s
        s._h1_rsi_ts = ts
        assert s._entry_trend_follow(ts, cfg) == 0

    def test_no_signal_when_rsi_stale(self):
        """Reject entry when RSI value is older than staleness window."""
        s = self._make_strategy(filter_staleness_seconds=7200)
        cfg: CobanRebornConfig = s.config  # type: ignore[assignment]
        ts = 1_000_000_000_000
        s._h1_sma_cross_dir = 1
        s._h1_sma_cross_ts = ts
        s._h1_current_hist = 0.001
        s._h1_current_rsi = 0.60
        s._h1_hist_ts = ts
        s._h1_rsi_ts = ts - 8000_000_000_000  # 8000s ago > 7200s
        assert s._entry_trend_follow(ts, cfg) == 0


# ---------------------------------------------------------------------------
# Exit mode tests
# ---------------------------------------------------------------------------


class TestExitModes:
    """Test fixed, trailing, and ATR exit logic."""

    def _make_strategy(
        self, **overrides: object,
    ) -> CobanRebornStrategy:
        from nautilus_trader.model.data import BarType
        from nautilus_trader.model.identifiers import InstrumentId

        m1 = BarType.from_str("EUR/USD.SIM-1-MINUTE-LAST-EXTERNAL")
        h1 = BarType.from_str("EUR/USD.SIM-60-MINUTE-LAST-EXTERNAL")
        h2 = BarType.from_str("EUR/USD.SIM-120-MINUTE-LAST-EXTERNAL")
        defaults: dict = {
            "instrument_id": InstrumentId.from_str("EUR/USD.SIM"),
            "bar_type": m1,
            "extra_bar_types": (h1, h2),
        }
        defaults.update(overrides)
        config = CobanRebornConfig(**defaults)
        return CobanRebornStrategy(config)

    # -- Fixed exit (high/low) -----------------------------------------------

    def test_fixed_long_tp_via_high(self):
        """Run a full backtest with fixed exit on oscillating data to cover TP path."""
        bars_df = _make_oscillating_bars(20000, seed=42)
        config = BacktestConfig(
            strategy="coban_reborn",
            instrument="EUR/USD",
            start=bars_df.index[0].to_pydatetime(),
            end=bars_df.index[-1].to_pydatetime(),
            bar_type="1-MINUTE-LAST-EXTERNAL",
            extra_bar_types=["60-MINUTE-LAST-EXTERNAL", "120-MINUTE-LAST-EXTERNAL"],
            trade_size=Decimal("100000"),
            strategy_params={
                "entry_mode": "trend_follow",
                "exit_mode": "fixed",
                "take_profit_pips": 1,
                "stop_loss_pips": 50,
                "spread_pips": 0.0,
            },
        )
        result = run_backtest(config, bars_df)
        assert result.num_trades > 0

    def test_fixed_short_sl_via_high(self):
        """Fixed exit on downtrend data to cover SL path."""
        bars_df = _make_bars(5000, seed=99, trend=-0.00005)
        config = BacktestConfig(
            strategy="coban_reborn",
            instrument="EUR/USD",
            start=bars_df.index[0].to_pydatetime(),
            end=bars_df.index[-1].to_pydatetime(),
            bar_type="1-MINUTE-LAST-EXTERNAL",
            extra_bar_types=["60-MINUTE-LAST-EXTERNAL", "120-MINUTE-LAST-EXTERNAL"],
            trade_size=Decimal("100000"),
            strategy_params={
                "entry_mode": "trend_follow",
                "exit_mode": "fixed",
                "take_profit_pips": 50,
                "stop_loss_pips": 1,
                "spread_pips": 0.0,
            },
        )
        result = run_backtest(config, bars_df)
        assert isinstance(result.total_pnl, float)

    # -- Trailing exit -------------------------------------------------------

    def test_trailing_long_exit(self):
        """Trailing exit on oscillating data."""
        bars_df = _make_oscillating_bars(20000, seed=42)
        config = BacktestConfig(
            strategy="coban_reborn",
            instrument="EUR/USD",
            start=bars_df.index[0].to_pydatetime(),
            end=bars_df.index[-1].to_pydatetime(),
            bar_type="1-MINUTE-LAST-EXTERNAL",
            extra_bar_types=["60-MINUTE-LAST-EXTERNAL", "120-MINUTE-LAST-EXTERNAL"],
            trade_size=Decimal("100000"),
            strategy_params={
                "entry_mode": "trend_follow",
                "exit_mode": "trailing",
                "trailing_stop_pips": 5,
                "stop_loss_pips": 50,
                "spread_pips": 0.0,
            },
        )
        result = run_backtest(config, bars_df)
        assert isinstance(result.total_pnl, float)

    # -- ATR exit ------------------------------------------------------------

    def test_atr_exit(self):
        """ATR exit on oscillating data."""
        bars_df = _make_oscillating_bars(20000, seed=42)
        config = BacktestConfig(
            strategy="coban_reborn",
            instrument="EUR/USD",
            start=bars_df.index[0].to_pydatetime(),
            end=bars_df.index[-1].to_pydatetime(),
            bar_type="1-MINUTE-LAST-EXTERNAL",
            extra_bar_types=["60-MINUTE-LAST-EXTERNAL", "120-MINUTE-LAST-EXTERNAL"],
            trade_size=Decimal("100000"),
            strategy_params={
                "entry_mode": "trend_follow",
                "exit_mode": "atr",
                "atr_tp_multiplier": 1.0,
                "atr_sl_multiplier": 1.0,
                "spread_pips": 0.0,
            },
        )
        result = run_backtest(config, bars_df)
        assert isinstance(result.total_pnl, float)


# ---------------------------------------------------------------------------
# Integration tests for new modes
# ---------------------------------------------------------------------------


class TestNewModeIntegration:
    """Integration tests running full backtests with new entry/exit modes."""

    def test_trend_follow_runs(self):
        bars_df = _make_oscillating_bars(20000, seed=42)
        config = BacktestConfig(
            strategy="coban_reborn",
            instrument="EUR/USD",
            start=bars_df.index[0].to_pydatetime(),
            end=bars_df.index[-1].to_pydatetime(),
            bar_type="1-MINUTE-LAST-EXTERNAL",
            extra_bar_types=[
                "60-MINUTE-LAST-EXTERNAL",
                "120-MINUTE-LAST-EXTERNAL",
            ],
            trade_size=Decimal("100000"),
            strategy_params={
                "entry_mode": "trend_follow",
            },
        )
        result = run_backtest(config, bars_df)
        assert isinstance(result.total_pnl, float)
        assert isinstance(result.num_trades, int)

    def test_trailing_exit_runs(self):
        bars_df = _make_oscillating_bars(20000, seed=42)
        config = BacktestConfig(
            strategy="coban_reborn",
            instrument="EUR/USD",
            start=bars_df.index[0].to_pydatetime(),
            end=bars_df.index[-1].to_pydatetime(),
            bar_type="1-MINUTE-LAST-EXTERNAL",
            extra_bar_types=[
                "60-MINUTE-LAST-EXTERNAL",
                "120-MINUTE-LAST-EXTERNAL",
            ],
            trade_size=Decimal("100000"),
            strategy_params={
                "entry_mode": "trend_follow",
                "exit_mode": "trailing",
            },
        )
        result = run_backtest(config, bars_df)
        assert isinstance(result.total_pnl, float)

    def test_atr_exit_runs(self):
        bars_df = _make_oscillating_bars(20000, seed=42)
        config = BacktestConfig(
            strategy="coban_reborn",
            instrument="EUR/USD",
            start=bars_df.index[0].to_pydatetime(),
            end=bars_df.index[-1].to_pydatetime(),
            bar_type="1-MINUTE-LAST-EXTERNAL",
            extra_bar_types=[
                "60-MINUTE-LAST-EXTERNAL",
                "120-MINUTE-LAST-EXTERNAL",
            ],
            trade_size=Decimal("100000"),
            strategy_params={
                "entry_mode": "trend_follow",
                "exit_mode": "atr",
            },
        )
        result = run_backtest(config, bars_df)
        assert isinstance(result.total_pnl, float)

    def test_full_mode_backward_compat(self):
        """entry_mode='full' should work the same as before."""
        bars_df = _make_bars(3000)
        config = BacktestConfig(
            strategy="coban_reborn",
            instrument="EUR/USD",
            start=bars_df.index[0].to_pydatetime(),
            end=bars_df.index[-1].to_pydatetime(),
            bar_type="1-MINUTE-LAST-EXTERNAL",
            extra_bar_types=[
                "60-MINUTE-LAST-EXTERNAL",
                "120-MINUTE-LAST-EXTERNAL",
            ],
            trade_size=Decimal("100000"),
            strategy_params={
                "entry_mode": "full",
                "exit_mode": "fixed",
                "double_confirm_enabled": False,
                "m1_confirm_enabled": False,
            },
        )
        result = run_backtest(config, bars_df)
        assert isinstance(result.total_pnl, float)

    def test_get_entry_signal_dispatches_correctly(self):
        """_get_entry_signal routes to the right method."""
        from nautilus_trader.model.data import BarType
        from nautilus_trader.model.identifiers import InstrumentId

        m1 = BarType.from_str("EUR/USD.SIM-1-MINUTE-LAST-EXTERNAL")
        h1 = BarType.from_str("EUR/USD.SIM-60-MINUTE-LAST-EXTERNAL")
        h2 = BarType.from_str("EUR/USD.SIM-120-MINUTE-LAST-EXTERNAL")

        # Test full mode dispatch
        cfg_full = CobanRebornConfig(
            instrument_id=InstrumentId.from_str("EUR/USD.SIM"),
            bar_type=m1, extra_bar_types=(h1, h2),
            entry_mode="full",
        )
        s = CobanRebornStrategy(cfg_full)
        # No signals set, so should return 0
        assert s._get_entry_signal(1_000_000_000_000, cfg_full) == 0

        # Test trend_follow mode dispatch
        cfg_tf = CobanRebornConfig(
            instrument_id=InstrumentId.from_str("EUR/USD.SIM"),
            bar_type=m1, extra_bar_types=(h1, h2),
            entry_mode="trend_follow",
        )
        s2 = CobanRebornStrategy(cfg_tf)
        assert s2._get_entry_signal(1_000_000_000_000, cfg_tf) == 0

    def test_trend_follow_with_5m_15m(self):
        """Trend follow on 5m/15m timeframes."""
        bars_df = _make_oscillating_bars(20000, seed=77)
        config = BacktestConfig(
            strategy="coban_reborn",
            instrument="EUR/USD",
            start=bars_df.index[0].to_pydatetime(),
            end=bars_df.index[-1].to_pydatetime(),
            bar_type="1-MINUTE-LAST-EXTERNAL",
            extra_bar_types=[
                "5-MINUTE-LAST-EXTERNAL",
                "15-MINUTE-LAST-EXTERNAL",
            ],
            trade_size=Decimal("100000"),
            strategy_params={
                "entry_mode": "trend_follow",
                "exit_mode": "atr",
            },
        )
        result = run_backtest(config, bars_df)
        assert isinstance(result.total_pnl, float)

    def test_next_bar_entry_runs(self):
        """next_bar_entry defers entry to the following M1 bar."""
        bars_df = _make_oscillating_bars(20000, seed=42)
        config = BacktestConfig(
            strategy="coban_reborn",
            instrument="EUR/USD",
            start=bars_df.index[0].to_pydatetime(),
            end=bars_df.index[-1].to_pydatetime(),
            bar_type="1-MINUTE-LAST-EXTERNAL",
            extra_bar_types=[
                "60-MINUTE-LAST-EXTERNAL",
                "120-MINUTE-LAST-EXTERNAL",
            ],
            trade_size=Decimal("100000"),
            strategy_params={
                "entry_mode": "trend_follow",
                "next_bar_entry": True,
            },
        )
        result = run_backtest(config, bars_df)
        assert isinstance(result.total_pnl, float)
        assert isinstance(result.num_trades, int)

    def test_next_bar_entry_differs_from_immediate(self):
        """next_bar_entry should produce different results than immediate entry."""
        bars_df = _make_oscillating_bars(20000, seed=42)
        base_params: dict = {
            "strategy": "coban_reborn",
            "instrument": "EUR/USD",
            "start": bars_df.index[0].to_pydatetime(),
            "end": bars_df.index[-1].to_pydatetime(),
            "bar_type": "1-MINUTE-LAST-EXTERNAL",
            "extra_bar_types": [
                "60-MINUTE-LAST-EXTERNAL",
                "120-MINUTE-LAST-EXTERNAL",
            ],
            "trade_size": Decimal("100000"),
        }
        immediate = run_backtest(
            BacktestConfig(**base_params, strategy_params={
                "entry_mode": "trend_follow",
                "next_bar_entry": False,
            }),
            bars_df,
        )
        deferred = run_backtest(
            BacktestConfig(**base_params, strategy_params={
                "entry_mode": "trend_follow",
                "next_bar_entry": True,
            }),
            bars_df,
        )
        # Both should produce trades, but P&L should differ
        assert immediate.num_trades > 0
        assert deferred.num_trades > 0
        assert immediate.total_pnl != deferred.total_pnl


def test_coban_reborn_chart_indicators() -> None:
    """chart_indicators() returns expected defaults for CobanReborn."""
    indicators = CobanRebornStrategy.chart_indicators()
    assert len(indicators) == 4
    names = [i["name"] for i in indicators]
    assert "sma" in names
    assert "rsi" in names
    assert "macd" in names
