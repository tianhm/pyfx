"""Tests for the Django dashboard views, APIs, and management commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from django.test import Client, TestCase

from pyfx.web.dashboard.models import BacktestRun, EquitySnapshot, Trade


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
        assert "Total Backtests" in content
        assert "Best Return" in content
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
