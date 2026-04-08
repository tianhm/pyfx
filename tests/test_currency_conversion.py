"""Integration tests for currency conversion in backtests.

Verifies that non-USD-quoted instruments (USD/JPY) produce correct
USD-denominated P&L, and that USD-quoted instruments (EUR/USD, XAU/USD)
continue to work correctly.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from pyfx.backtest.runner import (
    _convert_pnl_to_usd,
    _get_instrument,
    _parse_nautilus_money,
    run_backtest,
)
from pyfx.core.instruments import get_instrument_spec
from pyfx.core.types import BacktestConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_instrument_bars(
    instrument: str,
    n: int = 5000,
    seed: int = 42,
) -> pd.DataFrame:
    """Create synthetic M1 OHLCV data using instrument-appropriate scales."""
    spec = get_instrument_spec(instrument)
    rng = np.random.default_rng(seed)
    base = spec.base_price
    noise = rng.normal(0, spec.volatility, n)
    price = base + np.cumsum(noise)
    # Mean revert
    for i in range(1, len(price)):
        price[i] += (base - price[i]) * spec.volatility

    spread = np.abs(rng.normal(spec.spread, spec.spread * 0.4, n))

    df = pd.DataFrame(
        {
            "open": price,
            "high": price + spread + np.abs(rng.normal(0, spec.volatility, n)),
            "low": price - spread - np.abs(rng.normal(0, spec.volatility, n)),
            "close": price + rng.normal(0, spec.volatility * 0.5, n),
            "volume": np.full(n, 1_000_000.0),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="1min", tz="UTC"),
    )
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    return df


# ---------------------------------------------------------------------------
# Unit tests: _parse_nautilus_money
# ---------------------------------------------------------------------------


class TestParseNautilusMoney:
    def test_usd_string(self) -> None:
        amount, currency = _parse_nautilus_money("-56.40 USD")
        assert amount == pytest.approx(-56.40)
        assert currency == "USD"

    def test_jpy_string(self) -> None:
        amount, currency = _parse_nautilus_money("-7000 JPY")
        assert amount == pytest.approx(-7000.0)
        assert currency == "JPY"

    def test_no_currency_suffix(self) -> None:
        amount, currency = _parse_nautilus_money("123.45")
        assert amount == pytest.approx(123.45)
        assert currency == "USD"

    def test_empty_string(self) -> None:
        amount, currency = _parse_nautilus_money("")
        assert amount == 0.0
        assert currency == "USD"

    def test_zero(self) -> None:
        amount, currency = _parse_nautilus_money("0")
        assert amount == 0.0


# ---------------------------------------------------------------------------
# Unit tests: _convert_pnl_to_usd
# ---------------------------------------------------------------------------


class TestConvertPnlToUsd:
    def test_usd_passthrough(self) -> None:
        assert _convert_pnl_to_usd(-56.40, "USD", 1.10) == pytest.approx(-56.40)

    def test_jpy_conversion(self) -> None:
        # -7000 JPY at 150 JPY/USD = -$46.67
        result = _convert_pnl_to_usd(-7000.0, "JPY", 150.0)
        assert result == pytest.approx(-46.6667, rel=1e-3)

    def test_zero_close_price_fallback(self) -> None:
        """Zero close price returns raw pnl to avoid division by zero."""
        assert _convert_pnl_to_usd(-100.0, "JPY", 0.0) == -100.0


# ---------------------------------------------------------------------------
# Unit tests: _get_instrument
# ---------------------------------------------------------------------------


class TestGetInstrument:
    def test_fx_pair_uses_provider(self) -> None:
        i = _get_instrument("EUR/USD", "SIM")
        assert i.price_precision == 5
        assert str(i.base_currency) == "EUR"

    def test_jpy_pair_uses_provider(self) -> None:
        i = _get_instrument("USD/JPY", "SIM")
        assert i.price_precision == 3
        assert str(i.quote_currency) == "JPY"

    def test_commodity_uses_custom(self) -> None:
        i = _get_instrument("XAU/USD", "SIM")
        assert i.price_precision == 2
        assert str(i.base_currency) == "XAU"

    def test_oil_uses_custom(self) -> None:
        i = _get_instrument("OIL/USD", "SIM")
        assert i.price_precision == 2


# ---------------------------------------------------------------------------
# Integration tests: full backtest with currency conversion
# ---------------------------------------------------------------------------


class TestJPYBacktest:
    """USD/JPY backtests produce correct USD P&L."""

    def test_usdjpy_pnl_is_usd(self) -> None:
        bars = _make_instrument_bars("USD/JPY", n=5000)
        config = BacktestConfig(
            strategy="sample_sma",
            instrument="USD/JPY",
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 4),
        )
        result = run_backtest(config, bars)
        assert result.num_trades > 0

        # P&L should be in a reasonable USD range (not inflated JPY values)
        assert abs(result.total_pnl) < 50_000  # not -199k anymore
        assert abs(result.total_return_pct) < 50  # not -199%

        # Per-trade P&L should be in USD (converted from JPY)
        for trade in result.trades:
            assert trade.pnl_currency == "JPY"
            # Individual trade P&L should be modest, not 1000s of "USD"
            assert abs(trade.realized_pnl) < 5_000

    def test_usdjpy_equity_curve_reasonable(self) -> None:
        bars = _make_instrument_bars("USD/JPY", n=3000)
        config = BacktestConfig(
            strategy="sample_sma",
            instrument="USD/JPY",
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 3),
        )
        result = run_backtest(config, bars)

        # Equity curve should stay near starting balance
        for point in result.equity_curve:
            assert 50_000 < point.balance < 200_000


class TestUSDQuotedBacktest:
    """EUR/USD and XAU/USD (USD-quoted) continue working correctly."""

    def test_eurusd_no_conversion_needed(self) -> None:
        bars = _make_instrument_bars("EUR/USD", n=5000)
        config = BacktestConfig(
            strategy="sample_sma",
            instrument="EUR/USD",
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 4),
        )
        result = run_backtest(config, bars)
        assert result.num_trades > 0
        for trade in result.trades:
            assert trade.pnl_currency == "USD"

    def test_xauusd_commodity(self) -> None:
        bars = _make_instrument_bars("XAU/USD", n=5000)
        config = BacktestConfig(
            strategy="sample_sma",
            instrument="XAU/USD",
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 4),
            trade_size=Decimal("10"),  # 10 oz, not 100k
        )
        result = run_backtest(config, bars)
        assert result.num_trades > 0
        for trade in result.trades:
            assert trade.pnl_currency == "USD"
