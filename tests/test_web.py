"""Tests for the Django dashboard views, APIs, and management commands."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from django.test import Client, TestCase

from pyfx.web.dashboard.models import BacktestRun, Dataset, EquitySnapshot, Trade


def _create_run(
    strategy: str = "sample_sma",
    status: str = BacktestRun.STATUS_COMPLETED,
    total_pnl: float = 150.0,
    total_return_pct: float = 0.15,
    num_trades: int = 5,
    win_rate: float = 0.6,
    max_drawdown_pct: float = -1.2,
    **kwargs,
) -> BacktestRun:
    defaults = dict(
        strategy=strategy,
        instrument="EUR/USD",
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 6, 30, tzinfo=UTC),
        bar_type="1-MINUTE-LAST-EXTERNAL",
        balance=100_000.0,
        leverage=50.0,
        status=status,
        total_pnl=total_pnl,
        total_return_pct=total_return_pct,
        num_trades=num_trades,
        win_rate=win_rate,
        max_drawdown_pct=max_drawdown_pct,
        avg_trade_pnl=30.0,
        avg_win=80.0,
        avg_loss=-20.0,
        profit_factor=2.0,
        duration_seconds=3.5,
    )
    defaults.update(kwargs)
    return BacktestRun.objects.create(**defaults)


def _create_trade(run: BacktestRun, pnl: float = 50.0) -> Trade:
    return Trade.objects.create(
        run=run,
        instrument="EUR/USD",
        side="BUY",
        quantity=100_000,
        open_price=1.10000,
        close_price=1.10050,
        realized_pnl=pnl,
        realized_return_pct=0.05,
        opened_at=datetime(2024, 1, 2, tzinfo=UTC),
        closed_at=datetime(2024, 1, 2, 1, 0, tzinfo=UTC),
        duration_seconds=3600,
    )


def _create_equity(run: BacktestRun) -> EquitySnapshot:
    return EquitySnapshot.objects.create(
        run=run,
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        balance=100_000.0,
    )


class OverviewViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_overview_empty(self):
        resp = self.client.get("/")
        assert resp.status_code == 200
        assert b"Welcome to pyfx" in resp.content

    def test_overview_with_runs(self):
        _create_run(total_return_pct=5.0, max_drawdown_pct=-2.0)
        _create_run(
            strategy="other_strat",
            total_return_pct=-1.0,
            max_drawdown_pct=-8.0,
        )
        resp = self.client.get("/")
        assert resp.status_code == 200
        assert b"Overview" in resp.content
        # Summary cards present
        content = resp.content.decode()
        assert "Best Return" in content
        assert "Best Profit Factor" in content
        assert "Worst Drawdown" in content

    def test_overview_context_values(self):
        _create_run(total_return_pct=10.0, max_drawdown_pct=-3.0, win_rate=0.7)
        resp = self.client.get("/")
        ctx = resp.context
        assert ctx["total_runs"] == 1
        assert ctx["best_return"] == 10.0
        assert ctx["worst_drawdown"] == -3.0
        assert ctx["strategies_tested"] == 1

    def test_overview_shows_running_alert(self):
        _create_run(status=BacktestRun.STATUS_RUNNING)
        resp = self.client.get("/")
        content = resp.content.decode()
        assert "running" in content.lower()

    def test_overview_active_nav(self):
        resp = self.client.get("/")
        assert resp.context["active_nav"] == "overview"


class BacktestListViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_list_empty(self):
        resp = self.client.get("/backtests/")
        assert resp.status_code == 200
        assert b"No backtests yet" in resp.content

    def test_list_with_runs(self):
        _create_run()
        resp = self.client.get("/backtests/")
        assert resp.status_code == 200
        assert b"sample_sma" in resp.content

    def test_list_shows_status_icons(self):
        _create_run(status=BacktestRun.STATUS_COMPLETED)
        _create_run(status=BacktestRun.STATUS_FAILED, error_message="boom")
        resp = self.client.get("/backtests/")
        assert resp.status_code == 200

    def test_list_running_count(self):
        _create_run(status=BacktestRun.STATUS_RUNNING)
        resp = self.client.get("/backtests/")
        assert resp.context["running_count"] == 1

    def test_list_active_nav(self):
        resp = self.client.get("/backtests/")
        assert resp.context["active_nav"] == "backtests"

    def test_list_contains_new_backtest_button(self):
        resp = self.client.get("/backtests/")
        assert b"New Backtest" in resp.content

    def test_list_contains_action_buttons(self):
        run = _create_run()
        resp = self.client.get("/backtests/")
        assert f"/run/{run.pk}/rerun/".encode() in resp.content
        assert b"confirmDeleteBacktest(" in resp.content
        assert b"delete-backtest-modal" in resp.content


class BacktestDetailViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.run = _create_run()
        _create_trade(self.run, pnl=100.0)
        _create_trade(self.run, pnl=-50.0)
        _create_equity(self.run)

    def test_detail_200(self):
        resp = self.client.get(f"/run/{self.run.pk}/")
        assert resp.status_code == 200

    def test_detail_context(self):
        resp = self.client.get(f"/run/{self.run.pk}/")
        assert resp.context["run"] == self.run
        assert resp.context["active_nav"] == "backtests"
        assert len(resp.context["trades"]) == 2

    def test_detail_cumulative_pnl(self):
        resp = self.client.get(f"/run/{self.run.pk}/")
        pnl = json.loads(resp.context["cumulative_pnl_json"])
        assert len(pnl) == 2
        assert pnl[0]["value"] == 100.0
        assert pnl[1]["value"] == 50.0
        assert "time" in pnl[0]
        assert isinstance(pnl[0]["time"], int)

    def test_detail_trade_markers_json(self):
        resp = self.client.get(f"/run/{self.run.pk}/")
        markers = json.loads(resp.context["trade_markers_json"])
        # 2 trades × 2 markers (entry + exit) = 4
        assert len(markers) == 4
        assert markers[0]["type"] == "entry"
        assert markers[1]["type"] == "exit"
        assert "time" in markers[0]
        assert "side" in markers[0]
        assert "price" in markers[0]

    def test_detail_available_timeframes(self):
        resp = self.client.get(f"/run/{self.run.pk}/")
        tfs = json.loads(resp.context["available_timeframes_json"])
        assert "1-MINUTE-LAST-EXTERNAL" in tfs
        assert "5-MINUTE-LAST-EXTERNAL" in tfs

    def test_detail_chart_indicators_json(self):
        resp = self.client.get(f"/run/{self.run.pk}/")
        inds = json.loads(resp.context["chart_indicators_json"])
        assert isinstance(inds, list)

    def test_detail_extra_bar_types_in_timeframes(self):
        run = _create_run(extra_bar_types=["5-MINUTE-LAST-EXTERNAL", "15-MINUTE-LAST-EXTERNAL"])
        resp = self.client.get(f"/run/{run.pk}/")
        tfs = json.loads(resp.context["available_timeframes_json"])
        assert "5-MINUTE-LAST-EXTERNAL" in tfs
        assert "15-MINUTE-LAST-EXTERNAL" in tfs
        assert "1-MINUTE-LAST-EXTERNAL" in tfs

    def test_detail_chart_indicators_strategy_defaults(self):
        """Strategy chart_indicators() are loaded into context."""
        run = _create_run(strategy="sample_sma")
        resp = self.client.get(f"/run/{run.pk}/")
        inds = json.loads(resp.context["chart_indicators_json"])
        # sample_sma has SMA(10) and SMA(50)
        assert len(inds) >= 2

    def test_detail_chart_indicators_unknown_strategy(self):
        """Unknown strategy should not crash, just return empty list."""
        run = _create_run(strategy="nonexistent_strategy_xyz")
        resp = self.client.get(f"/run/{run.pk}/")
        inds = json.loads(resp.context["chart_indicators_json"])
        assert inds == []

    def test_detail_404(self):
        resp = self.client.get("/run/99999/")
        assert resp.status_code == 404

    def test_detail_failed_run(self):
        run = _create_run(
            status=BacktestRun.STATUS_FAILED,
            error_message="Something broke",
        )
        resp = self.client.get(f"/run/{run.pk}/")
        assert resp.status_code == 200
        assert b"Backtest Failed" in resp.content

    def test_detail_running_run(self):
        run = _create_run(status=BacktestRun.STATUS_RUNNING)
        resp = self.client.get(f"/run/{run.pk}/")
        assert resp.status_code == 200
        assert b"running" in resp.content.lower()

    def test_detail_shows_strategy_params(self):
        run = _create_run(strategy_params={"entry_mode": "relaxed", "rsi_period": 14})
        resp = self.client.get(f"/run/{run.pk}/")
        content = resp.content.decode()
        assert "entry_mode" in content
        assert "relaxed" in content
        assert "rsi_period" in content


class BacktestDeleteViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_delete_post(self):
        run = _create_run()
        resp = self.client.post(f"/run/{run.pk}/delete/")
        assert resp.status_code == 302
        assert not BacktestRun.objects.filter(pk=run.pk).exists()

    def test_delete_get_redirects(self):
        run = _create_run()
        resp = self.client.get(f"/run/{run.pk}/delete/")
        assert resp.status_code == 302

    def test_delete_404(self):
        resp = self.client.post("/run/99999/delete/")
        assert resp.status_code == 404


class BacktestRerunViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("pyfx.web.dashboard.views.subprocess.Popen")
    def test_rerun_post_clones_run(self, mock_popen):
        original = _create_run(
            strategy="coban_reborn",
            strategy_params={"entry_mode": "trend_follow"},
            data_file="/tmp/test.parquet",
        )
        resp = self.client.post(f"/run/{original.pk}/rerun/")
        assert resp.status_code == 302
        new_run = BacktestRun.objects.exclude(pk=original.pk).get()
        assert new_run.strategy == "coban_reborn"
        assert new_run.instrument == original.instrument
        assert new_run.strategy_params == {"entry_mode": "trend_follow"}
        assert new_run.data_file == "/tmp/test.parquet"
        assert new_run.status == BacktestRun.STATUS_RUNNING
        mock_popen.assert_called_once()

    def test_rerun_get_redirects(self):
        run = _create_run()
        resp = self.client.get(f"/run/{run.pk}/rerun/")
        assert resp.status_code == 302

    def test_rerun_404(self):
        resp = self.client.post("/run/99999/rerun/")
        assert resp.status_code == 404


class BacktestStartViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_start_get_redirects(self):
        resp = self.client.get("/backtests/start/")
        assert resp.status_code == 302

    @patch("pyfx.web.dashboard.views.subprocess.Popen")
    def test_start_post_creates_run(self, mock_popen):
        resp = self.client.post("/backtests/start/", {
            "strategy": "sample_sma",
            "instrument": "EUR/USD",
            "start": "2024-01-01",
            "end": "2024-06-30",
            "data_file": "/tmp/test.parquet",
            "balance": "100000",
            "leverage": "50",
            "trade_size": "100000",
            "bar_type": "1-MINUTE-LAST-EXTERNAL",
        })
        assert resp.status_code == 302
        run = BacktestRun.objects.latest("created_at")
        assert run.strategy == "sample_sma"
        assert run.status == BacktestRun.STATUS_RUNNING
        assert run.data_file == "/tmp/test.parquet"
        mock_popen.assert_called_once()

    @patch("pyfx.web.dashboard.views.subprocess.Popen")
    def test_start_with_strategy_params(self, mock_popen):
        resp = self.client.post("/backtests/start/", {
            "strategy": "sample_sma",
            "instrument": "EUR/USD",
            "start": "2024-01-01",
            "end": "2024-06-30",
            "data_file": "/tmp/test.parquet",
            "balance": "100000",
            "leverage": "50",
            "trade_size": "100000",
            "bar_type": "1-MINUTE-LAST-EXTERNAL",
            "param_fast_period": "10",
            "param_slow_period": "50",
        })
        assert resp.status_code == 302
        run = BacktestRun.objects.latest("created_at")
        assert run.strategy_params == {"fast_period": 10, "slow_period": 50}

    @patch("pyfx.web.dashboard.views.subprocess.Popen")
    def test_start_with_float_and_string_params(self, mock_popen):
        resp = self.client.post("/backtests/start/", {
            "strategy": "sample_sma",
            "instrument": "EUR/USD",
            "start": "2024-01-01",
            "end": "2024-06-30",
            "data_file": "/tmp/test.parquet",
            "balance": "100000",
            "leverage": "50",
            "trade_size": "100000",
            "bar_type": "1-MINUTE-LAST-EXTERNAL",
            "param_threshold": "1.5",
            "param_mode": "aggressive",
        })
        assert resp.status_code == 302
        run = BacktestRun.objects.latest("created_at")
        assert run.strategy_params["threshold"] == 1.5
        assert run.strategy_params["mode"] == "aggressive"

    @patch("pyfx.web.dashboard.views.subprocess.Popen")
    def test_start_expands_tilde(self, mock_popen):
        self.client.post("/backtests/start/", {
            "strategy": "sample_sma",
            "instrument": "EUR/USD",
            "start": "2024-01-01",
            "end": "2024-06-30",
            "data_file": "~/data/test.parquet",
            "balance": "100000",
            "leverage": "50",
            "trade_size": "100000",
            "bar_type": "1-MINUTE-LAST-EXTERNAL",
        })
        run = BacktestRun.objects.latest("created_at")
        assert "~" not in run.data_file


class BacktestNewViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_new_page_200(self):
        resp = self.client.get("/backtests/new/")
        assert resp.status_code == 200
        assert b"New Backtest" in resp.content

    def test_new_page_active_nav(self):
        resp = self.client.get("/backtests/new/")
        assert resp.context["active_nav"] == "backtests"

    def test_list_links_to_new_page(self):
        resp = self.client.get("/backtests/")
        assert b"/backtests/new/" in resp.content


class ApiStrategiesViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_api_strategies_returns_json(self):
        resp = self.client.get("/api/strategies/")
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert isinstance(data, list)
        # At minimum, sample_sma should be available if installed
        names = [s["name"] for s in data]
        if "sample_sma" in names:
            sma = next(s for s in data if s["name"] == "sample_sma")
            param_names = [p["name"] for p in sma["params"]]
            assert "fast_period" in param_names
            assert "slow_period" in param_names


class ApiBarsViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.run = _create_run()

    @patch("pyfx.data.resample.load_bars")
    def test_bars_ok(self, mock_load: MagicMock) -> None:
        import pandas as pd

        df = pd.DataFrame(
            {
                "open": [1.1, 1.2],
                "high": [1.15, 1.25],
                "low": [1.05, 1.15],
                "close": [1.12, 1.22],
                "volume": [100.0, 200.0],
            },
            index=pd.date_range("2024-01-02", periods=2, freq="min", tz="UTC"),
        )
        mock_load.return_value = df
        resp = self.client.get(f"/api/run/{self.run.pk}/bars/?timeframe=5-MINUTE-LAST-EXTERNAL")
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert len(data) == 2
        assert "time" in data[0]
        assert "open" in data[0]
        assert "high" in data[0]
        assert "low" in data[0]
        assert "close" in data[0]
        assert "volume" in data[0]

    @patch("pyfx.data.resample.load_bars")
    def test_bars_file_not_found(self, mock_load: MagicMock) -> None:
        mock_load.side_effect = FileNotFoundError("Data file not found")
        resp = self.client.get(f"/api/run/{self.run.pk}/bars/")
        assert resp.status_code == 404

    def test_bars_404_run(self) -> None:
        resp = self.client.get("/api/run/99999/bars/")
        assert resp.status_code == 404

    def test_bars_invalid_timeframe(self) -> None:
        resp = self.client.get(f"/api/run/{self.run.pk}/bars/?timeframe=bad-value")
        assert resp.status_code == 400

    @patch("pyfx.data.resample.load_bars")
    def test_bars_hard_cap(self, mock_load: MagicMock) -> None:
        """Explicit timeframe with >10k bars gets truncated."""
        import pandas as pd

        big_df = pd.DataFrame(
            {
                "open": [1.1] * 15000,
                "high": [1.2] * 15000,
                "low": [1.0] * 15000,
                "close": [1.15] * 15000,
                "volume": [100.0] * 15000,
            },
            index=pd.date_range("2024-01-02", periods=15000, freq="min", tz="UTC"),
        )
        mock_load.return_value = big_df
        resp = self.client.get(
            f"/api/run/{self.run.pk}/bars/?timeframe=1-MINUTE-LAST-EXTERNAL"
        )
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert len(data) == 10000

    @patch("pyfx.data.resample.load_bars")
    def test_bars_auto_downsample(self, mock_load: MagicMock) -> None:
        import pandas as pd

        # First call returns large dataset (>10k), second returns downsampled
        big_df = pd.DataFrame(
            {
                "open": [1.1] * 15000,
                "high": [1.2] * 15000,
                "low": [1.0] * 15000,
                "close": [1.15] * 15000,
                "volume": [100.0] * 15000,
            },
            index=pd.date_range("2024-01-02", periods=15000, freq="min", tz="UTC"),
        )
        small_df = pd.DataFrame(
            {
                "open": [1.1] * 100,
                "high": [1.2] * 100,
                "low": [1.0] * 100,
                "close": [1.15] * 100,
                "volume": [100.0] * 100,
            },
            index=pd.date_range("2024-01-02", periods=100, freq="5min", tz="UTC"),
        )
        mock_load.side_effect = [big_df, small_df]
        resp = self.client.get(f"/api/run/{self.run.pk}/bars/")
        assert resp.status_code == 200
        # load_bars called twice: once without tf, once with auto tf
        assert mock_load.call_count == 2


class ApiIndicatorsViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.run = _create_run()

    @patch("pyfx.data.resample.compute_indicator")
    @patch("pyfx.data.resample.load_bars")
    def test_indicator_sma(self, mock_load: MagicMock, mock_compute: MagicMock) -> None:
        import pandas as pd

        df = pd.DataFrame(
            {"close": [1.1, 1.2, 1.3]},
            index=pd.date_range("2024-01-02", periods=3, freq="min", tz="UTC"),
        )
        mock_load.return_value = df
        mock_compute.return_value = pd.Series(
            [float("nan"), 1.15, 1.25],
            index=df.index,
        )
        resp = self.client.get(
            f"/api/run/{self.run.pk}/indicators/?name=sma&period=2"
        )
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert len(data) == 2  # NaN dropped
        assert "time" in data[0]
        assert "value" in data[0]

    @patch("pyfx.data.resample.compute_indicator")
    @patch("pyfx.data.resample.load_bars")
    def test_indicator_macd(self, mock_load: MagicMock, mock_compute: MagicMock) -> None:
        import pandas as pd

        df = pd.DataFrame(
            {"close": [1.1, 1.2, 1.3]},
            index=pd.date_range("2024-01-02", periods=3, freq="min", tz="UTC"),
        )
        mock_load.return_value = df
        mock_compute.return_value = {
            "macd": pd.Series([0.01, 0.02, 0.03], index=df.index),
            "signal": pd.Series([0.005, 0.015, 0.025], index=df.index),
            "histogram": pd.Series([0.005, 0.005, 0.005], index=df.index),
        }
        resp = self.client.get(
            f"/api/run/{self.run.pk}/indicators/?name=macd&period=12"
        )
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert "macd" in data
        assert "signal" in data
        assert "histogram" in data

    def test_indicator_missing_name(self) -> None:
        resp = self.client.get(f"/api/run/{self.run.pk}/indicators/")
        assert resp.status_code == 400

    def test_indicator_invalid_name(self) -> None:
        resp = self.client.get(
            f"/api/run/{self.run.pk}/indicators/?name=bollinger&period=20"
        )
        assert resp.status_code == 400

    def test_indicator_invalid_period(self) -> None:
        resp = self.client.get(
            f"/api/run/{self.run.pk}/indicators/?name=sma&period=abc"
        )
        assert resp.status_code == 400

    def test_indicator_period_out_of_range(self) -> None:
        resp = self.client.get(
            f"/api/run/{self.run.pk}/indicators/?name=sma&period=0"
        )
        assert resp.status_code == 400
        resp = self.client.get(
            f"/api/run/{self.run.pk}/indicators/?name=sma&period=999"
        )
        assert resp.status_code == 400

    def test_indicator_invalid_timeframe(self) -> None:
        resp = self.client.get(
            f"/api/run/{self.run.pk}/indicators/?name=sma&period=20&timeframe=bad"
        )
        assert resp.status_code == 400

    @patch("pyfx.data.resample.load_bars")
    def test_indicator_file_not_found(self, mock_load: MagicMock) -> None:
        mock_load.side_effect = FileNotFoundError("not found")
        resp = self.client.get(
            f"/api/run/{self.run.pk}/indicators/?name=sma&period=20"
        )
        assert resp.status_code == 404


class ApiEquityCurveViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.run = _create_run()

    def test_equity_empty(self):
        resp = self.client.get(f"/api/run/{self.run.pk}/equity/")
        assert resp.status_code == 200
        assert json.loads(resp.content) == []

    def test_equity_with_data(self):
        _create_equity(self.run)
        resp = self.client.get(f"/api/run/{self.run.pk}/equity/")
        data = json.loads(resp.content)
        assert len(data) == 1
        assert data[0]["value"] == 100_000.0
        assert "time" in data[0]

    def test_equity_404(self):
        resp = self.client.get("/api/run/99999/equity/")
        assert resp.status_code == 404


class ApiTradesViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.run = _create_run()

    def test_trades_empty(self):
        resp = self.client.get(f"/api/run/{self.run.pk}/trades/")
        assert resp.status_code == 200
        assert json.loads(resp.content) == []

    def test_trades_with_data(self):
        _create_trade(self.run, pnl=75.0)
        resp = self.client.get(f"/api/run/{self.run.pk}/trades/")
        data = json.loads(resp.content)
        assert len(data) == 1
        assert data[0]["pnl"] == 75.0
        assert data[0]["side"] == "BUY"

    def test_trades_404(self):
        resp = self.client.get("/api/run/99999/trades/")
        assert resp.status_code == 404


class ApiBacktestStatusViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_status_completed(self):
        run = _create_run(status=BacktestRun.STATUS_COMPLETED)
        resp = self.client.get(f"/api/run/{run.pk}/status/")
        data = json.loads(resp.content)
        assert data["status"] == "completed"
        assert data["error_message"] == ""

    def test_status_running(self):
        run = _create_run(status=BacktestRun.STATUS_RUNNING)
        resp = self.client.get(f"/api/run/{run.pk}/status/")
        data = json.loads(resp.content)
        assert data["status"] == "running"

    def test_status_includes_progress(self):
        run = _create_run(
            status=BacktestRun.STATUS_RUNNING,
            progress_pct=20,
            progress_message="Running engine...",
            total_bars=525600,
        )
        resp = self.client.get(f"/api/run/{run.pk}/status/")
        data = json.loads(resp.content)
        assert data["progress_pct"] == 20
        assert data["progress_message"] == "Running engine..."
        assert data["total_bars"] == 525600

    def test_status_failed(self):
        run = _create_run(
            status=BacktestRun.STATUS_FAILED,
            error_message="File not found",
        )
        resp = self.client.get(f"/api/run/{run.pk}/status/")
        data = json.loads(resp.content)
        assert data["status"] == "failed"
        assert data["error_message"] == "File not found"

    def test_status_404(self):
        resp = self.client.get("/api/run/99999/status/")
        assert resp.status_code == 404


class ApiRunningCountViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_no_running(self):
        resp = self.client.get("/api/running-count/")
        data = json.loads(resp.content)
        assert data["running"] == 0

    def test_with_running(self):
        _create_run(status=BacktestRun.STATUS_RUNNING)
        _create_run(status=BacktestRun.STATUS_RUNNING)
        _create_run(status=BacktestRun.STATUS_COMPLETED)
        resp = self.client.get("/api/running-count/")
        data = json.loads(resp.content)
        assert data["running"] == 2


class ApiRunningBacktestsViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_no_running(self):
        resp = self.client.get("/api/running-backtests/")
        data = json.loads(resp.content)
        assert data == []

    def test_returns_running_only(self):
        _create_run(status=BacktestRun.STATUS_RUNNING)
        _create_run(status=BacktestRun.STATUS_COMPLETED)
        _create_run(status=BacktestRun.STATUS_FAILED)
        resp = self.client.get("/api/running-backtests/")
        data = json.loads(resp.content)
        assert len(data) == 1

    def test_response_fields(self):
        run = _create_run(
            status=BacktestRun.STATUS_RUNNING,
            progress_pct=20,
            progress_message="Running engine...",
            total_bars=100000,
        )
        resp = self.client.get("/api/running-backtests/")
        data = json.loads(resp.content)
        assert len(data) == 1
        bt = data[0]
        assert bt["id"] == run.pk
        assert bt["strategy"] == "sample_sma"
        assert bt["instrument"] == "EUR/USD"
        assert bt["start"] == "2024-01-01"
        assert bt["end"] == "2024-06-30"
        assert "created_at" in bt
        assert bt["progress_pct"] == 20
        assert bt["progress_message"] == "Running engine..."
        assert bt["total_bars"] == 100000


class ModelTests(TestCase):
    def test_backtest_run_str(self):
        run = _create_run(total_return_pct=5.5)
        assert "sample_sma" in str(run)
        assert "EUR/USD" in str(run)

    def test_trade_str(self):
        run = _create_run()
        trade = _create_trade(run, pnl=42.0)
        assert "BUY" in str(trade)
        assert "42.00" in str(trade)

    def test_backtest_run_ordering(self):
        _create_run()
        r2 = _create_run()
        runs = list(BacktestRun.objects.all())
        # Most recent first
        assert runs[0].pk == r2.pk

    def test_trade_ordering(self):
        run = _create_run()
        Trade.objects.create(
            run=run, instrument="EUR/USD", side="BUY", quantity=100_000,
            open_price=1.1, close_price=1.2, realized_pnl=10,
            opened_at=datetime(2024, 1, 3, tzinfo=UTC),
            closed_at=datetime(2024, 1, 3, 1, tzinfo=UTC),
        )
        t2 = Trade.objects.create(
            run=run, instrument="EUR/USD", side="SELL", quantity=100_000,
            open_price=1.1, close_price=1.0, realized_pnl=-10,
            opened_at=datetime(2024, 1, 1, tzinfo=UTC),
            closed_at=datetime(2024, 1, 1, 1, tzinfo=UTC),
        )
        trades = list(Trade.objects.filter(run=run))
        assert trades[0].pk == t2.pk  # Earlier opened_at first

    def test_equity_snapshot_ordering(self):
        run = _create_run()
        EquitySnapshot.objects.create(
            run=run, timestamp=datetime(2024, 1, 2, tzinfo=UTC), balance=101_000,
        )
        e2 = EquitySnapshot.objects.create(
            run=run, timestamp=datetime(2024, 1, 1, tzinfo=UTC), balance=100_000,
        )
        snaps = list(EquitySnapshot.objects.filter(run=run))
        assert snaps[0].pk == e2.pk  # Earlier timestamp first

    def test_progress_fields_defaults(self):
        run = _create_run()
        fresh = BacktestRun.objects.get(pk=run.pk)
        assert fresh.progress_pct == 0
        assert fresh.progress_message == ""
        assert fresh.total_bars == 0

    def test_status_choices(self):
        assert BacktestRun.STATUS_RUNNING == "running"
        assert BacktestRun.STATUS_COMPLETED == "completed"
        assert BacktestRun.STATUS_FAILED == "failed"

    def test_default_status_is_completed(self):
        run = _create_run()
        fresh = BacktestRun.objects.get(pk=run.pk)
        assert fresh.status == "completed"

    def test_win_rate_pct_property(self):
        run = _create_run(win_rate=0.65)
        assert run.win_rate_pct == pytest.approx(65.0)

    def test_win_rate_pct_zero(self):
        run = _create_run(win_rate=0.0)
        assert run.win_rate_pct == pytest.approx(0.0)

    def test_cascade_delete(self):
        run = _create_run()
        _create_trade(run)
        _create_equity(run)
        run.delete()
        assert Trade.objects.count() == 0
        assert EquitySnapshot.objects.count() == 0


class RunBacktestWebCommandTests(TestCase):
    @patch("pyfx.backtest.runner.run_backtest")
    @patch("pandas.read_parquet")
    def test_successful_run(self, mock_read_parquet, mock_run_bt):
        from pyfx.core.types import BacktestResult

        run = _create_run(
            status=BacktestRun.STATUS_RUNNING,
            data_file="/tmp/test.parquet",
        )

        mock_df = MagicMock()
        mock_df.index.tz = "UTC"
        mock_df.empty = False
        mock_df.__len__ = MagicMock(return_value=1000)
        mock_df.loc.__getitem__ = MagicMock(return_value=mock_df)
        mock_read_parquet.return_value = mock_df

        mock_result = MagicMock(spec=BacktestResult)
        mock_result.total_pnl = 200.0
        mock_result.total_return_pct = 0.2
        mock_result.num_trades = 3
        mock_result.win_rate = 0.67
        mock_result.max_drawdown_pct = -1.5
        mock_result.avg_trade_pnl = 66.67
        mock_result.avg_win = 100.0
        mock_result.avg_loss = -50.0
        mock_result.profit_factor = 2.0
        mock_result.duration_seconds = 2.5
        mock_result.trades = []
        mock_result.equity_curve = []
        mock_run_bt.return_value = mock_result

        from django.core.management import call_command
        with patch("pathlib.Path.exists", return_value=True):
            call_command("run_backtest_web", run_id=run.pk)

        run.refresh_from_db()
        assert run.status == BacktestRun.STATUS_COMPLETED
        assert run.total_pnl == 200.0
        assert run.progress_pct == 100
        assert run.progress_message == "Complete"
        assert run.total_bars == 1000

    def test_missing_run_id(self):
        from io import StringIO

        from django.core.management import call_command

        err = StringIO()
        call_command("run_backtest_web", run_id=99999, stderr=err)
        assert "not found" in err.getvalue()

    @patch("pyfx.backtest.runner.run_backtest")
    @patch("pandas.read_parquet")
    def test_failed_run(self, mock_read_parquet, mock_run_bt):
        run = _create_run(
            status=BacktestRun.STATUS_RUNNING,
            data_file="/tmp/test.parquet",
        )

        mock_run_bt.side_effect = RuntimeError("Engine crash")
        mock_df = MagicMock()
        mock_df.index.tz = "UTC"
        mock_df.empty = False
        mock_df.__len__ = MagicMock(return_value=500)
        mock_df.loc.__getitem__ = MagicMock(return_value=mock_df)
        mock_read_parquet.return_value = mock_df

        from django.core.management import call_command
        with patch("pathlib.Path.exists", return_value=True):
            call_command("run_backtest_web", run_id=run.pk)

        run.refresh_from_db()
        assert run.status == BacktestRun.STATUS_FAILED
        assert "Engine crash" in run.error_message
        assert run.progress_message == "Failed"

    def test_missing_data_file(self):
        run = _create_run(
            status=BacktestRun.STATUS_RUNNING,
            data_file="/nonexistent/data.parquet",
        )

        from django.core.management import call_command
        call_command("run_backtest_web", run_id=run.pk)

        run.refresh_from_db()
        assert run.status == BacktestRun.STATUS_FAILED
        assert "not found" in run.error_message.lower()


class ToJsonSafeTests(TestCase):
    def test_decimal_to_float(self):
        from pyfx.web.dashboard.views import _to_json_safe

        assert _to_json_safe(Decimal("1.5")) == 1.5

    def test_passthrough(self):
        from pyfx.web.dashboard.views import _to_json_safe

        assert _to_json_safe(42) == 42
        assert _to_json_safe("hello") == "hello"


class RunBacktestWebCsvTests(TestCase):
    @patch("pyfx.backtest.runner.run_backtest")
    @patch("pandas.read_csv")
    def test_csv_data_file(self, mock_read_csv, mock_run_bt):
        from pyfx.core.types import BacktestResult

        run = _create_run(
            status=BacktestRun.STATUS_RUNNING,
            data_file="/tmp/test.csv",
        )

        mock_df = MagicMock()
        mock_df.index.tz = "UTC"
        mock_df.empty = False
        mock_df.__len__ = MagicMock(return_value=1000)
        mock_df.loc.__getitem__ = MagicMock(return_value=mock_df)
        mock_read_csv.return_value = mock_df

        mock_result = MagicMock(spec=BacktestResult)
        mock_result.total_pnl = 100.0
        mock_result.total_return_pct = 0.1
        mock_result.num_trades = 2
        mock_result.win_rate = 0.5
        mock_result.max_drawdown_pct = -1.0
        mock_result.avg_trade_pnl = 50.0
        mock_result.avg_win = 100.0
        mock_result.avg_loss = -50.0
        mock_result.profit_factor = 2.0
        mock_result.duration_seconds = 1.0
        mock_result.trades = []
        mock_result.equity_curve = []
        mock_run_bt.return_value = mock_result

        from django.core.management import call_command
        with patch("pathlib.Path.exists", return_value=True):
            call_command("run_backtest_web", run_id=run.pk)

        run.refresh_from_db()
        assert run.status == BacktestRun.STATUS_COMPLETED
        mock_read_csv.assert_called_once()

    @patch("pyfx.backtest.runner.run_backtest")
    @patch("pandas.read_parquet")
    def test_empty_data_range(self, mock_read_parquet, mock_run_bt):
        run = _create_run(
            status=BacktestRun.STATUS_RUNNING,
            data_file="/tmp/test.parquet",
        )

        mock_df = MagicMock()
        mock_df.index.tz = "UTC"
        empty_df = MagicMock()
        empty_df.empty = True
        mock_df.loc.__getitem__ = MagicMock(return_value=empty_df)
        mock_read_parquet.return_value = mock_df

        from django.core.management import call_command
        with patch("pathlib.Path.exists", return_value=True):
            call_command("run_backtest_web", run_id=run.pk)

        run.refresh_from_db()
        assert run.status == BacktestRun.STATUS_FAILED
        assert "no data" in run.error_message.lower()

    @patch("pyfx.backtest.runner.run_backtest")
    @patch("pandas.read_parquet")
    def test_naive_tz_localized(self, mock_read_parquet, mock_run_bt):
        run = _create_run(
            status=BacktestRun.STATUS_RUNNING,
            data_file="/tmp/test.parquet",
        )

        mock_df = MagicMock()
        mock_df.index.tz = None  # Naive timezone
        mock_localized = MagicMock()
        mock_localized.empty = False
        mock_localized.__len__ = MagicMock(return_value=1000)
        mock_localized.loc.__getitem__ = MagicMock(return_value=mock_localized)
        mock_df.index.tz_localize.return_value = mock_localized
        mock_df.loc.__getitem__ = MagicMock(return_value=mock_localized)
        mock_read_parquet.return_value = mock_df

        mock_result = MagicMock()
        mock_result.total_pnl = 50.0
        mock_result.total_return_pct = 0.05
        mock_result.num_trades = 1
        mock_result.win_rate = 1.0
        mock_result.max_drawdown_pct = 0.0
        mock_result.avg_trade_pnl = 50.0
        mock_result.avg_win = 50.0
        mock_result.avg_loss = 0.0
        mock_result.profit_factor = None
        mock_result.duration_seconds = 0.5
        mock_result.trades = []
        mock_result.equity_curve = []
        mock_run_bt.return_value = mock_result

        from django.core.management import call_command
        with patch("pathlib.Path.exists", return_value=True):
            call_command("run_backtest_web", run_id=run.pk)

        run.refresh_from_db()
        assert run.status == BacktestRun.STATUS_COMPLETED


class FindStrategyConfigTests(TestCase):
    def test_find_config_for_sample_sma(self):
        from pyfx.strategies.sample_sma import SMACrossStrategy
        from pyfx.web.dashboard.views import _find_strategy_config

        config_cls = _find_strategy_config(SMACrossStrategy)
        assert config_cls is not None
        assert "fast_period" in config_cls.__struct_fields__

    def test_find_config_returns_none_for_object(self):
        from pyfx.web.dashboard.views import _find_strategy_config

        result = _find_strategy_config(object)
        assert result is None


class CategorizeParamTests(TestCase):
    def test_indicators_category(self):
        from pyfx.web.dashboard.views import _categorize_param

        assert _categorize_param("entry_mode") == "indicators"
        assert _categorize_param("sma_fast_period") == "indicators"
        assert _categorize_param("rsi_period") == "indicators"
        assert _categorize_param("rsi_level_threshold") == "indicators"

    def test_exits_category(self):
        from pyfx.web.dashboard.views import _categorize_param

        assert _categorize_param("exit_mode") == "exits"
        assert _categorize_param("take_profit_pips") == "exits"
        assert _categorize_param("stop_loss_pips") == "exits"
        assert _categorize_param("trailing_stop_pips") == "exits"
        assert _categorize_param("atr_tp_multiplier") == "exits"
        assert _categorize_param("spread_pips") == "exits"
        assert _categorize_param("macd_reversal_exit") == "exits"
        assert _categorize_param("macd_reversal_bars") == "exits"

    def test_timing_category(self):
        from pyfx.web.dashboard.views import _categorize_param

        assert _categorize_param("session_start_hour") == "timing"
        assert _categorize_param("max_signal_window_seconds") == "timing"
        assert _categorize_param("double_confirm_enabled") == "timing"

    def test_advanced_category(self):
        from pyfx.web.dashboard.views import _categorize_param

        assert _categorize_param("rsi_buffer_size") == "advanced"
        assert _categorize_param("trade_size") == "advanced"


class BoolParamParsingTests(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("pyfx.web.dashboard.views.subprocess.Popen")
    def test_start_parses_bool_true(self, mock_popen):
        self.client.post("/backtests/start/", {
            "strategy": "sample_sma",
            "instrument": "EUR/USD",
            "start": "2024-01-01",
            "end": "2024-06-30",
            "data_file": "/tmp/test.parquet",
            "balance": "100000",
            "leverage": "50",
            "trade_size": "100000",
            "bar_type": "1-MINUTE-LAST-EXTERNAL",
            "param_macd_reversal_exit": "true",
        })
        run = BacktestRun.objects.latest("created_at")
        assert run.strategy_params["macd_reversal_exit"] is True

    @patch("pyfx.web.dashboard.views.subprocess.Popen")
    def test_start_parses_bool_false(self, mock_popen):
        self.client.post("/backtests/start/", {
            "strategy": "sample_sma",
            "instrument": "EUR/USD",
            "start": "2024-01-01",
            "end": "2024-06-30",
            "data_file": "/tmp/test.parquet",
            "balance": "100000",
            "leverage": "50",
            "trade_size": "100000",
            "bar_type": "1-MINUTE-LAST-EXTERNAL",
            "param_some_flag": "false",
        })
        run = BacktestRun.objects.latest("created_at")
        assert run.strategy_params["some_flag"] is False


class ApiStrategiesBoolAndCategoryTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_api_strategies_includes_category(self):
        resp = self.client.get("/api/strategies/")
        data = json.loads(resp.content)
        for strategy in data:
            for param in strategy["params"]:
                assert "category" in param, (
                    f"Missing category for {strategy['name']}.{param['name']}"
                )

    def test_api_strategies_bool_type(self):
        resp = self.client.get("/api/strategies/")
        data = json.loads(resp.content)
        names = [s["name"] for s in data]
        if "coban_reborn" in names:
            cr = next(s for s in data if s["name"] == "coban_reborn")
            bool_params = [p for p in cr["params"] if p["type"] == "bool"]
            bool_names = [p["name"] for p in bool_params]
            assert "macd_reversal_exit" in bool_names
            assert "double_confirm_enabled" in bool_names


# ── Dataset helpers ──


def _create_dataset(
    instrument: str = "EUR/USD",
    timeframe: str = "M1",
    status: str = Dataset.STATUS_READY,
    **kwargs,
) -> Dataset:
    from datetime import date

    defaults = dict(
        instrument=instrument,
        timeframe=timeframe,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        file_path=f"/tmp/{instrument.replace('/', '')}_{timeframe}.parquet",
        file_size_bytes=5_000_000,
        row_count=500_000,
        source=Dataset.SOURCE_MANUAL,
        status=status,
    )
    defaults.update(kwargs)
    return Dataset.objects.create(**defaults)


# ── Dataset model tests ──


class DatasetModelTests(TestCase):
    def test_str(self):
        ds = _create_dataset()
        assert "EUR/USD" in str(ds)
        assert "M1" in str(ds)
        assert "2025-01-01" in str(ds)

    def test_display_size_mb(self):
        ds = _create_dataset(file_size_bytes=5_000_000)
        assert ds.display_size == "5.0 MB"

    def test_display_size_kb(self):
        ds = _create_dataset(file_size_bytes=5_000)
        assert ds.display_size == "5.0 KB"

    def test_display_size_bytes(self):
        ds = _create_dataset(file_size_bytes=500)
        assert ds.display_size == "500 B"

    def test_ordering(self):
        from datetime import date

        _create_dataset(
            file_path="/tmp/a.parquet",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )
        d2 = _create_dataset(
            file_path="/tmp/b.parquet",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
        datasets = list(Dataset.objects.all())
        assert datasets[0].pk == d2.pk

    def test_unique_file_path(self):
        from django.db import IntegrityError

        _create_dataset(file_path="/tmp/unique.parquet")
        with pytest.raises(IntegrityError):
            _create_dataset(file_path="/tmp/unique.parquet")

    def test_unique_identity(self):
        from datetime import date

        from django.db import IntegrityError

        _create_dataset(
            instrument="GBP/USD",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            file_path="/tmp/gbp1.parquet",
        )
        with pytest.raises(IntegrityError):
            _create_dataset(
                instrument="GBP/USD",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
                file_path="/tmp/gbp2.parquet",
            )

    def test_status_choices(self):
        assert Dataset.STATUS_DOWNLOADING == "downloading"
        assert Dataset.STATUS_INGESTING == "ingesting"
        assert Dataset.STATUS_READY == "ready"
        assert Dataset.STATUS_ERROR == "error"

    def test_source_choices(self):
        assert Dataset.SOURCE_DUKASCOPY == "dukascopy"
        assert Dataset.SOURCE_MANUAL == "manual"
        assert Dataset.SOURCE_GENERATED == "generated"

    def test_default_status(self):
        ds = _create_dataset()
        fresh = Dataset.objects.get(pk=ds.pk)
        assert fresh.progress_pct == 0 or fresh.status == Dataset.STATUS_READY


# ── Dataset view tests ──


class DatasetListViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_list_empty(self):
        resp = self.client.get("/data/")
        assert resp.status_code == 200
        assert b"No datasets yet" in resp.content

    def test_list_with_datasets(self):
        _create_dataset()
        resp = self.client.get("/data/")
        assert resp.status_code == 200
        assert b"EUR/USD" in resp.content

    def test_list_active_nav(self):
        resp = self.client.get("/data/")
        assert resp.context["active_nav"] == "data"

    def test_list_downloading_count(self):
        _create_dataset(
            instrument="GBP/USD",
            status=Dataset.STATUS_DOWNLOADING,
            file_path="/tmp/dl.parquet",
        )
        resp = self.client.get("/data/")
        assert resp.context["downloading_count"] == 1

    def test_list_contains_download_button(self):
        resp = self.client.get("/data/")
        assert b"Download Data" in resp.content


class DatasetNewViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_new_page_200(self):
        resp = self.client.get("/data/new/")
        assert resp.status_code == 200
        assert b"Download Data" in resp.content

    def test_new_page_active_nav(self):
        resp = self.client.get("/data/new/")
        assert resp.context["active_nav"] == "data"

    def test_new_page_has_instruments(self):
        resp = self.client.get("/data/new/")
        assert b"EUR/USD" in resp.content
        assert b"XAU/USD" in resp.content


class DatasetStartViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_start_get_redirects(self):
        resp = self.client.get("/data/start/")
        assert resp.status_code == 302

    @patch("pyfx.web.dashboard.views.subprocess.Popen")
    def test_start_post_creates_dataset(self, mock_popen):
        resp = self.client.post("/data/start/", {
            "instrument": "EUR/USD",
            "start": "2025-01-01",
            "end": "2025-12-31",
            "timeframe": "M1",
        })
        assert resp.status_code == 302
        ds = Dataset.objects.latest("created_at")
        assert ds.instrument == "EUR/USD"
        assert ds.status == Dataset.STATUS_DOWNLOADING
        assert ds.source == Dataset.SOURCE_DUKASCOPY
        mock_popen.assert_called_once()


class DatasetDeleteViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("pyfx.web.dashboard.views.Path.unlink")
    def test_delete_post(self, mock_unlink):
        ds = _create_dataset()
        resp = self.client.post(f"/api/data/{ds.pk}/delete/")
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert data["ok"] is True
        assert not Dataset.objects.filter(pk=ds.pk).exists()

    def test_delete_get_rejected(self):
        ds = _create_dataset()
        resp = self.client.get(f"/api/data/{ds.pk}/delete/")
        assert resp.status_code == 405

    def test_delete_404(self):
        resp = self.client.post("/api/data/99999/delete/")
        assert resp.status_code == 404


class DatasetRedownloadViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("pyfx.web.dashboard.views.subprocess.Popen")
    @patch("pyfx.web.dashboard.views.Path.unlink")
    def test_redownload(self, mock_unlink, mock_popen):
        ds = _create_dataset(status=Dataset.STATUS_READY)
        resp = self.client.post(f"/api/data/{ds.pk}/redownload/")
        assert resp.status_code == 200
        ds.refresh_from_db()
        assert ds.status == Dataset.STATUS_DOWNLOADING
        mock_popen.assert_called_once()

    @patch("pyfx.web.dashboard.views.subprocess.Popen")
    def test_redownload_already_downloading(self, mock_popen):
        ds = _create_dataset(
            status=Dataset.STATUS_DOWNLOADING,
            file_path="/tmp/dl2.parquet",
        )
        resp = self.client.post(f"/api/data/{ds.pk}/redownload/")
        assert resp.status_code == 409
        mock_popen.assert_not_called()

    def test_redownload_get_rejected(self):
        ds = _create_dataset()
        resp = self.client.get(f"/api/data/{ds.pk}/redownload/")
        assert resp.status_code == 405


class ApiDatasetsViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_empty(self):
        resp = self.client.get("/api/data/")
        data = json.loads(resp.content)
        assert data == []

    def test_returns_ready_only(self):
        _create_dataset(status=Dataset.STATUS_READY)
        _create_dataset(
            instrument="GBP/USD",
            status=Dataset.STATUS_DOWNLOADING,
            file_path="/tmp/dl3.parquet",
        )
        resp = self.client.get("/api/data/")
        data = json.loads(resp.content)
        assert len(data) == 1

    def test_response_fields(self):
        _create_dataset()
        resp = self.client.get("/api/data/")
        data = json.loads(resp.content)
        ds = data[0]
        assert "id" in ds
        assert ds["instrument"] == "EUR/USD"
        assert ds["timeframe"] == "M1"
        assert "file_path" in ds
        assert "row_count" in ds
        assert "display_size" in ds


class ApiDatasetStatusViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_status(self):
        ds = _create_dataset(
            status=Dataset.STATUS_DOWNLOADING,
            file_path="/tmp/ds_status.parquet",
        )
        ds.progress_pct = 50
        ds.progress_message = "Downloading..."
        ds.save()
        resp = self.client.get(f"/api/data/{ds.pk}/status/")
        data = json.loads(resp.content)
        assert data["status"] == "downloading"
        assert data["progress_pct"] == 50
        assert data["progress_message"] == "Downloading..."

    def test_status_404(self):
        resp = self.client.get("/api/data/99999/status/")
        assert resp.status_code == 404


class ApiRunningDownloadsViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_no_running(self):
        resp = self.client.get("/api/data/running/")
        data = json.loads(resp.content)
        assert data == []

    def test_returns_downloading_only(self):
        _create_dataset(
            instrument="GBP/USD",
            status=Dataset.STATUS_DOWNLOADING,
            file_path="/tmp/run1.parquet",
        )
        _create_dataset(
            instrument="USD/JPY",
            status=Dataset.STATUS_INGESTING,
            file_path="/tmp/run2.parquet",
        )
        _create_dataset(status=Dataset.STATUS_READY)
        resp = self.client.get("/api/data/running/")
        data = json.loads(resp.content)
        assert len(data) == 2

    def test_response_fields(self):
        _create_dataset(
            instrument="XAU/USD",
            status=Dataset.STATUS_DOWNLOADING,
            file_path="/tmp/run3.parquet",
        )
        resp = self.client.get("/api/data/running/")
        data = json.loads(resp.content)
        ds = data[0]
        assert "id" in ds
        assert "instrument" in ds
        assert "progress_pct" in ds
        assert "progress_message" in ds


class MonthRangesTests(TestCase):
    def test_single_month(self):
        from datetime import date

        from pyfx.web.dashboard.management.commands.run_download_web import (
            _month_ranges,
        )

        chunks = _month_ranges(date(2025, 3, 1), date(2025, 3, 31))
        assert len(chunks) == 1
        assert chunks[0] == (date(2025, 3, 1), date(2025, 3, 31))

    def test_multi_month(self):
        from datetime import date

        from pyfx.web.dashboard.management.commands.run_download_web import (
            _month_ranges,
        )

        chunks = _month_ranges(date(2025, 1, 15), date(2025, 3, 10))
        assert len(chunks) == 3
        assert chunks[0] == (date(2025, 1, 15), date(2025, 1, 31))
        assert chunks[1] == (date(2025, 2, 1), date(2025, 2, 28))
        assert chunks[2] == (date(2025, 3, 1), date(2025, 3, 10))

    def test_cross_year(self):
        from datetime import date

        from pyfx.web.dashboard.management.commands.run_download_web import (
            _month_ranges,
        )

        chunks = _month_ranges(date(2025, 12, 1), date(2026, 1, 31))
        assert len(chunks) == 2
        assert chunks[0] == (date(2025, 12, 1), date(2025, 12, 31))
        assert chunks[1] == (date(2026, 1, 1), date(2026, 1, 31))


class RunDownloadWebCommandTests(TestCase):
    def test_missing_dataset_id(self):
        from io import StringIO

        from django.core.management import call_command

        err = StringIO()
        call_command("run_download_web", dataset_id=99999, stderr=err)
        assert "not found" in err.getvalue()

    @patch("pyfx.web.dashboard.management.commands.run_download_web.CHUNK_DELAY_SECONDS", 0)
    @patch("pyfx.web.dashboard.management.commands.run_download_web.subprocess.run")
    @patch("pyfx.web.dashboard.management.commands.run_download_web.shutil.which")
    @patch("pyfx.web.dashboard.management.commands.run_download_web.tempfile.mkdtemp")
    def test_successful_download(
        self, mock_mkdtemp, mock_which, mock_subprocess_run, tmp_path=None,
    ):
        import tempfile


        # Set up a real temp dir so rglob finds CSVs
        real_tmp = Path(tempfile.mkdtemp())
        mock_mkdtemp.return_value = str(real_tmp)
        mock_which.return_value = "/usr/local/bin/npx"

        ds = _create_dataset(
            instrument="GBP/USD",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 2, 28),
            status=Dataset.STATUS_DOWNLOADING,
            file_path=str(real_tmp / "test_dl.parquet"),
        )

        # Create fake CSV files that subprocess.run would produce
        def create_chunk_csv(*args, **kwargs):
            cmd = args[0]
            chunk_dir = Path(cmd[-1])  # -dir argument
            chunk_dir.mkdir(parents=True, exist_ok=True)
            csv = chunk_dir / "data.csv"
            csv.write_text(
                "timestamp,open,high,low,close,volume\n"
                "1735689600000,1.25,1.26,1.24,1.255,100\n"
                "1735689660000,1.255,1.26,1.25,1.258,200\n"
            )

        mock_subprocess_run.side_effect = create_chunk_csv

        from django.core.management import call_command

        call_command("run_download_web", dataset_id=ds.pk)

        ds.refresh_from_db()
        assert ds.status == Dataset.STATUS_READY
        assert ds.progress_pct == 100
        assert ds.row_count == 2  # Deduped across 2 identical chunks
        # Temp dir should be cleaned up
        assert not real_tmp.exists()

    @patch("pyfx.web.dashboard.management.commands.run_download_web.shutil.which")
    def test_npx_not_found(self, mock_which):
        mock_which.return_value = None
        ds = _create_dataset(
            status=Dataset.STATUS_DOWNLOADING,
            file_path="/tmp/no_npx.parquet",
        )

        from django.core.management import call_command

        call_command("run_download_web", dataset_id=ds.pk)

        ds.refresh_from_db()
        assert ds.status == Dataset.STATUS_ERROR
        assert "npx not found" in ds.error_message

    @patch("pyfx.web.dashboard.management.commands.run_download_web.CHUNK_DELAY_SECONDS", 0)
    @patch("pyfx.web.dashboard.management.commands.run_download_web.RETRY_DELAY_SECONDS", 0)
    @patch("pyfx.web.dashboard.management.commands.run_download_web.subprocess.run")
    @patch("pyfx.web.dashboard.management.commands.run_download_web.shutil.which")
    @patch("pyfx.web.dashboard.management.commands.run_download_web.tempfile.mkdtemp")
    def test_download_failure_after_retries(
        self, mock_mkdtemp, mock_which, mock_subprocess_run,
    ):
        import subprocess as sp
        import tempfile

        real_tmp = Path(tempfile.mkdtemp())
        mock_mkdtemp.return_value = str(real_tmp)
        mock_which.return_value = "/usr/local/bin/npx"
        mock_subprocess_run.side_effect = sp.CalledProcessError(1, "npx")

        ds = _create_dataset(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            status=Dataset.STATUS_DOWNLOADING,
            file_path="/tmp/fail_dl.parquet",
        )

        from django.core.management import call_command

        call_command("run_download_web", dataset_id=ds.pk)

        ds.refresh_from_db()
        assert ds.status == Dataset.STATUS_ERROR
        assert ds.progress_message == "Failed"
        # Should have retried (MAX_RETRIES=2 calls per chunk)
        assert mock_subprocess_run.call_count == 2

    @patch("pyfx.web.dashboard.management.commands.run_download_web.CHUNK_DELAY_SECONDS", 0)
    @patch("pyfx.web.dashboard.management.commands.run_download_web.subprocess.run")
    @patch("pyfx.web.dashboard.management.commands.run_download_web.shutil.which")
    @patch("pyfx.web.dashboard.management.commands.run_download_web.tempfile.mkdtemp")
    def test_no_csv_found(
        self, mock_mkdtemp, mock_which, mock_subprocess_run,
    ):
        import tempfile

        real_tmp = Path(tempfile.mkdtemp())
        mock_mkdtemp.return_value = str(real_tmp)
        mock_which.return_value = "/usr/local/bin/npx"
        # subprocess succeeds but produces no CSV files

        ds = _create_dataset(
            instrument="AUD/USD",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            status=Dataset.STATUS_DOWNLOADING,
            file_path="/tmp/nocsv.parquet",
        )

        from django.core.management import call_command

        call_command("run_download_web", dataset_id=ds.pk)

        ds.refresh_from_db()
        assert ds.status == Dataset.STATUS_ERROR
        assert "No CSV files found" in ds.error_message

    @patch("pyfx.web.dashboard.management.commands.run_download_web.shutil.which")
    def test_unknown_instrument(self, mock_which):
        mock_which.return_value = "/usr/local/bin/npx"
        ds = _create_dataset(
            instrument="UNKNOWN/XXX",
            status=Dataset.STATUS_DOWNLOADING,
            file_path="/tmp/unknown.parquet",
        )

        from django.core.management import call_command

        call_command("run_download_web", dataset_id=ds.pk)

        ds.refresh_from_db()
        assert ds.status == Dataset.STATUS_ERROR
        assert "Unknown instrument" in ds.error_message


class CliWebCommandTests(TestCase):
    """Tests for `pyfx web` and `pyfx manage` CLI commands."""

    @patch("pyfx.cli.click.echo")
    @patch("pyfx.data.scanner.scan_data_directory", return_value=(0, 0))
    @patch("django.core.management.call_command")
    @patch("django.core.management.execute_from_command_line")
    def test_web_default_enables_reload(
        self, mock_exec: MagicMock, _cc: MagicMock, _scan: MagicMock, _echo: MagicMock,
    ) -> None:
        from click.testing import CliRunner

        from pyfx.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["web"])

        assert result.exit_code == 0
        mock_exec.assert_called_once()
        argv = mock_exec.call_args[0][0]
        assert "--noreload" not in argv
        assert "127.0.0.1:8000" in argv[2]

    @patch("pyfx.cli.click.echo")
    @patch("pyfx.data.scanner.scan_data_directory", return_value=(0, 0))
    @patch("django.core.management.call_command")
    @patch("django.core.management.execute_from_command_line")
    def test_web_no_reload_flag(
        self, mock_exec: MagicMock, _cc: MagicMock, _scan: MagicMock, _echo: MagicMock,
    ) -> None:
        from click.testing import CliRunner

        from pyfx.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["web", "--no-reload"])

        assert result.exit_code == 0
        argv = mock_exec.call_args[0][0]
        assert "--noreload" in argv

    @patch("pyfx.data.scanner.scan_data_directory", return_value=(3, 2))
    @patch("django.core.management.call_command")
    @patch("django.core.management.execute_from_command_line")
    def test_web_shows_registered_datasets(
        self, _exec: MagicMock, _cc: MagicMock, _scan: MagicMock,
    ) -> None:
        from click.testing import CliRunner

        from pyfx.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["web"])

        assert result.exit_code == 0
        assert "Registered 3 new dataset(s)" in result.output

    @patch("django.core.management.execute_from_command_line")
    def test_manage_passes_args(self, mock_exec: MagicMock) -> None:
        from click.testing import CliRunner

        from pyfx.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["manage", "migrate", "--run-syncdb"])

        assert result.exit_code == 0
        mock_exec.assert_called_once_with(["pyfx", "migrate", "--run-syncdb"])

    @patch("django.core.management.execute_from_command_line")
    def test_manage_no_args(self, mock_exec: MagicMock) -> None:
        from click.testing import CliRunner

        from pyfx.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["manage"])

        assert result.exit_code == 0
        mock_exec.assert_called_once_with(["pyfx"])
