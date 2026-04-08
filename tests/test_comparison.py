"""Tests for pyfx.analysis.comparison – trade matching and comparison."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from pyfx.analysis.comparison import (
    ComparisonReport,
    DailyDelta,
    TradeData,
    TradeMatch,
    WeeklyDelta,
    _compute_daily,
    _compute_weekly,
    _match_trades,
    compare_sessions,
)
from pyfx.web.dashboard.models import (
    BacktestRun,
    PaperTrade,
    PaperTradingSession,
    Trade,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_session(**kwargs: object) -> PaperTradingSession:
    defaults: dict[str, object] = dict(
        status="running",
        strategy="coban_reborn",
        instrument="XAU/USD",
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        account_currency="USD",
    )
    defaults.update(kwargs)
    return PaperTradingSession.objects.create(**defaults)


def _create_backtest_run(**kwargs: object) -> BacktestRun:
    defaults: dict[str, object] = dict(
        strategy="coban_reborn",
        instrument="XAU/USD",
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 6, 30, tzinfo=UTC),
        status=BacktestRun.STATUS_COMPLETED,
        total_pnl=1000.0,
        num_trades=10,
    )
    defaults.update(kwargs)
    return BacktestRun.objects.create(**defaults)


_UNSET: object = object()


def _create_paper_trade(
    session: PaperTradingSession,
    *,
    side: str = "BUY",
    open_price: float = 2050.0,
    close_price: float | None = 2060.0,
    realized_pnl: float | None = 10.0,
    opened_at: datetime | None = None,
    closed_at: datetime | None | object = _UNSET,
) -> PaperTrade:
    return PaperTrade.objects.create(
        session=session,
        instrument="XAU/USD",
        side=side,
        quantity=1.0,
        open_price=open_price,
        close_price=close_price,
        realized_pnl=realized_pnl,
        opened_at=opened_at or datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
        closed_at=(
            datetime(2024, 1, 2, 11, 0, tzinfo=UTC) if closed_at is _UNSET else closed_at
        ),
    )


def _create_backtest_trade(
    run: BacktestRun,
    *,
    side: str = "BUY",
    open_price: float = 2050.0,
    close_price: float = 2060.0,
    realized_pnl: float = 10.0,
    opened_at: datetime | None = None,
    closed_at: datetime | None = None,
) -> Trade:
    return Trade.objects.create(
        run=run,
        instrument="XAU/USD",
        side=side,
        quantity=1.0,
        open_price=open_price,
        close_price=close_price,
        realized_pnl=realized_pnl,
        opened_at=opened_at or datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
        closed_at=closed_at or datetime(2024, 1, 2, 11, 0, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Tests: _match_trades (pure logic)
# ---------------------------------------------------------------------------


class TestMatchTrades:
    def test_exact_time_match(self) -> None:
        paper = [
            TradeData(
                source="paper", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=2050.0, close_price=2060.0,
                realized_pnl=10.0,
                opened_at="2024-01-02T10:00:00+00:00",
                closed_at="2024-01-02T11:00:00+00:00",
            ),
        ]
        backtest = [
            TradeData(
                source="backtest", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=2050.5, close_price=2060.5,
                realized_pnl=10.5,
                opened_at="2024-01-02T10:00:00+00:00",
                closed_at="2024-01-02T11:00:00+00:00",
            ),
        ]
        matched, paper_only, bt_only = _match_trades(paper, backtest, 300)
        assert len(matched) == 1
        assert len(paper_only) == 0
        assert len(bt_only) == 0
        assert matched[0].slippage_delta == pytest.approx(-0.5)
        assert matched[0].pnl_delta == pytest.approx(-0.5)
        assert matched[0].timing_delta_seconds == 0.0

    def test_within_window_match(self) -> None:
        paper = [
            TradeData(
                source="paper", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=2050.0,
                opened_at="2024-01-02T10:02:00+00:00",
            ),
        ]
        backtest = [
            TradeData(
                source="backtest", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=2050.0,
                opened_at="2024-01-02T10:00:00+00:00",
            ),
        ]
        matched, paper_only, bt_only = _match_trades(paper, backtest, 300)
        assert len(matched) == 1
        assert matched[0].timing_delta_seconds == pytest.approx(120.0)

    def test_outside_window_not_matched(self) -> None:
        paper = [
            TradeData(
                source="paper", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=2050.0,
                opened_at="2024-01-02T10:10:00+00:00",
            ),
        ]
        backtest = [
            TradeData(
                source="backtest", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=2050.0,
                opened_at="2024-01-02T10:00:00+00:00",
            ),
        ]
        matched, paper_only, bt_only = _match_trades(paper, backtest, 300)
        assert len(matched) == 0
        assert len(paper_only) == 1
        assert len(bt_only) == 1

    def test_side_mismatch_not_matched(self) -> None:
        paper = [
            TradeData(
                source="paper", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=2050.0,
                opened_at="2024-01-02T10:00:00+00:00",
            ),
        ]
        backtest = [
            TradeData(
                source="backtest", instrument="XAU/USD", side="SELL",
                quantity=1.0, open_price=2050.0,
                opened_at="2024-01-02T10:00:00+00:00",
            ),
        ]
        matched, paper_only, bt_only = _match_trades(paper, backtest, 300)
        assert len(matched) == 0
        assert len(paper_only) == 1
        assert len(bt_only) == 1

    def test_multiple_matches_closest_wins(self) -> None:
        paper = [
            TradeData(
                source="paper", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=2050.0,
                opened_at="2024-01-02T10:01:00+00:00",
            ),
        ]
        backtest = [
            TradeData(
                source="backtest", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=2050.0,
                opened_at="2024-01-02T10:00:00+00:00",
            ),
            TradeData(
                source="backtest", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=2051.0,
                opened_at="2024-01-02T10:00:30+00:00",
            ),
        ]
        matched, paper_only, bt_only = _match_trades(paper, backtest, 300)
        assert len(matched) == 1
        # Should match to 10:00:30 (30s diff) not 10:00:00 (60s diff)
        assert matched[0].backtest_trade is not None
        assert matched[0].backtest_trade.open_price == 2051.0
        assert len(bt_only) == 1

    def test_empty_lists(self) -> None:
        matched, paper_only, bt_only = _match_trades([], [], 300)
        assert matched == []
        assert paper_only == []
        assert bt_only == []

    def test_only_paper_trades(self) -> None:
        paper = [
            TradeData(
                source="paper", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=2050.0,
                opened_at="2024-01-02T10:00:00+00:00",
            ),
        ]
        matched, paper_only, bt_only = _match_trades(paper, [], 300)
        assert len(matched) == 0
        assert len(paper_only) == 1
        assert len(bt_only) == 0

    def test_only_backtest_trades(self) -> None:
        backtest = [
            TradeData(
                source="backtest", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=2050.0,
                opened_at="2024-01-02T10:00:00+00:00",
            ),
        ]
        matched, paper_only, bt_only = _match_trades([], backtest, 300)
        assert len(matched) == 0
        assert len(paper_only) == 0
        assert len(bt_only) == 1

    def test_backtest_trade_not_reused(self) -> None:
        """Each backtest trade can only match one paper trade."""
        paper = [
            TradeData(
                source="paper", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=2050.0,
                opened_at="2024-01-02T10:00:00+00:00",
            ),
            TradeData(
                source="paper", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=2050.0,
                opened_at="2024-01-02T10:00:30+00:00",
            ),
        ]
        backtest = [
            TradeData(
                source="backtest", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=2050.0,
                opened_at="2024-01-02T10:00:00+00:00",
            ),
        ]
        matched, paper_only, bt_only = _match_trades(paper, backtest, 300)
        assert len(matched) == 1
        assert len(paper_only) == 1
        assert len(bt_only) == 0

    def test_naive_datetime_gets_utc(self) -> None:
        """Timestamps without timezone info should be treated as UTC."""
        paper = [
            TradeData(
                source="paper", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=2050.0,
                opened_at="2024-01-02T10:00:00",
            ),
        ]
        backtest = [
            TradeData(
                source="backtest", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=2050.0,
                opened_at="2024-01-02T10:00:00",
            ),
        ]
        matched, _, _ = _match_trades(paper, backtest, 300)
        assert len(matched) == 1

    def test_slippage_delta_with_zero_backtest_price(self) -> None:
        paper = [
            TradeData(
                source="paper", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=2050.0,
                opened_at="2024-01-02T10:00:00+00:00",
            ),
        ]
        backtest = [
            TradeData(
                source="backtest", instrument="XAU/USD", side="BUY",
                quantity=1.0, open_price=0.0,
                opened_at="2024-01-02T10:00:00+00:00",
            ),
        ]
        matched, _, _ = _match_trades(paper, backtest, 300)
        assert len(matched) == 1
        # open_price=0 is falsy, so slippage_delta is None
        assert matched[0].slippage_delta is None


# ---------------------------------------------------------------------------
# Tests: _compute_daily
# ---------------------------------------------------------------------------


class TestComputeDaily:
    def test_basic_daily(self) -> None:
        class FakePaperTrade:
            def __init__(self, closed_at: datetime, realized_pnl: float) -> None:
                self.closed_at = closed_at
                self.realized_pnl = realized_pnl

        class FakeBacktestTrade:
            def __init__(self, closed_at: datetime, realized_pnl: float) -> None:
                self.closed_at = closed_at
                self.realized_pnl = realized_pnl

        p = [
            FakePaperTrade(datetime(2024, 1, 2, 10, 0, tzinfo=UTC), 100.0),
            FakePaperTrade(datetime(2024, 1, 2, 14, 0, tzinfo=UTC), -50.0),
            FakePaperTrade(datetime(2024, 1, 3, 10, 0, tzinfo=UTC), 200.0),
        ]
        b = [
            FakeBacktestTrade(datetime(2024, 1, 2, 10, 0, tzinfo=UTC), 120.0),
        ]
        result = _compute_daily(p, b)  # type: ignore[arg-type]
        assert len(result) == 2
        # Jan 2: paper=50, backtest=120
        jan2 = result[0]
        assert jan2.date == date(2024, 1, 2)
        assert jan2.paper_pnl == pytest.approx(50.0)
        assert jan2.backtest_pnl == pytest.approx(120.0)
        assert jan2.delta == pytest.approx(-70.0)
        assert jan2.paper_trades == 2
        assert jan2.backtest_trades == 1
        # Jan 3: paper=200, backtest=0
        jan3 = result[1]
        assert jan3.date == date(2024, 1, 3)
        assert jan3.paper_pnl == pytest.approx(200.0)
        assert jan3.backtest_pnl == 0.0

    def test_empty_trades(self) -> None:
        result = _compute_daily([], [])
        assert result == []

    def test_paper_trade_with_none_closed_at_skipped(self) -> None:
        class FakePaperTrade:
            def __init__(self) -> None:
                self.closed_at = None
                self.realized_pnl = 100.0

        result = _compute_daily([FakePaperTrade()], [])  # type: ignore[list-item]
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Tests: _compute_weekly
# ---------------------------------------------------------------------------


class TestComputeWeekly:
    def test_basic_weekly(self) -> None:
        daily = [
            DailyDelta(
                date=date(2024, 1, 1), paper_pnl=100.0, backtest_pnl=110.0,
                delta=-10.0, paper_trades=1, backtest_trades=1,
            ),
            DailyDelta(
                date=date(2024, 1, 2), paper_pnl=200.0, backtest_pnl=180.0,
                delta=20.0, paper_trades=2, backtest_trades=1,
            ),
            # Next week (Jan 8 = Monday)
            DailyDelta(
                date=date(2024, 1, 8), paper_pnl=50.0, backtest_pnl=60.0,
                delta=-10.0, paper_trades=1, backtest_trades=1,
            ),
        ]
        result = _compute_weekly(daily)
        assert len(result) == 2
        # First week (starts Mon Dec 25 or Jan 1 depending on weekday)
        # Jan 1, 2024 is Monday
        w1 = result[0]
        assert w1.paper_pnl == pytest.approx(300.0)
        assert w1.backtest_pnl == pytest.approx(290.0)
        assert w1.delta == pytest.approx(10.0)
        # Second week
        w2 = result[1]
        assert w2.paper_pnl == pytest.approx(50.0)

    def test_empty_daily(self) -> None:
        result = _compute_weekly([])
        assert result == []


# ---------------------------------------------------------------------------
# Tests: Pydantic models
# ---------------------------------------------------------------------------


class TestModels:
    def test_trade_data_minimal(self) -> None:
        td = TradeData(
            source="paper",
            instrument="XAU/USD",
            side="BUY",
            quantity=1.0,
            open_price=2050.0,
            opened_at="2024-01-02T10:00:00+00:00",
        )
        assert td.close_price is None
        assert td.realized_pnl is None
        assert td.closed_at is None

    def test_trade_match_unmatched(self) -> None:
        td = TradeData(
            source="paper",
            instrument="XAU/USD",
            side="BUY",
            quantity=1.0,
            open_price=2050.0,
            opened_at="2024-01-02T10:00:00+00:00",
        )
        tm = TradeMatch(paper_trade=td)
        assert tm.backtest_trade is None
        assert tm.slippage_delta is None

    def test_daily_delta_defaults(self) -> None:
        dd = DailyDelta(date=date(2024, 1, 1))
        assert dd.paper_pnl == 0.0
        assert dd.backtest_pnl == 0.0
        assert dd.delta == 0.0

    def test_weekly_delta_defaults(self) -> None:
        wd = WeeklyDelta(week_start=date(2024, 1, 1))
        assert wd.paper_pnl == 0.0
        assert wd.delta == 0.0

    def test_comparison_report_defaults(self) -> None:
        cr = ComparisonReport(paper_session_id=1, backtest_run_id=2)
        assert cr.matched == []
        assert cr.paper_only == []
        assert cr.backtest_only == []
        assert cr.total_pnl_paper == 0.0


# ---------------------------------------------------------------------------
# Tests: compare_sessions (full integration with DB)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCompareSessions:
    def test_basic_comparison(self) -> None:
        session = _create_session()
        bt_run = _create_backtest_run()
        # Create matching trades
        _create_paper_trade(
            session,
            open_price=2050.0,
            close_price=2060.0,
            realized_pnl=10.0,
            opened_at=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
            closed_at=datetime(2024, 1, 2, 11, 0, tzinfo=UTC),
        )
        _create_backtest_trade(
            bt_run,
            open_price=2050.5,
            close_price=2060.5,
            realized_pnl=10.5,
            opened_at=datetime(2024, 1, 2, 10, 1, tzinfo=UTC),
            closed_at=datetime(2024, 1, 2, 11, 1, tzinfo=UTC),
        )
        report = compare_sessions(session_id=session.pk, backtest_id=bt_run.pk)
        assert report.paper_session_id == session.pk
        assert report.backtest_run_id == bt_run.pk
        assert report.trades_paper == 1
        assert report.trades_backtest == 1
        assert len(report.matched) == 1
        assert report.avg_slippage_delta is not None
        assert report.avg_pnl_delta is not None

    def test_auto_resolves_most_recent(self) -> None:
        session = _create_session()
        bt_run = _create_backtest_run()
        _create_paper_trade(session)
        _create_backtest_trade(bt_run)
        report = compare_sessions()
        assert report.paper_session_id == session.pk

    def test_no_session_raises(self) -> None:
        with pytest.raises(ValueError, match="No paper trading sessions"):
            compare_sessions()

    def test_no_backtest_raises(self) -> None:
        session = _create_session()
        with pytest.raises(ValueError, match="No completed backtest found"):
            compare_sessions(session_id=session.pk)

    def test_paper_only_and_backtest_only(self) -> None:
        session = _create_session()
        bt_run = _create_backtest_run()
        # Paper trade at a very different time
        _create_paper_trade(
            session,
            opened_at=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
            closed_at=datetime(2024, 1, 2, 11, 0, tzinfo=UTC),
        )
        _create_backtest_trade(
            bt_run,
            opened_at=datetime(2024, 1, 3, 10, 0, tzinfo=UTC),
            closed_at=datetime(2024, 1, 3, 11, 0, tzinfo=UTC),
        )
        report = compare_sessions(session_id=session.pk, backtest_id=bt_run.pk)
        assert len(report.matched) == 0
        assert len(report.paper_only) == 1
        assert len(report.backtest_only) == 1

    def test_win_rates(self) -> None:
        session = _create_session()
        bt_run = _create_backtest_run()
        _create_paper_trade(session, realized_pnl=10.0)
        _create_paper_trade(
            session,
            realized_pnl=-5.0,
            opened_at=datetime(2024, 1, 3, 10, 0, tzinfo=UTC),
            closed_at=datetime(2024, 1, 3, 11, 0, tzinfo=UTC),
        )
        _create_backtest_trade(bt_run, realized_pnl=10.0)
        report = compare_sessions(session_id=session.pk, backtest_id=bt_run.pk)
        assert report.win_rate_paper == pytest.approx(0.5)
        assert report.win_rate_backtest == pytest.approx(1.0)

    def test_profit_factors(self) -> None:
        session = _create_session()
        bt_run = _create_backtest_run()
        _create_paper_trade(session, realized_pnl=100.0)
        _create_paper_trade(
            session,
            realized_pnl=-50.0,
            opened_at=datetime(2024, 1, 3, 10, 0, tzinfo=UTC),
            closed_at=datetime(2024, 1, 3, 11, 0, tzinfo=UTC),
        )
        report = compare_sessions(session_id=session.pk, backtest_id=bt_run.pk)
        assert report.profit_factor_paper == pytest.approx(2.0)
        # No backtest losses -> None
        _create_backtest_trade(bt_run, realized_pnl=50.0)
        report2 = compare_sessions(session_id=session.pk, backtest_id=bt_run.pk)
        assert report2.profit_factor_backtest is None

    def test_daily_and_weekly_comparison(self) -> None:
        session = _create_session()
        bt_run = _create_backtest_run()
        _create_paper_trade(
            session,
            realized_pnl=100.0,
            closed_at=datetime(2024, 1, 2, 11, 0, tzinfo=UTC),
        )
        _create_backtest_trade(
            bt_run,
            realized_pnl=120.0,
            closed_at=datetime(2024, 1, 2, 11, 0, tzinfo=UTC),
        )
        report = compare_sessions(session_id=session.pk, backtest_id=bt_run.pk)
        assert len(report.daily_comparison) >= 1
        assert len(report.weekly_comparison) >= 1

    def test_open_paper_trades_excluded(self) -> None:
        """Only closed paper trades are included in comparison."""
        session = _create_session()
        bt_run = _create_backtest_run()
        # Open trade (no closed_at)
        _create_paper_trade(
            session,
            close_price=None,
            realized_pnl=None,
            closed_at=None,
        )
        _create_backtest_trade(bt_run)
        report = compare_sessions(session_id=session.pk, backtest_id=bt_run.pk)
        assert report.trades_paper == 0
        assert report.trades_backtest == 1

    def test_auto_resolve_matches_strategy_and_instrument(self) -> None:
        """Auto-resolve backtest should match on strategy + instrument."""
        session = _create_session(strategy="coban_reborn", instrument="XAU/USD")
        # Different strategy -- should not match
        _create_backtest_run(strategy="sample_sma", instrument="XAU/USD")
        # Same strategy but different instrument -- should not match
        _create_backtest_run(strategy="coban_reborn", instrument="EUR/USD")
        # Exact match
        bt_match = _create_backtest_run(strategy="coban_reborn", instrument="XAU/USD")
        _create_backtest_trade(bt_match)
        _create_paper_trade(session)
        report = compare_sessions(session_id=session.pk)
        assert report.backtest_run_id == bt_match.pk
