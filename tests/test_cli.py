"""Tests for CLI entry point."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from pyfx.cli import _load_data, _parse_params, _save_to_django, _setup_django, main
from pyfx.core.types import parse_strategy_params
from pyfx.core.types import BacktestConfig, BacktestResult, EquityPoint, TradeRecord


# ---------------------------------------------------------------------------
# _parse_params
# ---------------------------------------------------------------------------

class TestParseParams:
    def test_int_value(self) -> None:
        assert _parse_params(("fast_period=10",)) == {"fast_period": 10}

    def test_float_value(self) -> None:
        assert _parse_params(("threshold=0.5",)) == {"threshold": 0.5}

    def test_string_value(self) -> None:
        assert _parse_params(("mode=trend_follow",)) == {"mode": "trend_follow"}

    def test_multiple_params(self) -> None:
        result = _parse_params(("a=1", "b=2.5", "c=hello"))
        assert result == {"a": 1, "b": 2.5, "c": "hello"}

    def test_empty(self) -> None:
        assert _parse_params(()) == {}

    def test_bool_values(self) -> None:
        result = _parse_params(("flag=true", "other=false"))
        assert result == {"flag": True, "other": False}


class TestParseStrategyParams:
    def test_tuple_input(self) -> None:
        result = parse_strategy_params(("a=1", "b=2.5", "c=hello"))
        assert result == {"a": 1, "b": 2.5, "c": "hello"}

    def test_dict_input(self) -> None:
        result = parse_strategy_params({"a": "1", "b": "2.5", "c": "hello"})
        assert result == {"a": 1, "b": 2.5, "c": "hello"}

    def test_bool_coercion(self) -> None:
        result = parse_strategy_params({"flag": "true", "other": "False"})
        assert result == {"flag": True, "other": False}

    def test_empty_tuple(self) -> None:
        assert parse_strategy_params(()) == {}

    def test_empty_dict(self) -> None:
        assert parse_strategy_params({}) == {}


# ---------------------------------------------------------------------------
# _load_data
# ---------------------------------------------------------------------------

class TestLoadData:
    def test_no_file_exits(self) -> None:
        with pytest.raises(SystemExit):
            _load_data(None, datetime(2024, 1, 1), datetime(2024, 6, 1))

    def test_parquet_file(self, tmp_path: Path) -> None:
        df = pd.DataFrame(
            {"open": [1.1], "high": [1.2], "low": [1.0], "close": [1.15], "volume": [100.0]},
            index=pd.date_range("2024-03-01", periods=1, freq="1min", tz="UTC"),
        )
        path = tmp_path / "data.parquet"
        df.to_parquet(path)
        result = _load_data(path, datetime(2024, 1, 1), datetime(2025, 1, 1))
        assert len(result) == 1

    def test_csv_file(self, tmp_path: Path) -> None:
        df = pd.DataFrame(
            {"open": [1.1], "high": [1.2], "low": [1.0], "close": [1.15], "volume": [100.0]},
            index=pd.date_range("2024-03-01", periods=1, freq="1min", tz="UTC"),
        )
        path = tmp_path / "data.csv"
        df.to_csv(path)
        result = _load_data(path, datetime(2024, 1, 1), datetime(2025, 1, 1))
        assert len(result) == 1

    def test_naive_tz_data(self, tmp_path: Path) -> None:
        """Naive-tz data should be localized to UTC."""
        df = pd.DataFrame(
            {"open": [1.1], "high": [1.2], "low": [1.0], "close": [1.15], "volume": [100.0]},
            index=pd.date_range("2024-03-01", periods=1, freq="1min"),  # naive
        )
        path = tmp_path / "data.parquet"
        df.to_parquet(path)
        result = _load_data(path, datetime(2024, 1, 1), datetime(2025, 1, 1))
        assert len(result) == 1

    def test_aware_start_end(self, tmp_path: Path) -> None:
        """Already tz-aware start/end should pass through."""
        df = pd.DataFrame(
            {"open": [1.1], "high": [1.2], "low": [1.0], "close": [1.15], "volume": [100.0]},
            index=pd.date_range("2024-03-01", periods=1, freq="1min", tz="UTC"),
        )
        path = tmp_path / "data.parquet"
        df.to_parquet(path)
        result = _load_data(
            path,
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 1, tzinfo=UTC),
        )
        assert len(result) == 1

    def test_file_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.parquet"
        with pytest.raises(SystemExit):
            _load_data(missing, datetime(2024, 1, 1), datetime(2025, 1, 1))

    def test_empty_after_filter(self, tmp_path: Path) -> None:
        df = pd.DataFrame(
            {"open": [1.1], "high": [1.2], "low": [1.0], "close": [1.15], "volume": [100.0]},
            index=pd.date_range("2024-03-01", periods=1, freq="1min", tz="UTC"),
        )
        path = tmp_path / "data.parquet"
        df.to_parquet(path)
        with pytest.raises(SystemExit):
            _load_data(path, datetime(2025, 6, 1), datetime(2025, 7, 1))


# ---------------------------------------------------------------------------
# _setup_django
# ---------------------------------------------------------------------------

class TestSetupDjango:
    def test_setup_django_sets_env(self) -> None:
        with patch.dict(os.environ, {}, clear=False), \
             patch("django.setup") as mock_setup:
            _setup_django()
            assert os.environ["DJANGO_SETTINGS_MODULE"] == "pyfx.web.pyfx_web.settings"
            mock_setup.assert_called_once()


class TestEnsureMigrated:
    def test_ensure_migrated_caches(self) -> None:
        import pyfx.cli as cli_module

        original = cli_module._migrated
        try:
            cli_module._migrated = False
            with patch("pyfx.cli._setup_django"), \
                 patch("django.core.management.call_command") as mock_call:
                cli_module._ensure_migrated()
                assert cli_module._migrated is True
                mock_call.assert_called_once()

                # Second call should be a no-op
                cli_module._ensure_migrated()
                mock_call.assert_called_once()  # still just once
        finally:
            cli_module._migrated = original


# ---------------------------------------------------------------------------
# _save_to_django
# ---------------------------------------------------------------------------

class TestSaveToDjango:
    def test_save_creates_records(self) -> None:
        config = BacktestConfig(
            strategy="test",
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
        result = BacktestResult(
            config=config,
            total_pnl=100.0,
            total_return_pct=0.1,
            num_trades=1,
            win_rate=1.0,
            max_drawdown_pct=-0.5,
            trades=[trade],
            equity_curve=[eq],
        )
        mock_run = MagicMock()
        with patch("pyfx.cli._ensure_migrated"), \
             patch("pyfx.web.dashboard.models.BacktestRun") as MockRun, \
             patch("pyfx.web.dashboard.models.Trade") as MockTrade, \
             patch("pyfx.web.dashboard.models.EquitySnapshot") as MockSnap:
            MockRun.objects.create.return_value = mock_run
            _save_to_django(result)
            MockRun.objects.create.assert_called_once()
            MockTrade.objects.bulk_create.assert_called_once()
            MockSnap.objects.bulk_create.assert_called_once()

    def test_save_naive_datetimes(self) -> None:
        """Naive start/end should be converted to UTC without mutating the model."""
        config = BacktestConfig(
            strategy="test",
            instrument="EUR/USD",
            start=datetime(2024, 1, 1),  # naive
            end=datetime(2024, 6, 1),  # naive
        )
        result = BacktestResult(
            config=config,
            total_pnl=0.0,
            total_return_pct=0.0,
            num_trades=0,
            win_rate=0.0,
            max_drawdown_pct=0.0,
        )
        with patch("pyfx.cli._ensure_migrated"), \
             patch("pyfx.web.dashboard.models.BacktestRun") as MockRun, \
             patch("pyfx.web.dashboard.models.Trade"), \
             patch("pyfx.web.dashboard.models.EquitySnapshot"):
            MockRun.objects.create.return_value = MagicMock()
            _save_to_django(result)
            call_kwargs = MockRun.objects.create.call_args[1]
            assert call_kwargs["start"].tzinfo is not None
            assert call_kwargs["end"].tzinfo is not None


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

class TestBacktestCommand:
    def test_backtest_runs(self, tmp_path: Path) -> None:
        df = pd.DataFrame(
            {"open": [1.1], "high": [1.2], "low": [1.0], "close": [1.15], "volume": [100.0]},
            index=pd.date_range("2024-03-01", periods=1, freq="1min", tz="UTC"),
        )
        path = tmp_path / "data.parquet"
        df.to_parquet(path)

        mock_result = BacktestResult(
            config=BacktestConfig(
                strategy="test",
                instrument="EUR/USD",
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 6, 1, tzinfo=UTC),
            ),
            total_pnl=50.0,
            total_return_pct=0.05,
            num_trades=2,
            win_rate=0.5,
            max_drawdown_pct=-1.0,
            profit_factor=1.5,
            duration_seconds=1.0,
        )

        with patch("pyfx.backtest.runner.run_backtest", return_value=mock_result):
            runner = CliRunner()
            result = runner.invoke(main, [
                "backtest",
                "-s", "test",
                "--start", "2024-01-01",
                "--end", "2024-06-01",
                "--data-file", str(path),
            ])
            assert result.exit_code == 0
            assert "50.00" in result.output

    def test_backtest_with_save(self, tmp_path: Path) -> None:
        df = pd.DataFrame(
            {"open": [1.1], "high": [1.2], "low": [1.0], "close": [1.15], "volume": [100.0]},
            index=pd.date_range("2024-03-01", periods=1, freq="1min", tz="UTC"),
        )
        path = tmp_path / "data.parquet"
        df.to_parquet(path)

        mock_result = BacktestResult(
            config=BacktestConfig(
                strategy="test",
                instrument="EUR/USD",
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 6, 1, tzinfo=UTC),
            ),
            total_pnl=0.0,
            total_return_pct=0.0,
            num_trades=0,
            win_rate=0.0,
            max_drawdown_pct=0.0,
            duration_seconds=1.0,
        )

        with patch("pyfx.backtest.runner.run_backtest", return_value=mock_result), \
             patch("pyfx.cli._save_to_django") as mock_save:
            runner = CliRunner()
            result = runner.invoke(main, [
                "backtest",
                "-s", "test",
                "--start", "2024-01-01",
                "--end", "2024-06-01",
                "--data-file", str(path),
                "--save",
            ])
            assert result.exit_code == 0
            mock_save.assert_called_once()

    def test_backtest_with_params(self, tmp_path: Path) -> None:
        df = pd.DataFrame(
            {"open": [1.1], "high": [1.2], "low": [1.0], "close": [1.15], "volume": [100.0]},
            index=pd.date_range("2024-03-01", periods=1, freq="1min", tz="UTC"),
        )
        path = tmp_path / "data.parquet"
        df.to_parquet(path)

        mock_result = BacktestResult(
            config=BacktestConfig(
                strategy="test",
                instrument="EUR/USD",
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 6, 1, tzinfo=UTC),
            ),
            total_pnl=0.0,
            total_return_pct=0.0,
            num_trades=0,
            win_rate=0.0,
            max_drawdown_pct=0.0,
            duration_seconds=1.0,
        )

        with patch("pyfx.backtest.runner.run_backtest", return_value=mock_result) as mock_run:
            runner = CliRunner()
            result = runner.invoke(main, [
                "backtest",
                "-s", "test",
                "--start", "2024-01-01",
                "--end", "2024-06-01",
                "--data-file", str(path),
                "-p", "fast_period=10",
                "-p", "mode=trend_follow",
            ])
            assert result.exit_code == 0
            call_config = mock_run.call_args[0][0]
            assert call_config.strategy_params == {"fast_period": 10, "mode": "trend_follow"}


class TestStrategiesCommand:
    def test_list_strategies(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["strategies"])
        assert result.exit_code == 0
        assert "sample_sma" in result.output or "Available strategies" in result.output

    def test_no_strategies(self) -> None:
        with patch("pyfx.strategies.loader.discover_strategies", return_value={}):
            runner = CliRunner()
            result = runner.invoke(main, ["strategies"])
            assert "No strategies found" in result.output


class TestGenerateSampleData:
    def test_generates_data(self, tmp_path: Path) -> None:
        output = tmp_path / "test.parquet"
        runner = CliRunner()
        result = runner.invoke(main, [
            "generate-sample-data",
            "--days", "2",
            "-o", str(output),
        ])
        assert result.exit_code == 0
        assert output.exists()
        df = pd.read_parquet(output)
        assert len(df) == 2 * 24 * 60

    def test_generates_data_default_output(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without -o, should use default data_dir path."""
        monkeypatch.setattr("pyfx.core.config.settings.data_dir", tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, [
            "generate-sample-data",
            "--days", "1",
        ])
        assert result.exit_code == 0
        generated = list(tmp_path.glob("*.parquet"))
        assert len(generated) == 1


class TestIngestCommand:
    def test_ingest(self, tmp_path: Path) -> None:
        # Create a minimal Dukascopy-format CSV
        csv_path = tmp_path / "data.csv"
        csv_path.write_text(
            "timestamp,open,high,low,close,volume\n"
            "1704067200000,1.1,1.2,1.0,1.15,1000\n"
        )
        output = tmp_path / "data.parquet"
        runner = CliRunner()
        result = runner.invoke(main, [
            "ingest",
            "-i", str(csv_path),
            "-o", str(output),
        ])
        assert result.exit_code == 0
        assert output.exists()


class TestWebCommand:
    def test_web_starts(self) -> None:
        with patch("pyfx.cli._ensure_migrated"), \
             patch("pyfx.data.scanner.scan_data_directory", return_value=(2, 3)), \
             patch("django.core.management.execute_from_command_line") as mock_exec:
            runner = CliRunner()
            result = runner.invoke(main, ["web", "--no-reload"])
            assert result.exit_code == 0
            mock_exec.assert_called_once()
            argv = mock_exec.call_args[0][0]
            assert "--noreload" in argv

    def test_web_no_scan_results(self) -> None:
        with patch("pyfx.cli._ensure_migrated"), \
             patch("pyfx.data.scanner.scan_data_directory", return_value=(0, 5)), \
             patch("django.core.management.execute_from_command_line"):
            runner = CliRunner()
            result = runner.invoke(main, ["web"])
            assert result.exit_code == 0
            assert "Registered" not in result.output


class TestManageCommand:
    def test_manage_passes_args(self) -> None:
        with patch("pyfx.cli._setup_django"), \
             patch("django.core.management.execute_from_command_line") as mock_exec:
            runner = CliRunner()
            result = runner.invoke(main, ["manage", "migrate"])
            assert result.exit_code == 0
            argv = mock_exec.call_args[0][0]
            assert "migrate" in argv


class TestDataListCommand:
    def test_data_list_empty(self) -> None:
        mock_qs = MagicMock()
        mock_qs.__bool__ = lambda _: False
        with patch("pyfx.cli._setup_django"), \
             patch("pyfx.web.dashboard.models.Dataset") as MockDataset:
            MockDataset.objects.all.return_value = mock_qs
            runner = CliRunner()
            result = runner.invoke(main, ["data", "list"])
            assert result.exit_code == 0
            assert "No datasets" in result.output

    def test_data_list_with_datasets(self) -> None:
        mock_ds = MagicMock()
        mock_ds.instrument = "EUR/USD"
        mock_ds.timeframe = "M1"
        mock_ds.start_date = "2024-01-01"
        mock_ds.end_date = "2024-06-01"
        mock_ds.row_count = 1000
        mock_ds.display_size = "1.0 MB"
        mock_ds.source = "manual"
        mock_ds.status = "ready"
        mock_qs = MagicMock()
        mock_qs.__bool__ = lambda _: True
        mock_qs.__iter__ = lambda _: iter([mock_ds])
        with patch("pyfx.cli._setup_django"), \
             patch("pyfx.web.dashboard.models.Dataset") as MockDataset:
            MockDataset.objects.all.return_value = mock_qs
            runner = CliRunner()
            result = runner.invoke(main, ["data", "list"])
            assert result.exit_code == 0
            assert "EUR/USD" in result.output


class TestDataScanCommand:
    def test_data_scan(self) -> None:
        with patch("pyfx.cli._ensure_migrated"), \
             patch("pyfx.data.scanner.scan_data_directory", return_value=(3, 2)):
            runner = CliRunner()
            result = runner.invoke(main, ["data", "scan"])
            assert result.exit_code == 0
            assert "3" in result.output
