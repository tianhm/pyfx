"""Tests for the Django dashboard views, APIs, and management commands."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client, TestCase

from pyfx.web.dashboard.models import (
    BacktestRun,
    Dataset,
    EquitySnapshot,
    PaperTrade,
    PaperTradingSession,
    RiskSnapshot,
    SessionEvent,
    Trade,
)


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
        resp = self.client.get(f"/api/run/{self.run.pk}/cumulative-pnl/")
        pnl = resp.json()
        assert len(pnl) == 2
        assert pnl[0]["value"] == 100.0
        assert pnl[1]["value"] == 50.0
        assert "time" in pnl[0]
        assert isinstance(pnl[0]["time"], int)

    def test_detail_trade_markers_json(self):
        resp = self.client.get(f"/api/run/{self.run.pk}/trade-markers/")
        markers = resp.json()
        # 2 trades × 2 markers (entry + exit) = 4
        assert len(markers) == 4
        assert markers[0]["type"] == "entry"
        assert markers[1]["type"] == "exit"
        assert "time" in markers[0]
        assert "side" in markers[0]
        assert "price" in markers[0]
        # tradeIdx links markers to trade table rows
        assert markers[0]["tradeIdx"] == 0
        assert markers[1]["tradeIdx"] == 0
        assert markers[2]["tradeIdx"] == 1
        assert markers[3]["tradeIdx"] == 1

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

    def test_detail_zero_avg_loss(self) -> None:
        """Branch: avg_loss=0 -> win_loss_ratio stays None."""
        run = _create_run(avg_loss=0.0, num_trades=3, win_rate=1.0)
        resp = self.client.get(f"/run/{run.pk}/")
        assert resp.context["win_loss_ratio"] is None

    def test_detail_extra_bar_types_with_duplicate(self) -> None:
        """Branch: extra_bar_types contains 1-MINUTE (already in list)."""
        run = _create_run(extra_bar_types=["1-MINUTE-LAST-EXTERNAL", "5-MINUTE-LAST-EXTERNAL"])
        resp = self.client.get(f"/run/{run.pk}/")
        tfs = json.loads(resp.context["available_timeframes_json"])
        assert tfs.count("1-MINUTE-LAST-EXTERNAL") == 1  # no duplicate

    def test_detail_strategy_without_chart_indicators(self) -> None:
        """Branch: strategy exists but has no chart_indicators method."""
        run = _create_run(strategy="coban_experimental")
        resp = self.client.get(f"/run/{run.pk}/")
        inds = json.loads(resp.context["chart_indicators_json"])
        assert inds == []


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

    @patch("pyfx.web.dashboard.views.subprocess.Popen")
    def test_start_rejects_path_traversal(self, mock_popen):
        resp = self.client.post("/backtests/start/", {
            "strategy": "sample_sma",
            "instrument": "EUR/USD",
            "start": "2024-01-01",
            "end": "2024-06-30",
            "data_file": "../../etc/passwd",
            "balance": "100000",
            "leverage": "50",
            "trade_size": "100000",
            "bar_type": "1-MINUTE-LAST-EXTERNAL",
        })
        assert resp.status_code == 400
        mock_popen.assert_not_called()


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


class ApiChartDataViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.run = _create_run()

    @patch("pyfx.data.resample.compute_indicator")
    @patch("pyfx.data.resample.load_bars")
    def test_chart_data_bars_and_indicators(
        self, mock_load: MagicMock, mock_compute: MagicMock,
    ) -> None:
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
        mock_compute.return_value = pd.Series([1.15, 1.25], index=df.index)
        resp = self.client.get(
            f"/api/run/{self.run.pk}/chart-data/"
            "?timeframe=5-MINUTE-LAST-EXTERNAL&indicators=sma:20"
        )
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert "bars" in data
        assert "indicators" in data
        assert len(data["bars"]) == 2
        assert "sma_20" in data["indicators"]
        assert len(data["indicators"]["sma_20"]) == 2

    @patch("pyfx.data.resample.compute_indicator")
    @patch("pyfx.data.resample.load_bars")
    def test_chart_data_macd_indicator(
        self, mock_load: MagicMock, mock_compute: MagicMock,
    ) -> None:
        import pandas as pd

        df = pd.DataFrame(
            {
                "open": [1.1, 1.2, 1.3],
                "high": [1.15, 1.25, 1.35],
                "low": [1.05, 1.15, 1.25],
                "close": [1.12, 1.22, 1.32],
            },
            index=pd.date_range("2024-01-02", periods=3, freq="min", tz="UTC"),
        )
        mock_load.return_value = df
        mock_compute.return_value = {
            "macd": pd.Series([0.01, 0.02, 0.03], index=df.index),
            "signal": pd.Series([0.005, 0.015, 0.025], index=df.index),
            "histogram": pd.Series([0.005, 0.005, 0.005], index=df.index),
        }
        resp = self.client.get(
            f"/api/run/{self.run.pk}/chart-data/?indicators=macd:12"
        )
        assert resp.status_code == 200
        data = json.loads(resp.content)
        macd_data = data["indicators"]["macd_12"]
        assert "macd" in macd_data
        assert "signal" in macd_data
        assert "histogram" in macd_data

    @patch("pyfx.data.resample.load_bars")
    def test_chart_data_bars_only(self, mock_load: MagicMock) -> None:
        import pandas as pd

        df = pd.DataFrame(
            {"open": [1.1], "high": [1.2], "low": [1.0], "close": [1.15]},
            index=pd.date_range("2024-01-02", periods=1, freq="min", tz="UTC"),
        )
        mock_load.return_value = df
        resp = self.client.get(f"/api/run/{self.run.pk}/chart-data/")
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert len(data["bars"]) == 1
        assert data["indicators"] == {}

    def test_chart_data_invalid_indicator_format(self) -> None:
        resp = self.client.get(
            f"/api/run/{self.run.pk}/chart-data/?indicators=sma"
        )
        assert resp.status_code == 400

    def test_chart_data_invalid_indicator_name(self) -> None:
        resp = self.client.get(
            f"/api/run/{self.run.pk}/chart-data/?indicators=bollinger:20"
        )
        assert resp.status_code == 400

    def test_chart_data_invalid_period(self) -> None:
        resp = self.client.get(
            f"/api/run/{self.run.pk}/chart-data/?indicators=sma:abc"
        )
        assert resp.status_code == 400

    def test_chart_data_period_out_of_range(self) -> None:
        resp = self.client.get(
            f"/api/run/{self.run.pk}/chart-data/?indicators=sma:0"
        )
        assert resp.status_code == 400

    def test_chart_data_invalid_timeframe(self) -> None:
        resp = self.client.get(
            f"/api/run/{self.run.pk}/chart-data/?timeframe=bad"
        )
        assert resp.status_code == 400

    @patch("pyfx.data.resample.load_bars")
    def test_chart_data_file_not_found(self, mock_load: MagicMock) -> None:
        mock_load.side_effect = FileNotFoundError("not found")
        resp = self.client.get(f"/api/run/{self.run.pk}/chart-data/")
        assert resp.status_code == 404

    @patch("pyfx.data.resample.load_bars")
    def test_chart_data_auto_downsample(self, mock_load: MagicMock) -> None:
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
        resp = self.client.get(f"/api/run/{self.run.pk}/chart-data/")
        assert resp.status_code == 200
        assert mock_load.call_count == 2

    @patch("pyfx.data.resample.load_bars")
    def test_chart_data_hard_cap(self, mock_load: MagicMock) -> None:
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
            f"/api/run/{self.run.pk}/chart-data/"
            "?timeframe=1-MINUTE-LAST-EXTERNAL"
        )
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert len(data["bars"]) == 10000

    def test_chart_data_404_run(self) -> None:
        resp = self.client.get("/api/run/99999/chart-data/")
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
        from pyfx.core.types import BacktestConfig, BacktestResult

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
        mock_result.config = BacktestConfig(
            strategy=run.strategy, instrument=run.instrument,
            start=run.start, end=run.end,
        )
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
        from pyfx.core.types import BacktestConfig, BacktestResult

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
        mock_result.config = BacktestConfig(
            strategy=run.strategy, instrument=run.instrument,
            start=run.start, end=run.end,
        )
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
        from pyfx.strategies.loader import find_strategy_config_class
        from pyfx.strategies.sample_sma import SMACrossStrategy

        config_cls = find_strategy_config_class(SMACrossStrategy)
        assert config_cls is not None
        assert "fast_period" in config_cls.__struct_fields__

    def test_find_config_returns_none_for_object(self):
        from pyfx.strategies.loader import find_strategy_config_class

        result = find_strategy_config_class(object)
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


# ---------------------------------------------------------------------------
# Paper Trading View Helpers
# ---------------------------------------------------------------------------


def _create_session(**kwargs: object) -> PaperTradingSession:
    defaults: dict[str, object] = {
        "status": PaperTradingSession.STATUS_STOPPED,
        "strategy": "coban_reborn",
        "instrument": "XAU/USD",
        "bar_type": "1-MINUTE-LAST-EXTERNAL",
        "started_at": datetime(2024, 1, 1, tzinfo=UTC),
        "account_currency": "USD",
        "account_id": "DU1234567",
    }
    defaults.update(kwargs)
    return PaperTradingSession.objects.create(**defaults)


def _create_paper_trade(
    session: PaperTradingSession,
    pnl: float | None = 50.0,
    closed: bool = True,
) -> PaperTrade:
    trade_kwargs: dict[str, object] = {
        "session": session,
        "instrument": "XAU/USD",
        "side": "BUY",
        "quantity": 100.0,
        "open_price": 2050.50,
        "opened_at": datetime(2024, 1, 2, tzinfo=UTC),
        "fill_latency_ms": 15.0,
        "slippage_ticks": 0.3,
    }
    if closed:
        trade_kwargs.update({
            "close_price": 2055.50,
            "realized_pnl": pnl,
            "realized_return_pct": 0.05 if pnl and pnl > 0 else -0.05,
            "closed_at": datetime(2024, 1, 2, 1, 0, tzinfo=UTC),
            "duration_seconds": 3600.0,
        })
    return PaperTrade.objects.create(**trade_kwargs)


def _create_session_event(
    session: PaperTradingSession,
    event_type: str = "info",
    message: str = "test event",
) -> SessionEvent:
    return SessionEvent.objects.create(
        session=session,
        timestamp=datetime(2024, 1, 2, tzinfo=UTC),
        event_type=event_type,
        message=message,
    )


def _create_risk_snapshot(
    session: PaperTradingSession,
    equity: float = 100000.0,
) -> RiskSnapshot:
    return RiskSnapshot.objects.create(
        session=session,
        timestamp=datetime(2024, 1, 2, tzinfo=UTC),
        equity=equity,
        daily_pnl=0.0,
        open_positions=0,
        drawdown_pct=0.0,
        utilization_pct=0.0,
    )


# ---------------------------------------------------------------------------
# Paper Trading View Tests
# ---------------------------------------------------------------------------


class PaperListViewTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_paper_list_empty(self) -> None:
        resp = self.client.get("/paper/")
        assert resp.status_code == 200
        assert resp.context["active_nav"] == "paper"

    def test_paper_list_with_sessions(self) -> None:
        _create_session(strategy="coban_reborn")
        _create_session(strategy="sample_sma", instrument="EUR/USD")
        resp = self.client.get("/paper/")
        assert resp.status_code == 200
        assert len(resp.context["sessions"]) == 2

    def test_paper_list_running_sessions(self) -> None:
        _create_session(status=PaperTradingSession.STATUS_RUNNING)
        _create_session(status=PaperTradingSession.STATUS_STOPPED)
        resp = self.client.get("/paper/")
        assert len(resp.context["running_sessions"]) == 1


class PaperDetailViewTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.session = _create_session()
        _create_paper_trade(self.session, pnl=100.0, closed=True)
        _create_paper_trade(self.session, pnl=None, closed=False)
        _create_session_event(self.session)

    def test_paper_detail_200(self) -> None:
        resp = self.client.get(f"/paper/{self.session.pk}/")
        assert resp.status_code == 200
        assert resp.context["active_nav"] == "paper"

    def test_paper_detail_context(self) -> None:
        resp = self.client.get(f"/paper/{self.session.pk}/")
        assert resp.context["session"] == self.session
        assert len(resp.context["trades"]) == 2
        assert len(resp.context["open_trades"]) == 1
        assert len(resp.context["closed_trades"]) == 1
        assert len(resp.context["events"]) == 1

    def test_paper_detail_matching_backtests(self) -> None:
        bt = _create_run(
            strategy="coban_reborn",
            instrument="XAU/USD",
            status=BacktestRun.STATUS_COMPLETED,
        )
        resp = self.client.get(f"/paper/{self.session.pk}/")
        matching = resp.context["matching_backtests"]
        assert len(matching) == 1
        assert matching[0].pk == bt.pk

    def test_paper_detail_404(self) -> None:
        resp = self.client.get("/paper/99999/")
        assert resp.status_code == 404


class PaperDeleteViewTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_delete_post(self) -> None:
        session = _create_session()
        resp = self.client.post(f"/paper/{session.pk}/delete/")
        assert resp.status_code == 302
        assert not PaperTradingSession.objects.filter(pk=session.pk).exists()

    def test_delete_get_returns_405(self) -> None:
        session = _create_session()
        resp = self.client.get(f"/paper/{session.pk}/delete/")
        assert resp.status_code == 405

    def test_delete_404(self) -> None:
        resp = self.client.post("/paper/99999/delete/")
        assert resp.status_code == 404

    def test_delete_redirects_to_list(self) -> None:
        session = _create_session()
        resp = self.client.post(f"/paper/{session.pk}/delete/")
        assert resp.status_code == 302
        assert resp.url == "/paper/"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Comparison View Tests
# ---------------------------------------------------------------------------


class ComparisonViewTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.session = _create_session()
        self.bt_run = _create_run(
            strategy="coban_reborn",
            instrument="XAU/USD",
        )

    @patch("pyfx.analysis.comparison.compare_sessions")
    def test_comparison_view_200(self, mock_compare: MagicMock) -> None:
        from pyfx.analysis.comparison import ComparisonReport

        mock_compare.return_value = ComparisonReport(
            paper_session_id=self.session.pk,
            backtest_run_id=self.bt_run.pk,
            total_pnl_paper=500.0,
            total_pnl_backtest=600.0,
            pnl_difference=-100.0,
            trades_paper=10,
            trades_backtest=12,
            win_rate_paper=0.6,
            win_rate_backtest=0.65,
        )
        resp = self.client.get(
            f"/paper/{self.session.pk}/compare/{self.bt_run.pk}/",
        )
        assert resp.status_code == 200
        assert resp.context["active_nav"] == "paper"
        assert resp.context["session"] == self.session
        assert resp.context["bt_run"] == self.bt_run
        assert resp.context["report"].total_pnl_paper == 500.0
        mock_compare.assert_called_once_with(
            session_id=self.session.pk,
            backtest_id=self.bt_run.pk,
        )

    def test_comparison_view_404_session(self) -> None:
        resp = self.client.get(
            f"/paper/99999/compare/{self.bt_run.pk}/",
        )
        assert resp.status_code == 404

    def test_comparison_view_404_backtest(self) -> None:
        resp = self.client.get(
            f"/paper/{self.session.pk}/compare/99999/",
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Risk Dashboard View Tests
# ---------------------------------------------------------------------------


class RiskDashboardViewTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_risk_dashboard_no_sessions(self) -> None:
        resp = self.client.get("/risk/")
        assert resp.status_code == 200
        assert resp.context["active_nav"] == "risk"
        assert resp.context["session"] is None
        assert resp.context["snapshots"] == []
        assert resp.context["risk_events"] == []
        assert resp.context["open_trades"] == []

    def test_risk_dashboard_with_running_session(self) -> None:
        session = _create_session(status=PaperTradingSession.STATUS_RUNNING)
        _create_risk_snapshot(session, equity=101000.0)
        _create_paper_trade(session, closed=False)
        _create_session_event(session, event_type="risk_warning", message="High DD")

        resp = self.client.get("/risk/")
        assert resp.status_code == 200
        assert resp.context["session"] == session
        assert len(resp.context["snapshots"]) == 1
        assert len(resp.context["risk_events"]) == 1
        assert len(resp.context["open_trades"]) == 1

    def test_risk_dashboard_prefers_running(self) -> None:
        _create_session(
            status=PaperTradingSession.STATUS_STOPPED,
            started_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        running = _create_session(
            status=PaperTradingSession.STATUS_RUNNING,
            started_at=datetime(2024, 1, 2, tzinfo=UTC),
        )
        resp = self.client.get("/risk/")
        assert resp.context["session"] == running

    def test_risk_dashboard_falls_back_to_latest(self) -> None:
        _create_session(
            status=PaperTradingSession.STATUS_STOPPED,
            started_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        new = _create_session(
            status=PaperTradingSession.STATUS_STOPPED,
            started_at=datetime(2024, 6, 1, tzinfo=UTC),
        )
        resp = self.client.get("/risk/")
        assert resp.context["session"] == new

    def test_risk_dashboard_filters_risk_events_only(self) -> None:
        session = _create_session(status=PaperTradingSession.STATUS_RUNNING)
        _create_session_event(session, event_type="risk_warning", message="warn1")
        _create_session_event(session, event_type="circuit_breaker", message="cb1")
        _create_session_event(session, event_type="info", message="should not appear")
        _create_session_event(session, event_type="position_limit", message="pos1")

        resp = self.client.get("/risk/")
        events = resp.context["risk_events"]
        assert len(events) == 3
        event_types = {e.event_type for e in events}
        assert "info" not in event_types


# ---------------------------------------------------------------------------
# Paper Trading API Tests
# ---------------------------------------------------------------------------


class ApiPaperTradesTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.session = _create_session()

    def test_paper_trades_empty(self) -> None:
        resp = self.client.get(f"/api/paper/{self.session.pk}/trades/")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_paper_trades_with_data(self) -> None:
        _create_paper_trade(self.session, pnl=100.0, closed=True)
        _create_paper_trade(self.session, pnl=None, closed=False)
        resp = self.client.get(f"/api/paper/{self.session.pk}/trades/")
        data = resp.json()
        assert len(data) == 2

        # Check closed trade
        closed = [t for t in data if not t["is_open"]][0]
        assert closed["instrument"] == "XAU/USD"
        assert closed["side"] == "BUY"
        assert closed["realized_pnl"] == 100.0
        assert closed["closed_at"] is not None
        assert closed["fill_latency_ms"] == 15.0
        assert closed["slippage_ticks"] == 0.3

        # Check open trade
        open_t = [t for t in data if t["is_open"]][0]
        assert open_t["closed_at"] is None
        assert open_t["is_open"] is True

    def test_paper_trades_404(self) -> None:
        resp = self.client.get("/api/paper/99999/trades/")
        assert resp.status_code == 404


class ApiPaperEventsTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.session = _create_session()

    def test_paper_events_empty(self) -> None:
        resp = self.client.get(f"/api/paper/{self.session.pk}/events/")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_paper_events_with_data(self) -> None:
        _create_session_event(self.session, event_type="info", message="started")
        _create_session_event(
            self.session, event_type="risk_warning", message="high DD",
        )
        resp = self.client.get(f"/api/paper/{self.session.pk}/events/")
        data = resp.json()
        assert len(data) == 2
        assert data[0]["event_type"] in ("info", "risk_warning")
        assert "timestamp" in data[0]
        assert "message" in data[0]

    def test_paper_events_404(self) -> None:
        resp = self.client.get("/api/paper/99999/events/")
        assert resp.status_code == 404


class ApiPaperRiskSnapshotsTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.session = _create_session()

    def test_risk_snapshots_empty(self) -> None:
        resp = self.client.get(
            f"/api/paper/{self.session.pk}/risk-snapshots/",
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_risk_snapshots_with_data(self) -> None:
        _create_risk_snapshot(self.session, equity=100000.0)
        _create_risk_snapshot(self.session, equity=101000.0)
        resp = self.client.get(
            f"/api/paper/{self.session.pk}/risk-snapshots/",
        )
        data = resp.json()
        assert len(data) == 2
        assert "timestamp" in data[0]
        assert "equity" in data[0]
        assert "daily_pnl" in data[0]
        assert "open_positions" in data[0]
        assert "drawdown_pct" in data[0]
        assert "utilization_pct" in data[0]

    def test_risk_snapshots_404(self) -> None:
        resp = self.client.get("/api/paper/99999/risk-snapshots/")
        assert resp.status_code == 404


class ApiComparisonDataTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.session = _create_session()
        self.bt_run = _create_run(
            strategy="coban_reborn",
            instrument="XAU/USD",
        )

    @patch("pyfx.analysis.comparison.compare_sessions")
    def test_comparison_data_json(self, mock_compare: MagicMock) -> None:
        from pyfx.analysis.comparison import ComparisonReport

        report = ComparisonReport(
            paper_session_id=self.session.pk,
            backtest_run_id=self.bt_run.pk,
            total_pnl_paper=500.0,
            total_pnl_backtest=600.0,
            pnl_difference=-100.0,
            trades_paper=10,
            trades_backtest=12,
            win_rate_paper=0.6,
            win_rate_backtest=0.65,
        )
        mock_compare.return_value = report

        resp = self.client.get(
            f"/api/compare/{self.session.pk}/{self.bt_run.pk}/",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["paper_session_id"] == self.session.pk
        assert data["backtest_run_id"] == self.bt_run.pk
        assert data["total_pnl_paper"] == 500.0
        assert data["pnl_difference"] == -100.0
        assert data["trades_paper"] == 10
        assert data["win_rate_paper"] == 0.6
        mock_compare.assert_called_once()


# ---------------------------------------------------------------------------
# Paper Equity Curve API Tests
# ---------------------------------------------------------------------------


class ApiPaperEquityCurveTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.session = _create_session()

    def test_equity_curve_empty(self) -> None:
        resp = self.client.get(f"/api/paper/{self.session.pk}/equity/")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_equity_curve_with_snapshots(self) -> None:
        RiskSnapshot.objects.create(
            session=self.session,
            timestamp=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
            equity=100000.0,
            daily_pnl=0.0,
            open_positions=0,
            drawdown_pct=0.0,
            utilization_pct=0.0,
        )
        RiskSnapshot.objects.create(
            session=self.session,
            timestamp=datetime(2024, 1, 2, 11, 0, tzinfo=UTC),
            equity=100500.0,
            daily_pnl=500.0,
            open_positions=1,
            drawdown_pct=0.0,
            utilization_pct=25.0,
        )

        resp = self.client.get(f"/api/paper/{self.session.pk}/equity/")
        data = resp.json()
        assert len(data) == 2
        assert "time" in data[0]
        assert "value" in data[0]
        assert data[0]["value"] == 100000.0
        assert data[1]["value"] == 100500.0

    def test_equity_curve_404(self) -> None:
        resp = self.client.get("/api/paper/99999/equity/")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Paper Cumulative P&L API Tests
# ---------------------------------------------------------------------------


class ApiPaperCumulativePnlTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.session = _create_session()

    def test_cumulative_pnl_empty(self) -> None:
        resp = self.client.get(
            f"/api/paper/{self.session.pk}/cumulative-pnl/",
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_cumulative_pnl_with_closed_trades(self) -> None:
        PaperTrade.objects.create(
            session=self.session,
            instrument="XAU/USD",
            side="BUY",
            quantity=100.0,
            open_price=2050.0,
            close_price=2060.0,
            realized_pnl=100.0,
            opened_at=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
            closed_at=datetime(2024, 1, 2, 11, 0, tzinfo=UTC),
        )
        PaperTrade.objects.create(
            session=self.session,
            instrument="XAU/USD",
            side="SELL",
            quantity=100.0,
            open_price=2060.0,
            close_price=2055.0,
            realized_pnl=50.0,
            opened_at=datetime(2024, 1, 2, 12, 0, tzinfo=UTC),
            closed_at=datetime(2024, 1, 2, 13, 0, tzinfo=UTC),
        )

        resp = self.client.get(
            f"/api/paper/{self.session.pk}/cumulative-pnl/",
        )
        data = resp.json()
        assert len(data) == 2
        assert data[0]["value"] == 100.0
        assert data[1]["value"] == 150.0
        assert "time" in data[0]

    def test_cumulative_pnl_excludes_open_trades(self) -> None:
        # Open trade (no closed_at)
        PaperTrade.objects.create(
            session=self.session,
            instrument="XAU/USD",
            side="BUY",
            quantity=100.0,
            open_price=2050.0,
            opened_at=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
        )
        resp = self.client.get(
            f"/api/paper/{self.session.pk}/cumulative-pnl/",
        )
        assert resp.json() == []

    def test_cumulative_pnl_404(self) -> None:
        resp = self.client.get("/api/paper/99999/cumulative-pnl/")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Paper Trade Markers API Tests
# ---------------------------------------------------------------------------


class ApiPaperTradeMarkersTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.session = _create_session()

    def test_trade_markers_empty(self) -> None:
        resp = self.client.get(
            f"/api/paper/{self.session.pk}/trade-markers/",
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_trade_markers_closed_trade(self) -> None:
        PaperTrade.objects.create(
            session=self.session,
            instrument="XAU/USD",
            side="BUY",
            quantity=100.0,
            open_price=2050.0,
            close_price=2060.0,
            realized_pnl=100.0,
            opened_at=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
            closed_at=datetime(2024, 1, 2, 11, 0, tzinfo=UTC),
        )

        resp = self.client.get(
            f"/api/paper/{self.session.pk}/trade-markers/",
        )
        markers = resp.json()
        assert len(markers) == 2

        entry = markers[0]
        assert entry["type"] == "entry"
        assert entry["side"] == "BUY"
        assert entry["price"] == 2050.0
        assert entry["pnl"] is None
        assert entry["tradeIdx"] == 0

        exit_m = markers[1]
        assert exit_m["type"] == "exit"
        assert exit_m["side"] == "BUY"
        assert exit_m["price"] == 2060.0
        assert exit_m["pnl"] == 100.0
        assert exit_m["tradeIdx"] == 0

    def test_trade_markers_open_trade(self) -> None:
        PaperTrade.objects.create(
            session=self.session,
            instrument="XAU/USD",
            side="SELL",
            quantity=50.0,
            open_price=2070.0,
            opened_at=datetime(2024, 1, 2, 14, 0, tzinfo=UTC),
        )

        resp = self.client.get(
            f"/api/paper/{self.session.pk}/trade-markers/",
        )
        markers = resp.json()
        # Open trade: entry marker only, no exit
        assert len(markers) == 1
        assert markers[0]["type"] == "entry"
        assert markers[0]["side"] == "SELL"
        assert markers[0]["price"] == 2070.0

    def test_trade_markers_mixed(self) -> None:
        """Mix of open and closed trades produces correct marker count."""
        PaperTrade.objects.create(
            session=self.session,
            instrument="XAU/USD",
            side="BUY",
            quantity=100.0,
            open_price=2050.0,
            close_price=2060.0,
            realized_pnl=100.0,
            opened_at=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
            closed_at=datetime(2024, 1, 2, 11, 0, tzinfo=UTC),
        )
        PaperTrade.objects.create(
            session=self.session,
            instrument="XAU/USD",
            side="SELL",
            quantity=50.0,
            open_price=2070.0,
            opened_at=datetime(2024, 1, 2, 14, 0, tzinfo=UTC),
        )

        resp = self.client.get(
            f"/api/paper/{self.session.pk}/trade-markers/",
        )
        markers = resp.json()
        # 1 closed trade (entry + exit) + 1 open trade (entry only) = 3
        assert len(markers) == 3

    def test_trade_markers_404(self) -> None:
        resp = self.client.get("/api/paper/99999/trade-markers/")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Paper New / Start View Tests
# ---------------------------------------------------------------------------


class PaperNewViewTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_paper_new_renders_200(self) -> None:
        resp = self.client.get("/paper/new/")
        assert resp.status_code == 200
        assert resp.context["active_nav"] == "paper"

    def test_paper_new_context_instruments(self) -> None:
        resp = self.client.get("/paper/new/")
        instruments = resp.context["instruments"]
        assert "XAU/USD" in instruments
        assert "EUR/USD" in instruments
        assert len(instruments) == 8

    def test_paper_new_context_risk_defaults(self) -> None:
        resp = self.client.get("/paper/new/")
        rd = resp.context["risk_defaults"]
        assert rd["max_positions"] == 3
        assert rd["daily_loss_limit"] == 2000.0
        assert rd["max_drawdown_pct"] == 10.0
        assert rd["position_size_pct"] == 2.0
        assert rd["risk_sizing_method"] == "fixed_fractional"
        assert rd["account_currency"] == "USD"

    def test_paper_new_context_ib_config(self) -> None:
        resp = self.client.get("/paper/new/")
        ib = resp.context["ib_config"]
        assert "host" in ib
        assert "port" in ib
        assert "trading_mode" in ib
        assert "password" not in ib

    def test_paper_new_contains_form_elements(self) -> None:
        resp = self.client.get("/paper/new/")
        content = resp.content.decode()
        assert "Risk Management" in content
        assert "Strategy" in content
        assert "Start Paper Trading" in content


class PaperStartViewTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_get_redirects(self) -> None:
        resp = self.client.get("/paper/start/")
        assert resp.status_code == 302

    @patch("pyfx.web.dashboard.views._spawn_management_command")
    def test_creates_session(self, mock_spawn: MagicMock) -> None:
        resp = self.client.post("/paper/start/", {
            "strategy": "coban_reborn",
            "instruments": ["XAU/USD"],
            "bar_type": "1-MINUTE-LAST-EXTERNAL",
            "trade_size": "100000",
            "max_positions": "3",
            "max_position_size": "100000",
            "daily_loss_limit": "2000",
            "max_drawdown_pct": "10",
            "position_size_pct": "2",
            "max_notional_per_order": "500000",
            "risk_sizing_method": "fixed_fractional",
        })
        assert resp.status_code == 302
        assert resp.url == "/paper/"  # type: ignore[attr-defined]
        assert PaperTradingSession.objects.count() == 1

    @patch("pyfx.web.dashboard.views._spawn_management_command")
    def test_spawns_subprocess(self, mock_spawn: MagicMock) -> None:
        self.client.post("/paper/start/", {
            "strategy": "coban_reborn",
            "instruments": ["XAU/USD"],
            "bar_type": "1-MINUTE-LAST-EXTERNAL",
            "trade_size": "100000",
            "max_positions": "3",
            "max_position_size": "100000",
            "daily_loss_limit": "2000",
            "max_drawdown_pct": "10",
            "position_size_pct": "2",
            "max_notional_per_order": "500000",
            "risk_sizing_method": "fixed_fractional",
        })
        mock_spawn.assert_called_once()
        args = mock_spawn.call_args[0]
        assert args[0] == "run_paper_web"
        assert args[1] == "--session-id"

    @patch("pyfx.web.dashboard.views._spawn_management_command")
    def test_stores_risk_overrides(self, mock_spawn: MagicMock) -> None:
        self.client.post("/paper/start/", {
            "strategy": "coban_reborn",
            "instruments": ["XAU/USD"],
            "bar_type": "1-MINUTE-LAST-EXTERNAL",
            "trade_size": "100000",
            "max_positions": "5",
            "max_position_size": "200000",
            "daily_loss_limit": "3000",
            "max_drawdown_pct": "15",
            "position_size_pct": "3",
            "max_notional_per_order": "600000",
            "risk_sizing_method": "atr_based",
        })
        session = PaperTradingSession.objects.first()
        assert session is not None
        overrides = session.config_json["risk_overrides"]
        assert overrides["risk_max_positions"] == 5
        assert overrides["risk_daily_loss_limit"] == 3000.0
        assert overrides["risk_sizing_method"] == "atr_based"

    @patch("pyfx.web.dashboard.views._spawn_management_command")
    def test_stores_strategy_params(self, mock_spawn: MagicMock) -> None:
        self.client.post("/paper/start/", {
            "strategy": "coban_reborn",
            "instruments": ["XAU/USD"],
            "bar_type": "1-MINUTE-LAST-EXTERNAL",
            "trade_size": "100000",
            "max_positions": "3",
            "max_position_size": "100000",
            "daily_loss_limit": "2000",
            "max_drawdown_pct": "10",
            "position_size_pct": "2",
            "max_notional_per_order": "500000",
            "risk_sizing_method": "fixed_fractional",
            "param_entry_mode": "trend_follow",
            "param_sma_fast_period": "3",
        })
        session = PaperTradingSession.objects.first()
        assert session is not None
        sp = session.config_json["strategy_params"]
        assert sp["entry_mode"] == "trend_follow"
        assert sp["sma_fast_period"] == 3

    @patch("pyfx.web.dashboard.views._spawn_management_command")
    def test_multiple_instruments(self, mock_spawn: MagicMock) -> None:
        self.client.post("/paper/start/", {
            "strategy": "coban_reborn",
            "instruments": ["XAU/USD", "EUR/USD"],
            "bar_type": "1-MINUTE-LAST-EXTERNAL",
            "trade_size": "100000",
            "max_positions": "3",
            "max_position_size": "100000",
            "daily_loss_limit": "2000",
            "max_drawdown_pct": "10",
            "position_size_pct": "2",
            "max_notional_per_order": "500000",
            "risk_sizing_method": "fixed_fractional",
        })
        session = PaperTradingSession.objects.first()
        assert session is not None
        assert session.instrument == "XAU/USD,EUR/USD"

    def test_validates_no_strategy(self) -> None:
        resp = self.client.post("/paper/start/", {
            "strategy": "",
            "instruments": ["XAU/USD"],
            "max_positions": "3",
            "daily_loss_limit": "2000",
            "max_drawdown_pct": "10",
            "position_size_pct": "2",
            "risk_sizing_method": "fixed_fractional",
        })
        assert resp.status_code == 200  # re-renders form
        assert "strategy" in resp.context["errors"]
        assert PaperTradingSession.objects.count() == 0

    def test_validates_no_instruments(self) -> None:
        resp = self.client.post("/paper/start/", {
            "strategy": "coban_reborn",
            "max_positions": "3",
            "daily_loss_limit": "2000",
            "max_drawdown_pct": "10",
            "position_size_pct": "2",
            "risk_sizing_method": "fixed_fractional",
        })
        assert resp.status_code == 200
        assert "instruments" in resp.context["errors"]

    def test_validates_negative_daily_loss(self) -> None:
        resp = self.client.post("/paper/start/", {
            "strategy": "coban_reborn",
            "instruments": ["XAU/USD"],
            "max_positions": "3",
            "daily_loss_limit": "-100",
            "max_drawdown_pct": "10",
            "position_size_pct": "2",
            "risk_sizing_method": "fixed_fractional",
        })
        assert resp.status_code == 200
        assert "daily_loss_limit" in resp.context["errors"]

    def test_validates_drawdown_over_100(self) -> None:
        resp = self.client.post("/paper/start/", {
            "strategy": "coban_reborn",
            "instruments": ["XAU/USD"],
            "max_positions": "3",
            "daily_loss_limit": "2000",
            "max_drawdown_pct": "101",
            "position_size_pct": "2",
            "risk_sizing_method": "fixed_fractional",
        })
        assert resp.status_code == 200
        assert "max_drawdown_pct" in resp.context["errors"]

    def test_validates_zero_drawdown(self) -> None:
        resp = self.client.post("/paper/start/", {
            "strategy": "coban_reborn",
            "instruments": ["XAU/USD"],
            "max_positions": "3",
            "daily_loss_limit": "2000",
            "max_drawdown_pct": "0",
            "position_size_pct": "2",
            "risk_sizing_method": "fixed_fractional",
        })
        assert resp.status_code == 200
        assert "max_drawdown_pct" in resp.context["errors"]

    def test_validates_zero_max_positions(self) -> None:
        resp = self.client.post("/paper/start/", {
            "strategy": "coban_reborn",
            "instruments": ["XAU/USD"],
            "max_positions": "0",
            "daily_loss_limit": "2000",
            "max_drawdown_pct": "10",
            "position_size_pct": "2",
            "risk_sizing_method": "fixed_fractional",
        })
        assert resp.status_code == 200
        assert "max_positions" in resp.context["errors"]

    def test_validates_bad_sizing_method(self) -> None:
        resp = self.client.post("/paper/start/", {
            "strategy": "coban_reborn",
            "instruments": ["XAU/USD"],
            "max_positions": "3",
            "daily_loss_limit": "2000",
            "max_drawdown_pct": "10",
            "position_size_pct": "2",
            "risk_sizing_method": "bogus",
        })
        assert resp.status_code == 200
        assert "risk_sizing_method" in resp.context["errors"]

    def test_validation_preserves_submitted_values(self) -> None:
        resp = self.client.post("/paper/start/", {
            "strategy": "",
            "instruments": ["XAU/USD"],
            "daily_loss_limit": "3500",
            "max_drawdown_pct": "10",
            "position_size_pct": "2",
            "max_positions": "3",
            "risk_sizing_method": "fixed_fractional",
        })
        assert resp.status_code == 200
        assert resp.context["submitted"]["daily_loss_limit"] == "3500"


class ValidatePaperStartTests(TestCase):
    """Test the _validate_paper_start helper directly."""

    def test_valid_input(self) -> None:
        from pyfx.web.dashboard.views import _validate_paper_start

        errors = _validate_paper_start({
            "strategy": "test",
            "instruments": ["XAU/USD"],
            "daily_loss_limit": "2000",
            "max_drawdown_pct": "10",
            "position_size_pct": "2",
            "max_positions": "3",
            "risk_sizing_method": "fixed_fractional",
        })
        assert errors == {}

    def test_all_invalid(self) -> None:
        from pyfx.web.dashboard.views import _validate_paper_start

        errors = _validate_paper_start({
            "strategy": "",
            "instruments": [],
            "daily_loss_limit": "-1",
            "max_drawdown_pct": "200",
            "position_size_pct": "0",
            "max_positions": "0",
            "risk_sizing_method": "nope",
        })
        assert len(errors) >= 5

    def test_non_numeric_values(self) -> None:
        from pyfx.web.dashboard.views import _validate_paper_start

        errors = _validate_paper_start({
            "strategy": "test",
            "instruments": ["XAU/USD"],
            "daily_loss_limit": "abc",
            "max_drawdown_pct": "xyz",
            "position_size_pct": "nope",
            "max_positions": "wat",
            "risk_sizing_method": "fixed_fractional",
        })
        assert "daily_loss_limit" in errors
        assert "max_drawdown_pct" in errors
        assert "position_size_pct" in errors
        assert "max_positions" in errors


# ---------------------------------------------------------------------------
# API: IB Config & Instruments
# ---------------------------------------------------------------------------


class ApiIbConfigTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_returns_json(self) -> None:
        resp = self.client.get("/api/ib-config/")
        assert resp.status_code == 200
        data = resp.json()
        assert "host" in data
        assert "port" in data
        assert "trading_mode" in data
        assert "warnings" in data
        assert "configured" in data

    def test_no_password_in_response(self) -> None:
        resp = self.client.get("/api/ib-config/")
        data = resp.json()
        assert "password" not in data
        assert "ib_password" not in data
        # Also check raw content
        assert b"password" not in resp.content.lower()


class ApiSupportedInstrumentsTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_returns_all_instruments(self) -> None:
        resp = self.client.get("/api/instruments/")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 8
        names = [i["name"] for i in data]
        assert "XAU/USD" in names
        assert "EUR/USD" in names

    def test_instrument_metadata(self) -> None:
        resp = self.client.get("/api/instruments/")
        data = resp.json()
        xau = next(i for i in data if i["name"] == "XAU/USD")
        assert "tick_size" in xau
        assert "lot_size" in xau
        assert "pip_value" in xau


# ---------------------------------------------------------------------------
# Run Paper Web Command Tests
# ---------------------------------------------------------------------------


class RunPaperWebCommandTests(TestCase):
    def test_session_not_found(self) -> None:
        from django.core.management import call_command
        from io import StringIO

        err = StringIO()
        call_command("run_paper_web", "--session-id", "99999", stderr=err)
        assert "not found" in err.getvalue()

    @patch("pyfx.live.runner.start_live_trading")
    def test_calls_start_live_trading(self, mock_start: MagicMock) -> None:
        from django.core.management import call_command

        session = _create_session(
            status=PaperTradingSession.STATUS_RUNNING,
            config_json={
                "strategy": "coban_reborn",
                "instruments": ["XAU/USD"],
                "bar_type": "1-MINUTE-LAST-EXTERNAL",
                "extra_bar_types": [],
                "strategy_params": {},
                "trade_size": "100000",
                "account_currency": "USD",
                "risk_overrides": {
                    "risk_max_positions": 5,
                    "risk_daily_loss_limit": 3000.0,
                },
            },
        )
        call_command("run_paper_web", "--session-id", str(session.pk))
        mock_start.assert_called_once()
        _, kwargs = mock_start.call_args
        assert kwargs["session_id"] == session.pk

    @patch("pyfx.live.runner.start_live_trading")
    def test_sets_error_on_failure(self, mock_start: MagicMock) -> None:
        from django.core.management import call_command

        mock_start.side_effect = RuntimeError("boom")
        session = _create_session(
            status=PaperTradingSession.STATUS_RUNNING,
            config_json={
                "strategy": "coban_reborn",
                "instruments": ["XAU/USD"],
                "bar_type": "1-MINUTE-LAST-EXTERNAL",
                "extra_bar_types": [],
                "strategy_params": {},
                "trade_size": "100000",
                "account_currency": "USD",
            },
        )
        call_command("run_paper_web", "--session-id", str(session.pk))
        session.refresh_from_db()
        assert session.status == PaperTradingSession.STATUS_ERROR

    @patch("pyfx.live.runner.start_live_trading")
    def test_applies_risk_overrides(self, mock_start: MagicMock) -> None:
        from django.core.management import call_command

        session = _create_session(
            status=PaperTradingSession.STATUS_RUNNING,
            config_json={
                "strategy": "coban_reborn",
                "instruments": ["XAU/USD"],
                "bar_type": "1-MINUTE-LAST-EXTERNAL",
                "extra_bar_types": [],
                "strategy_params": {},
                "trade_size": "100000",
                "account_currency": "USD",
                "risk_overrides": {
                    "risk_max_positions": 7,
                    "risk_daily_loss_limit": 5000.0,
                },
            },
        )
        call_command("run_paper_web", "--session-id", str(session.pk))
        mock_start.assert_called_once()
        call_args = mock_start.call_args
        patched_settings = call_args[0][1]
        assert patched_settings.risk_max_positions == 7
        assert patched_settings.risk_daily_loss_limit == 5000.0
