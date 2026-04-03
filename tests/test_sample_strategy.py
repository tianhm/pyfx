"""Smoke test: run the sample SMA strategy on synthetic data."""

from decimal import Decimal

import numpy as np
import pandas as pd

from pyfx.backtest.runner import run_backtest
from pyfx.core.types import BacktestConfig


def test_sample_sma_backtest():
    """Run the SMA crossover strategy on synthetic data and verify it completes."""
    rng = np.random.default_rng(42)
    n = 500
    price = 1.10 + np.cumsum(rng.normal(0, 0.0002, n))
    spread = np.abs(rng.normal(0, 0.0001, n))

    bars_df = pd.DataFrame(
        {
            "open": price,
            "high": price + spread,
            "low": price - spread,
            "close": price + rng.normal(0, 0.00005, n),
            "volume": np.full(n, 1_000_000.0),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="1min", tz="UTC"),
    )
    # Ensure high >= max(open, close) and low <= min(open, close)
    bars_df["high"] = bars_df[["open", "high", "close"]].max(axis=1)
    bars_df["low"] = bars_df[["open", "low", "close"]].min(axis=1)

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
    assert isinstance(result.total_pnl, float)
    assert isinstance(result.win_rate, float)
