"""Tests for CLI live commands (start, stop, status, history, compare, config)."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from pyfx.cli import main


class TestLiveStartCommand:
    """Tests for `pyfx live start`."""

    @patch("pyfx.live.runner.start_live_trading")
    @patch("pyfx.cli.settings")
    def test_live_start_basic(
        self, mock_settings: MagicMock, mock_start: MagicMock,
    ) -> None:
        mock_settings.ib_host = "127.0.0.1"
        mock_settings.ib_port = 4002
        mock_settings.ib_account_id = "DU1234567"
        mock_settings.ib_trading_mode = "paper"
        mock_settings.account_currency = "USD"
        mock_settings.validate_ib_config.return_value = []

        runner = CliRunner()
        result = runner.invoke(main, [
            "live", "start",
            "-s", "coban_reborn",
            "-i", "XAU/USD",
        ])

        assert result.exit_code == 0
        assert "Starting paper trading" in result.output
        mock_start.assert_called_once()
        config = mock_start.call_args[0][0]
        assert config.strategy == "coban_reborn"
        assert config.instrument == "XAU/USD"

    @patch("pyfx.live.runner.start_live_trading")
    @patch("pyfx.cli.settings")
    def test_live_start_with_params(
        self, mock_settings: MagicMock, mock_start: MagicMock,
    ) -> None:
        mock_settings.ib_host = "127.0.0.1"
        mock_settings.ib_port = 4002
        mock_settings.ib_account_id = "DU1234567"
        mock_settings.ib_trading_mode = "paper"
        mock_settings.account_currency = "USD"
        mock_settings.validate_ib_config.return_value = []

        runner = CliRunner()
        result = runner.invoke(main, [
            "live", "start",
            "-s", "coban_reborn",
            "-i", "XAU/USD",
            "-p", "entry_mode=trend_follow",
            "-p", "fast_period=3",
        ])

        assert result.exit_code == 0
        config = mock_start.call_args[0][0]
        assert config.strategy_params == {
            "entry_mode": "trend_follow",
            "fast_period": 3,
        }

    @patch("pyfx.live.runner.start_live_trading")
    @patch("pyfx.cli.settings")
    def test_live_start_extra_bar_types(
        self, mock_settings: MagicMock, mock_start: MagicMock,
    ) -> None:
        mock_settings.ib_host = "127.0.0.1"
        mock_settings.ib_port = 4002
        mock_settings.ib_account_id = "DU1234567"
        mock_settings.ib_trading_mode = "paper"
        mock_settings.account_currency = "USD"
        mock_settings.validate_ib_config.return_value = []

        runner = CliRunner()
        result = runner.invoke(main, [
            "live", "start",
            "-s", "coban_reborn",
            "--extra-bar-type", "5-MINUTE-LAST-EXTERNAL",
            "--extra-bar-type", "15-MINUTE-LAST-EXTERNAL",
        ])

        assert result.exit_code == 0
        config = mock_start.call_args[0][0]
        assert config.extra_bar_types == [
            "5-MINUTE-LAST-EXTERNAL",
            "15-MINUTE-LAST-EXTERNAL",
        ]

    @patch("pyfx.cli.settings")
    def test_live_start_refuses_live_without_confirm(
        self, mock_settings: MagicMock,
    ) -> None:
        mock_settings.ib_host = "127.0.0.1"
        mock_settings.ib_port = 4001
        mock_settings.ib_account_id = "U1234567"
        mock_settings.ib_trading_mode = "live"
        mock_settings.account_currency = "USD"
        mock_settings.validate_ib_config.return_value = [
            "WARNING: trading_mode is 'live' -- use --confirm-live flag",
        ]

        runner = CliRunner()
        result = runner.invoke(main, [
            "live", "start",
            "-s", "coban_reborn",
        ])

        assert result.exit_code != 0
        assert "confirm-live" in result.output

    @patch("pyfx.live.runner.start_live_trading")
    @patch("pyfx.cli.settings")
    def test_live_start_shows_warnings(
        self, mock_settings: MagicMock, mock_start: MagicMock,
    ) -> None:
        mock_settings.ib_host = "127.0.0.1"
        mock_settings.ib_port = 4002
        mock_settings.ib_account_id = None
        mock_settings.ib_trading_mode = "paper"
        mock_settings.account_currency = "USD"
        mock_settings.validate_ib_config.return_value = [
            "PYFX_IB_ACCOUNT_ID is not set",
        ]

        runner = CliRunner()
        result = runner.invoke(main, [
            "live", "start",
            "-s", "coban_reborn",
        ])

        assert result.exit_code == 0
        assert "PYFX_IB_ACCOUNT_ID is not set" in result.output

    @patch("pyfx.live.runner.start_live_trading")
    @patch("pyfx.cli.settings")
    def test_live_start_displays_gateway_info(
        self, mock_settings: MagicMock, mock_start: MagicMock,
    ) -> None:
        mock_settings.ib_host = "192.168.1.10"
        mock_settings.ib_port = 4002
        mock_settings.ib_account_id = "DU9999999"
        mock_settings.ib_trading_mode = "paper"
        mock_settings.account_currency = "USD"
        mock_settings.validate_ib_config.return_value = []

        runner = CliRunner()
        result = runner.invoke(main, [
            "live", "start",
            "-s", "coban_reborn",
        ])

        assert "192.168.1.10:4002" in result.output
        assert "DU9999999" in result.output
        assert "paper" in result.output


    @patch("pyfx.live.runner.start_live_trading")
    @patch("pyfx.cli.settings")
    def test_live_start_multiple_instruments(
        self, mock_settings: MagicMock, mock_start: MagicMock,
    ) -> None:
        mock_settings.ib_host = "127.0.0.1"
        mock_settings.ib_port = 4002
        mock_settings.ib_account_id = "DU1234567"
        mock_settings.ib_trading_mode = "paper"
        mock_settings.account_currency = "USD"
        mock_settings.validate_ib_config.return_value = []

        runner = CliRunner()
        result = runner.invoke(main, [
            "live", "start",
            "-s", "coban_reborn",
            "-i", "XAU/USD",
            "-i", "EUR/USD",
        ])

        assert result.exit_code == 0
        assert "XAU/USD" in result.output
        assert "EUR/USD" in result.output
        config = mock_start.call_args[0][0]
        assert config.instruments == ["XAU/USD", "EUR/USD"]
        assert config.instrument == "XAU/USD"


class TestLiveTestConnectionCommand:
    """Tests for `pyfx live test-connection`."""

    @patch("pyfx.cli.settings")
    def test_connection_success(self, mock_settings: MagicMock) -> None:
        from pyfx.core.types import ConnectionTestResult

        mock_settings.ib_host = "127.0.0.1"
        mock_settings.ib_port = 4002

        mock_result = ConnectionTestResult(
            success=True,
            elapsed_seconds=0.1,
            diagnostics=["PYFX_IB_USERNAME: te***", "PYFX_IB_PASSWORD: ***",
                         "Config validation passed"],
            warnings=[],
        )

        with patch(
            "pyfx.live.connection.validate_ib_config", return_value=mock_result,
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["live", "test-connection"])

        assert result.exit_code == 0
        assert "Config OK" in result.output
        assert "Config validation passed" in result.output

    @patch("pyfx.cli.settings")
    def test_connection_failure(self, mock_settings: MagicMock) -> None:
        from pyfx.core.types import ConnectionTestResult

        mock_settings.ib_host = "127.0.0.1"
        mock_settings.ib_port = 4002

        mock_result = ConnectionTestResult(
            success=False,
            elapsed_seconds=0.05,
            diagnostics=["PYFX_IB_USERNAME: not set", "FAIL: Credentials missing"],
            warnings=[],
            error="Missing required IB configuration",
        )

        with patch(
            "pyfx.live.connection.validate_ib_config", return_value=mock_result,
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["live", "test-connection"])

        assert result.exit_code == 1
        assert "Config validation failed" in result.output
        assert "Missing required IB configuration" in result.output

    @patch("pyfx.cli.settings")
    def test_connection_with_warnings(self, mock_settings: MagicMock) -> None:
        from pyfx.core.types import ConnectionTestResult

        mock_settings.ib_host = "127.0.0.1"
        mock_settings.ib_port = 4002

        mock_result = ConnectionTestResult(
            success=True,
            elapsed_seconds=0.1,
            diagnostics=["Config validation passed"],
            warnings=["PYFX_IB_ACCOUNT_ID is not set"],
        )

        with patch(
            "pyfx.live.connection.validate_ib_config", return_value=mock_result,
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["live", "test-connection"])

        assert result.exit_code == 0
        assert "PYFX_IB_ACCOUNT_ID is not set" in result.output


class TestLiveStopCommand:
    """Tests for `pyfx live stop`."""

    @patch("pyfx.web.dashboard.services.stop_paper_session")
    @patch("pyfx.web.dashboard.models.PaperTradingSession")
    @patch("pyfx.cli._setup_django")
    def test_live_stop_marks_session(
        self,
        _setup: MagicMock,
        MockSession: MagicMock,
        mock_stop: MagicMock,
    ) -> None:
        mock_session = MagicMock()
        mock_session.pk = 42
        mock_session.instrument = "XAU/USD"
        qs = MockSession.objects.filter.return_value
        qs.order_by.return_value.first.return_value = mock_session

        runner = CliRunner()
        result = runner.invoke(main, ["live", "stop"])

        assert result.exit_code == 0
        assert "42" in result.output
        assert "stopped" in result.output
        mock_stop.assert_called_once_with(42)

    @patch("pyfx.web.dashboard.models.PaperTradingSession")
    @patch("pyfx.cli._setup_django")
    def test_live_stop_no_sessions(
        self, _setup: MagicMock, MockSession: MagicMock,
    ) -> None:
        qs = MockSession.objects.filter.return_value
        qs.order_by.return_value.first.return_value = None
        qs.exists.return_value = False

        runner = CliRunner()
        result = runner.invoke(main, ["live", "stop"])

        assert result.exit_code == 0
        assert "No running" in result.output

    @patch("pyfx.web.dashboard.services.stop_paper_session")
    @patch("pyfx.web.dashboard.models.PaperTradingSession")
    @patch("pyfx.cli._setup_django")
    def test_live_stop_session_id(
        self,
        _setup: MagicMock,
        MockSession: MagicMock,
        mock_stop: MagicMock,
    ) -> None:
        mock_session = MagicMock()
        mock_session.pk = 7
        mock_session.instrument = "EUR/USD"
        MockSession.objects.get.return_value = mock_session

        runner = CliRunner()
        result = runner.invoke(main, ["live", "stop", "--session-id", "7"])

        assert result.exit_code == 0
        assert "7" in result.output
        mock_stop.assert_called_once_with(7)

    @patch("pyfx.web.dashboard.services.stop_paper_session")
    @patch("pyfx.web.dashboard.models.PaperTradingSession")
    @patch("pyfx.cli._setup_django")
    def test_live_stop_all(
        self,
        _setup: MagicMock,
        MockSession: MagicMock,
        mock_stop: MagicMock,
    ) -> None:
        s1 = MagicMock(pk=1, instrument="XAU/USD")
        s2 = MagicMock(pk=2, instrument="EUR/USD")
        qs = MockSession.objects.filter.return_value
        qs.exists.return_value = True
        qs.__iter__ = MagicMock(return_value=iter([s1, s2]))

        runner = CliRunner()
        result = runner.invoke(main, ["live", "stop", "--all"])

        assert result.exit_code == 0
        assert mock_stop.call_count == 2
        assert "XAU/USD" in result.output
        assert "EUR/USD" in result.output

    @patch("pyfx.web.dashboard.models.PaperTradingSession")
    @patch("pyfx.cli._setup_django")
    def test_live_stop_session_not_found(
        self, _setup: MagicMock, MockSession: MagicMock,
    ) -> None:
        MockSession.objects.get.side_effect = MockSession.DoesNotExist
        MockSession.DoesNotExist = type("DoesNotExist", (Exception,), {})
        MockSession.objects.get.side_effect = MockSession.DoesNotExist()

        runner = CliRunner()
        result = runner.invoke(main, ["live", "stop", "--session-id", "999"])

        assert result.exit_code == 1

    @patch("pyfx.web.dashboard.models.PaperTradingSession")
    @patch("pyfx.cli._setup_django")
    def test_live_stop_all_no_sessions(
        self, _setup: MagicMock, MockSession: MagicMock,
    ) -> None:
        qs = MockSession.objects.filter.return_value
        qs.exists.return_value = False

        runner = CliRunner()
        result = runner.invoke(main, ["live", "stop", "--all"])

        assert result.exit_code == 0
        assert "No running" in result.output


class TestLiveStatusCommand:
    """Tests for `pyfx live status`."""

    @patch("pyfx.live.runner.get_session_status")
    def test_live_status_basic(self, mock_status: MagicMock) -> None:
        mock_status.return_value = {
            "session_id": 1,
            "status": "running",
            "strategy": "coban_reborn",
            "instrument": "XAU/USD",
            "started_at": "2024-01-01 00:00:00+00:00",
            "stopped_at": None,
            "total_pnl": 500.0,
            "total_return_pct": 0.5,
            "num_trades": 10,
            "win_rate": 0.6,
            "profit_factor": 1.8,
            "max_drawdown_pct": 2.0,
            "open_trades": [],
            "recent_events": [],
        }

        runner = CliRunner()
        result = runner.invoke(main, ["live", "status"])

        assert result.exit_code == 0
        assert "Session #1" in result.output
        assert "running" in result.output
        assert "coban_reborn" in result.output
        assert "XAU/USD" in result.output
        assert "$500.00" in result.output or "500.00" in result.output

    @patch("pyfx.live.runner.get_session_status")
    def test_live_status_with_session_id(self, mock_status: MagicMock) -> None:
        mock_status.return_value = {
            "session_id": 5,
            "status": "stopped",
            "strategy": "sample_sma",
            "instrument": "EUR/USD",
            "started_at": "2024-01-01 00:00:00+00:00",
            "stopped_at": "2024-01-02 00:00:00+00:00",
            "total_pnl": None,
            "total_return_pct": None,
            "num_trades": 0,
            "win_rate": None,
            "profit_factor": None,
            "max_drawdown_pct": None,
            "open_trades": [],
            "recent_events": [],
        }

        runner = CliRunner()
        result = runner.invoke(main, ["live", "status", "--session-id", "5"])

        assert result.exit_code == 0
        assert "Session #5" in result.output
        mock_status.assert_called_once_with(5)

    @patch("pyfx.live.runner.get_session_status")
    def test_live_status_error(self, mock_status: MagicMock) -> None:
        mock_status.return_value = {"error": "No paper trading sessions found"}

        runner = CliRunner()
        result = runner.invoke(main, ["live", "status"])

        assert result.exit_code == 0
        assert "No paper trading sessions found" in result.output

    @patch("pyfx.live.runner.get_session_status")
    def test_live_status_with_open_trades(self, mock_status: MagicMock) -> None:
        mock_status.return_value = {
            "session_id": 1,
            "status": "running",
            "strategy": "coban_reborn",
            "instrument": "XAU/USD",
            "started_at": "2024-01-01 00:00:00+00:00",
            "stopped_at": None,
            "total_pnl": 200.0,
            "total_return_pct": 0.2,
            "num_trades": 3,
            "win_rate": 0.67,
            "profit_factor": 2.0,
            "max_drawdown_pct": 1.5,
            "open_trades": [
                {
                    "instrument": "XAU/USD",
                    "side": "BUY",
                    "quantity": 100.0,
                    "open_price": 2050.0,
                },
            ],
            "recent_events": [
                {
                    "event_type": "info",
                    "message": "Session started",
                },
            ],
        }

        runner = CliRunner()
        result = runner.invoke(main, ["live", "status"])

        assert result.exit_code == 0
        assert "Open positions (1)" in result.output
        assert "BUY" in result.output
        assert "2050.0" in result.output
        assert "Recent events" in result.output
        assert "Session started" in result.output

    @patch("pyfx.live.runner.get_all_running_sessions")
    def test_live_status_all(self, mock_all: MagicMock) -> None:
        mock_all.return_value = [
            {
                "session_id": 1, "strategy": "coban_reborn",
                "instrument": "XAU/USD", "started_at": "2024-01-01",
                "client_id": 1, "process_pid": 12345,
                "total_pnl": 500.0, "num_trades": 10,
            },
            {
                "session_id": 2, "strategy": "coban_reborn",
                "instrument": "EUR/USD,GBP/USD", "started_at": "2024-01-01",
                "client_id": 2, "process_pid": 12346,
                "total_pnl": None, "num_trades": 0,
            },
        ]

        runner = CliRunner()
        result = runner.invoke(main, ["live", "status", "--all"])

        assert result.exit_code == 0
        assert "Running Sessions (2)" in result.output
        assert "XAU/USD" in result.output

    @patch("pyfx.live.runner.get_all_running_sessions")
    def test_live_status_all_empty(self, mock_all: MagicMock) -> None:
        mock_all.return_value = []

        runner = CliRunner()
        result = runner.invoke(main, ["live", "status", "--all"])

        assert result.exit_code == 0
        assert "No running" in result.output


class TestLiveHistoryCommand:
    """Tests for `pyfx live history`."""

    @patch("pyfx.live.runner.get_session_history")
    def test_live_history_with_trades(self, mock_history: MagicMock) -> None:
        mock_history.return_value = [
            {
                "trades": [
                    {
                        "opened_at": "2024-01-02 10:00:00+00:00",
                        "side": "BUY",
                        "instrument": "XAU/USD",
                        "realized_pnl": 150.0,
                    },
                    {
                        "opened_at": "2024-01-02 14:00:00+00:00",
                        "side": "SELL",
                        "instrument": "XAU/USD",
                        "realized_pnl": -50.0,
                    },
                ],
                "events": [
                    {
                        "timestamp": "2024-01-02 10:00:00+00:00",
                        "event_type": "info",
                        "message": "Order filled",
                    },
                ],
            },
        ]

        runner = CliRunner()
        result = runner.invoke(main, ["live", "history"])

        assert result.exit_code == 0
        assert "Trades (2)" in result.output
        assert "BUY" in result.output
        assert "$150.00" in result.output or "+150.00" in result.output
        assert "Events (1)" in result.output

    @patch("pyfx.live.runner.get_session_history")
    def test_live_history_empty(self, mock_history: MagicMock) -> None:
        mock_history.return_value = [{"trades": [], "events": []}]

        runner = CliRunner()
        result = runner.invoke(main, ["live", "history"])

        assert result.exit_code == 0
        assert "No trades found" in result.output
        assert "No events found" in result.output

    @patch("pyfx.live.runner.get_session_history")
    def test_live_history_completely_empty(self, mock_history: MagicMock) -> None:
        mock_history.return_value = []

        runner = CliRunner()
        result = runner.invoke(main, ["live", "history"])

        assert result.exit_code == 0
        assert "No trades found" in result.output

    @patch("pyfx.live.runner.get_session_history")
    def test_live_history_with_last_n(self, mock_history: MagicMock) -> None:
        mock_history.return_value = [{"trades": [], "events": []}]

        runner = CliRunner()
        result = runner.invoke(main, ["live", "history", "--last", "5"])

        assert result.exit_code == 0
        mock_history.assert_called_once_with(last_n=5, since=None)

    @patch("pyfx.live.runner.get_session_history")
    def test_live_history_with_since(self, mock_history: MagicMock) -> None:
        mock_history.return_value = [{"trades": [], "events": []}]

        runner = CliRunner()
        result = runner.invoke(main, [
            "live", "history", "--since", "2024-01-01",
        ])

        assert result.exit_code == 0
        call_args = mock_history.call_args
        assert call_args[1]["since"] is not None

    @patch("pyfx.live.runner.get_session_history")
    def test_live_history_open_trade(self, mock_history: MagicMock) -> None:
        mock_history.return_value = [
            {
                "trades": [
                    {
                        "opened_at": "2024-01-02 10:00:00+00:00",
                        "side": "BUY",
                        "instrument": "XAU/USD",
                        "realized_pnl": None,
                    },
                ],
                "events": [],
            },
        ]

        runner = CliRunner()
        result = runner.invoke(main, ["live", "history"])

        assert result.exit_code == 0
        assert "open" in result.output


class TestLiveCompareCommand:
    """Tests for `pyfx live compare`."""

    @patch("pyfx.analysis.comparison.compare_sessions")
    @patch("pyfx.cli._setup_django")
    def test_compare_table_format(
        self, _setup: MagicMock, mock_compare: MagicMock,
    ) -> None:
        from pyfx.analysis.comparison import ComparisonReport

        mock_compare.return_value = ComparisonReport(
            paper_session_id=1,
            backtest_run_id=2,
            matched=[],
            paper_only=[],
            backtest_only=[],
            total_pnl_paper=500.0,
            total_pnl_backtest=600.0,
            pnl_difference=-100.0,
            trades_paper=10,
            trades_backtest=12,
            win_rate_paper=0.6,
            win_rate_backtest=0.65,
            profit_factor_paper=1.8,
            profit_factor_backtest=2.0,
            avg_slippage_delta=0.0005,
            daily_comparison=[],
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "live", "compare",
            "--session", "1",
            "--backtest", "2",
        ])

        assert result.exit_code == 0
        assert "Paper Session #1" in result.output
        assert "Backtest #2" in result.output
        assert "Total P&L" in result.output
        assert "Trades" in result.output
        assert "Win Rate" in result.output
        assert "Profit Factor" in result.output
        assert "slippage delta" in result.output.lower()

    @patch("pyfx.analysis.comparison.compare_sessions")
    @patch("pyfx.cli._setup_django")
    def test_compare_json_format(
        self, _setup: MagicMock, mock_compare: MagicMock,
    ) -> None:
        from pyfx.analysis.comparison import ComparisonReport

        mock_compare.return_value = ComparisonReport(
            paper_session_id=1,
            backtest_run_id=2,
            total_pnl_paper=500.0,
            total_pnl_backtest=600.0,
            pnl_difference=-100.0,
            trades_paper=10,
            trades_backtest=12,
            win_rate_paper=0.6,
            win_rate_backtest=0.65,
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "live", "compare",
            "--session", "1",
            "--backtest", "2",
            "--format", "json",
        ])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["paper_session_id"] == 1
        assert data["backtest_run_id"] == 2
        assert data["total_pnl_paper"] == 500.0

    @patch("pyfx.analysis.comparison.compare_sessions")
    @patch("pyfx.cli._setup_django")
    def test_compare_with_daily(
        self, _setup: MagicMock, mock_compare: MagicMock,
    ) -> None:
        from datetime import date

        from pyfx.analysis.comparison import ComparisonReport, DailyDelta

        mock_compare.return_value = ComparisonReport(
            paper_session_id=1,
            backtest_run_id=2,
            total_pnl_paper=500.0,
            total_pnl_backtest=600.0,
            pnl_difference=-100.0,
            trades_paper=5,
            trades_backtest=6,
            win_rate_paper=0.6,
            win_rate_backtest=0.65,
            daily_comparison=[
                DailyDelta(
                    date=date(2024, 1, 2),
                    paper_pnl=200.0,
                    backtest_pnl=250.0,
                    delta=-50.0,
                ),
                DailyDelta(
                    date=date(2024, 1, 3),
                    paper_pnl=300.0,
                    backtest_pnl=350.0,
                    delta=-50.0,
                ),
            ],
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "live", "compare",
            "--session", "1",
            "--backtest", "2",
        ])

        assert result.exit_code == 0
        assert "2024-01-02" in result.output
        assert "2024-01-03" in result.output

    @patch("pyfx.analysis.comparison.compare_sessions")
    @patch("pyfx.cli._setup_django")
    def test_compare_no_profit_factor(
        self, _setup: MagicMock, mock_compare: MagicMock,
    ) -> None:
        from pyfx.analysis.comparison import ComparisonReport

        mock_compare.return_value = ComparisonReport(
            paper_session_id=1,
            backtest_run_id=2,
            total_pnl_paper=0.0,
            total_pnl_backtest=0.0,
            pnl_difference=0.0,
            trades_paper=0,
            trades_backtest=0,
            win_rate_paper=0.0,
            win_rate_backtest=0.0,
            profit_factor_paper=None,
            profit_factor_backtest=None,
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "live", "compare",
            "--session", "1",
            "--backtest", "2",
        ])

        assert result.exit_code == 0
        # Profit Factor line should be absent when both are None
        assert "Profit Factor" not in result.output


class TestLiveConfigCommand:
    """Tests for `pyfx live config`."""

    @patch("pyfx.cli.settings")
    def test_live_config_displays_settings(self, mock_settings: MagicMock) -> None:
        mock_settings.ib_host = "127.0.0.1"
        mock_settings.ib_port = 4002
        mock_settings.ib_account_id = "DU1234567"
        mock_settings.ib_trading_mode = "paper"
        mock_settings.ib_read_only_api = False
        mock_settings.ib_gateway_image = "ghcr.io/gnzsnz/ib-gateway:stable"
        mock_settings.account_currency = "USD"
        mock_settings.risk_sizing_method = "fixed_fractional"
        mock_settings.risk_position_size_pct = 2.0
        mock_settings.risk_max_positions = 3
        mock_settings.risk_max_position_size = Decimal("100000")
        mock_settings.risk_daily_loss_limit = 2000.0
        mock_settings.risk_max_drawdown_pct = 10.0
        mock_settings.risk_max_notional_per_order = 500_000
        mock_settings.validate_ib_config.return_value = []

        runner = CliRunner()
        result = runner.invoke(main, ["live", "config"])

        assert result.exit_code == 0
        assert "Live Trading Configuration" in result.output
        assert "127.0.0.1" in result.output
        assert "4002" in result.output
        assert "DU1234567" in result.output
        assert "paper" in result.output
        assert "fixed_fractional" in result.output
        assert "Risk Management" in result.output

    @patch("pyfx.cli.settings")
    def test_live_config_shows_warnings(self, mock_settings: MagicMock) -> None:
        mock_settings.ib_host = "127.0.0.1"
        mock_settings.ib_port = 4002
        mock_settings.ib_account_id = None
        mock_settings.ib_trading_mode = "paper"
        mock_settings.ib_read_only_api = True
        mock_settings.ib_gateway_image = "ghcr.io/gnzsnz/ib-gateway:stable"
        mock_settings.account_currency = "USD"
        mock_settings.risk_sizing_method = "fixed_fractional"
        mock_settings.risk_position_size_pct = 2.0
        mock_settings.risk_max_positions = 3
        mock_settings.risk_max_position_size = Decimal("100000")
        mock_settings.risk_daily_loss_limit = 2000.0
        mock_settings.risk_max_drawdown_pct = 10.0
        mock_settings.risk_max_notional_per_order = 500_000
        mock_settings.validate_ib_config.return_value = [
            "PYFX_IB_ACCOUNT_ID is not set",
            "read_only_api is True -- order execution will be blocked",
        ]

        runner = CliRunner()
        result = runner.invoke(main, ["live", "config"])

        assert result.exit_code == 0
        assert "Warnings:" in result.output
        assert "PYFX_IB_ACCOUNT_ID is not set" in result.output
        assert "read_only_api is True" in result.output

    @patch("pyfx.cli.settings")
    def test_live_config_no_account_shows_not_set(
        self, mock_settings: MagicMock,
    ) -> None:
        mock_settings.ib_host = "127.0.0.1"
        mock_settings.ib_port = 4002
        mock_settings.ib_account_id = None
        mock_settings.ib_trading_mode = "paper"
        mock_settings.ib_read_only_api = False
        mock_settings.ib_gateway_image = "ghcr.io/gnzsnz/ib-gateway:stable"
        mock_settings.account_currency = "USD"
        mock_settings.risk_sizing_method = "fixed_fractional"
        mock_settings.risk_position_size_pct = 2.0
        mock_settings.risk_max_positions = 3
        mock_settings.risk_max_position_size = Decimal("100000")
        mock_settings.risk_daily_loss_limit = 2000.0
        mock_settings.risk_max_drawdown_pct = 10.0
        mock_settings.risk_max_notional_per_order = 500_000
        mock_settings.validate_ib_config.return_value = []

        runner = CliRunner()
        result = runner.invoke(main, ["live", "config"])

        assert result.exit_code == 0
        assert "(not set)" in result.output


# ---------------------------------------------------------------------------
# validate_ib_config() -- direct tests
# ---------------------------------------------------------------------------


class TestValidateIBConfig:
    """Tests for pyfx.live.connection.validate_ib_config (config-only check)."""

    def test_valid_config(self) -> None:
        from pyfx.live.connection import validate_ib_config

        mock_settings = MagicMock()
        mock_settings.ib_username = "testuser"
        mock_settings.ib_password = "testpass"
        mock_settings.ib_account_id = "DU1234567"
        mock_settings.ib_host = "127.0.0.1"
        mock_settings.ib_port = 4002
        mock_settings.ib_trading_mode = "paper"
        mock_settings.account_currency = "USD"
        mock_settings.validate_ib_config.return_value = []

        result = validate_ib_config(mock_settings)

        assert result.success is True
        assert result.error is None
        assert "Config validation passed" in result.diagnostics
        assert result.elapsed_seconds >= 0

    def test_missing_credentials(self) -> None:
        from pyfx.live.connection import validate_ib_config

        mock_settings = MagicMock()
        mock_settings.ib_username = None
        mock_settings.ib_password = None
        mock_settings.ib_account_id = "DU1234567"
        mock_settings.ib_host = "127.0.0.1"
        mock_settings.ib_port = 4002
        mock_settings.ib_trading_mode = "paper"
        mock_settings.account_currency = "USD"
        mock_settings.validate_ib_config.return_value = []

        result = validate_ib_config(mock_settings)

        assert result.success is False
        assert result.error is not None
        assert "PYFX_IB_USERNAME: not set" in result.diagnostics
        assert "PYFX_IB_PASSWORD: not set" in result.diagnostics
        assert "FAIL: Credentials missing" in result.diagnostics

    def test_missing_account_id(self) -> None:
        from pyfx.live.connection import validate_ib_config

        mock_settings = MagicMock()
        mock_settings.ib_username = "testuser"
        mock_settings.ib_password = "testpass"
        mock_settings.ib_account_id = None
        mock_settings.ib_host = "127.0.0.1"
        mock_settings.ib_port = 4002
        mock_settings.ib_trading_mode = "paper"
        mock_settings.account_currency = "USD"
        mock_settings.validate_ib_config.return_value = []

        result = validate_ib_config(mock_settings)

        assert result.success is False
        assert "FAIL: Account ID missing" in result.diagnostics
        assert "PYFX_IB_ACCOUNT_ID: not set" in result.diagnostics

    def test_username_masked(self) -> None:
        from pyfx.live.connection import validate_ib_config

        mock_settings = MagicMock()
        mock_settings.ib_username = "testuser"
        mock_settings.ib_password = "secret"
        mock_settings.ib_account_id = "DU1234567"
        mock_settings.ib_host = "127.0.0.1"
        mock_settings.ib_port = 4002
        mock_settings.ib_trading_mode = "paper"
        mock_settings.account_currency = "USD"
        mock_settings.validate_ib_config.return_value = []

        result = validate_ib_config(mock_settings)

        assert any("te***" in d for d in result.diagnostics)
        assert any("***" in d and "PASSWORD" in d for d in result.diagnostics)

    def test_with_warnings(self) -> None:
        from pyfx.live.connection import validate_ib_config

        mock_settings = MagicMock()
        mock_settings.ib_username = "testuser"
        mock_settings.ib_password = "secret"
        mock_settings.ib_account_id = "DU1234567"
        mock_settings.ib_host = "127.0.0.1"
        mock_settings.ib_port = 4002
        mock_settings.ib_trading_mode = "paper"
        mock_settings.account_currency = "USD"
        mock_settings.validate_ib_config.return_value = [
            "PYFX_IB_ACCOUNT_ID is not set",
        ]

        result = validate_ib_config(mock_settings)

        assert result.warnings == ["PYFX_IB_ACCOUNT_ID is not set"]

    def test_diagnostics_contain_config_values(self) -> None:
        from pyfx.live.connection import validate_ib_config

        mock_settings = MagicMock()
        mock_settings.ib_username = "user1"
        mock_settings.ib_password = "pass1"
        mock_settings.ib_account_id = "DU9999999"
        mock_settings.ib_host = "192.168.1.10"
        mock_settings.ib_port = 4002
        mock_settings.ib_trading_mode = "paper"
        mock_settings.account_currency = "EUR"
        mock_settings.validate_ib_config.return_value = []

        result = validate_ib_config(mock_settings)

        diag_text = "\n".join(result.diagnostics)
        assert "DU9999999" in diag_text
        assert "192.168.1.10" in diag_text
        assert "4002" in diag_text
        assert "paper" in diag_text
        assert "EUR" in diag_text

    def test_password_only_missing(self) -> None:
        """Username set, password missing -> still fails."""
        from pyfx.live.connection import validate_ib_config

        mock_settings = MagicMock()
        mock_settings.ib_username = "testuser"
        mock_settings.ib_password = None
        mock_settings.ib_account_id = "DU1234567"
        mock_settings.ib_host = "127.0.0.1"
        mock_settings.ib_port = 4002
        mock_settings.ib_trading_mode = "paper"
        mock_settings.account_currency = "USD"
        mock_settings.validate_ib_config.return_value = []

        result = validate_ib_config(mock_settings)

        assert result.success is False
        assert "FAIL: Credentials missing" in result.diagnostics
