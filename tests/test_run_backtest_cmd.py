"""Tests for run_backtest management command."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from django.core.management import call_command

from pyfx.core.types import BacktestResult, BacktestConfig, EquityPoint, TradeRecord


def _make_data(tmp_path: Path) -> Path:
    """Create a minimal parquet data file."""
    df = pd.DataFrame(
        {"open": [1.1], "high": [1.2], "low": [1.0], "close": [1.15], "volume": [100.0]},
        index=pd.date_range("2024-03-01", periods=1, freq="1min", tz="UTC"),
    )
    path = tmp_path / "data.parquet"
    df.to_parquet(path)
    return path


def _make_result() -> BacktestResult:
    config = BacktestConfig(
        strategy="sample_sma",
        instrument="EUR/USD",
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 6, 1, tzinfo=UTC),
    )
    trade = TradeRecord(
        instrument="EUR/USD",
        side="BUY",
        quantity=100000.0,
        open_price=1.1,
        close_price=1.11,
        realized_pnl=100.0,
        realized_return_pct=1.0,
        opened_at=datetime(2024, 2, 1, tzinfo=UTC),
        closed_at=datetime(2024, 2, 2, tzinfo=UTC),
        duration_seconds=86400.0,
    )
    eq = EquityPoint(
        timestamp=datetime(2024, 2, 2, tzinfo=UTC),
        balance=100100.0,
    )
    return BacktestResult(
        config=config,
        total_pnl=100.0,
        total_return_pct=0.1,
        num_trades=1,
        win_rate=1.0,
        max_drawdown_pct=-0.5,
        trades=[trade],
        equity_curve=[eq],
        duration_seconds=1.0,
    )


@pytest.mark.django_db
class TestRunBacktestCommand:
    def test_runs_and_saves(self, tmp_path: Path) -> None:
        data_path = _make_data(tmp_path)
        mock_result = _make_result()

        with patch(
            "pyfx.backtest.runner.run_backtest",
            return_value=mock_result,
        ):
            out = StringIO()
            call_command(
                "run_backtest",
                strategy="sample_sma",
                start="2024-01-01",
                end="2024-12-01",
                data_file=data_path,
                stdout=out,
            )
            output = out.getvalue()
            assert "Saved" in output

    def test_empty_data(self, tmp_path: Path) -> None:
        """When data file has no rows in range, should print error."""
        df = pd.DataFrame(
            {"open": [1.1], "high": [1.2], "low": [1.0], "close": [1.15], "volume": [100.0]},
            index=pd.date_range("2020-01-01", periods=1, freq="1min", tz="UTC"),
        )
        path = tmp_path / "data.parquet"
        df.to_parquet(path)

        err = StringIO()
        call_command(
            "run_backtest",
            strategy="sample_sma",
            start="2024-01-01",
            end="2024-06-01",
            data_file=path,
            stderr=err,
        )
        assert "No data" in err.getvalue()

    def test_csv_file(self, tmp_path: Path) -> None:
        df = pd.DataFrame(
            {"open": [1.1], "high": [1.2], "low": [1.0], "close": [1.15], "volume": [100.0]},
            index=pd.date_range("2024-03-01", periods=1, freq="1min", tz="UTC"),
        )
        path = tmp_path / "data.csv"
        df.to_csv(path)
        mock_result = _make_result()

        with patch(
            "pyfx.backtest.runner.run_backtest",
            return_value=mock_result,
        ):
            out = StringIO()
            call_command(
                "run_backtest",
                strategy="sample_sma",
                start="2024-01-01",
                end="2024-12-01",
                data_file=path,
                stdout=out,
            )
            assert "Saved" in out.getvalue()

    def test_naive_tz_data(self, tmp_path: Path) -> None:
        """Naive-tz data should be localized."""
        df = pd.DataFrame(
            {"open": [1.1], "high": [1.2], "low": [1.0], "close": [1.15], "volume": [100.0]},
            index=pd.date_range("2024-03-01", periods=1, freq="1min"),  # naive
        )
        path = tmp_path / "data.parquet"
        df.to_parquet(path)
        mock_result = _make_result()

        with patch(
            "pyfx.backtest.runner.run_backtest",
            return_value=mock_result,
        ):
            out = StringIO()
            call_command(
                "run_backtest",
                strategy="sample_sma",
                start="2024-01-01",
                end="2024-12-01",
                data_file=path,
                stdout=out,
            )
            assert "Saved" in out.getvalue()

    def test_aware_dates(self, tmp_path: Path) -> None:
        """Pass ISO dates with tz to exercise the 'already aware' branch."""
        data_path = _make_data(tmp_path)
        mock_result = _make_result()

        with patch(
            "pyfx.backtest.runner.run_backtest",
            return_value=mock_result,
        ):
            out = StringIO()
            call_command(
                "run_backtest",
                strategy="sample_sma",
                start="2024-01-01T00:00:00+00:00",
                end="2024-12-01T00:00:00+00:00",
                data_file=data_path,
                stdout=out,
            )
            assert "Saved" in out.getvalue()

    def test_with_params(self, tmp_path: Path) -> None:
        data_path = _make_data(tmp_path)
        mock_result = _make_result()

        with patch(
            "pyfx.backtest.runner.run_backtest",
            return_value=mock_result,
        ) as mock_run:
            out = StringIO()
            call_command(
                "run_backtest",
                strategy="sample_sma",
                start="2024-01-01",
                end="2024-12-01",
                data_file=str(data_path),
                param=["fast_period=10", "mode=trend_follow", "threshold=0.5"],
                stdout=out,
            )
            config = mock_run.call_args[0][0]
            assert config.strategy_params["fast_period"] == 10
            assert config.strategy_params["mode"] == "trend_follow"
            assert config.strategy_params["threshold"] == 0.5
