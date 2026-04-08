"""Tests for backtest runner helper functions."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from pyfx.backtest.runner import (
    _convert_pnl_to_usd,
    _parse_nautilus_money,
    _to_utc_datetime,
)
from pyfx.strategies.loader import find_strategy_config_class

# ---------------------------------------------------------------------------
# _to_utc_datetime
# ---------------------------------------------------------------------------

class TestToUtcDatetime:
    def test_aware_datetime_passthrough(self) -> None:
        dt = datetime(2024, 1, 1, tzinfo=UTC)
        assert _to_utc_datetime(dt) == dt

    def test_naive_datetime(self) -> None:
        dt = datetime(2024, 1, 1)
        result = _to_utc_datetime(dt)
        assert result.tzinfo == UTC

    def test_pd_timestamp_aware(self) -> None:
        ts = pd.Timestamp("2024-01-01", tz="UTC")
        result = _to_utc_datetime(ts)
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_pd_timestamp_naive(self) -> None:
        ts = pd.Timestamp("2024-01-01")
        result = _to_utc_datetime(ts)
        assert result.tzinfo is not None

    def test_epoch_seconds(self) -> None:
        epoch = 1704067200.0  # 2024-01-01 00:00:00 UTC
        result = _to_utc_datetime(epoch)
        assert result.year == 2024
        assert result.tzinfo is not None

    def test_epoch_microseconds(self) -> None:
        # Microsecond range: 1e12 < v <= 1e15
        # 2001-09-09 01:46:40 UTC = 1_000_000_000 seconds = 1e15 microseconds
        epoch_us = 1_000_000_000_000_000  # 1e15 exactly — still > 1e15? No, equal
        # Use a value cleanly in the range: 100_000_000_000_000 (1e14)
        # 1e14 microseconds = 1e8 seconds = 1973-03-03
        epoch_us = 100_000_000_000_000
        result = _to_utc_datetime(epoch_us)
        assert result.year == 1973

    def test_epoch_nanoseconds(self) -> None:
        epoch_ns = 1704067200_000_000_000  # nanoseconds
        result = _to_utc_datetime(epoch_ns)
        assert result.year == 2024

    def test_string_fallback(self) -> None:
        result = _to_utc_datetime("2024-01-01 00:00:00")
        assert result.year == 2024
        assert result.tzinfo is not None


# ---------------------------------------------------------------------------
# _parse_nautilus_money
# ---------------------------------------------------------------------------

class TestParseNautilusMoney:
    def test_amount_and_currency(self) -> None:
        assert _parse_nautilus_money("-56.40 USD") == (-56.40, "USD")

    def test_amount_only(self) -> None:
        assert _parse_nautilus_money("123.45") == (123.45, "USD")

    def test_empty_string(self) -> None:
        assert _parse_nautilus_money("") == (0.0, "USD")

    def test_non_usd_currency(self) -> None:
        assert _parse_nautilus_money("1000.0 JPY") == (1000.0, "JPY")


# ---------------------------------------------------------------------------
# _convert_pnl_to_usd
# ---------------------------------------------------------------------------

class TestConvertPnlToUsd:
    def test_usd_passthrough(self) -> None:
        assert _convert_pnl_to_usd(100.0, "USD", 1.1) == 100.0

    def test_zero_close_price(self) -> None:
        assert _convert_pnl_to_usd(100.0, "JPY", 0.0) == 100.0

    def test_jpy_conversion(self) -> None:
        result = _convert_pnl_to_usd(15000.0, "JPY", 150.0)
        assert result == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# find_strategy_config_class
# ---------------------------------------------------------------------------

class TestFindStrategyConfigClass:
    def test_finds_annotated_config(self) -> None:
        from pyfx.strategies.sample_sma import SMACrossStrategy

        config_cls = find_strategy_config_class(SMACrossStrategy)
        assert config_cls is not None
        assert config_cls.__name__ == "SMACrossConfig"

    def test_finds_coban_config(self) -> None:
        from pyfx.strategies.coban_reborn import CobanRebornStrategy

        config_cls = find_strategy_config_class(CobanRebornStrategy)
        assert config_cls is not None
        assert config_cls.__name__ == "CobanRebornConfig"

    def test_no_config_returns_none(self) -> None:
        class NoConfigStrategy:
            pass

        assert find_strategy_config_class(NoConfigStrategy) is None


    def test_fallback_module_scan(self) -> None:
        """When __init__ has no config annotation, scan module for *Config class."""
        import sys
        import types

        module = types.ModuleType("fake_module")
        module.__name__ = "fake_module"

        class FakeConfig:
            pass

        class FakeStrategy:
            __module__ = "fake_module"

            def __init__(self) -> None:
                pass

        module.FakeConfig = FakeConfig  # type: ignore[attr-defined]
        module.FakeStrategy = FakeStrategy  # type: ignore[attr-defined]

        sys.modules["fake_module"] = module
        try:
            result = find_strategy_config_class(FakeStrategy)
            assert result is FakeConfig
        finally:
            del sys.modules["fake_module"]
