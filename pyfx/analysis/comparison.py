"""Compare paper trading sessions against backtest runs to find deltas."""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, timedelta

from pydantic import BaseModel, Field


class TradeData(BaseModel):
    """Normalised trade record for comparison (works for both paper and backtest)."""

    source: str  # "paper" or "backtest"
    instrument: str
    side: str
    quantity: float
    open_price: float
    close_price: float | None = None
    realized_pnl: float | None = None
    opened_at: str  # ISO format string for serialisation
    closed_at: str | None = None


class TradeMatch(BaseModel):
    """A matched pair of paper trade and backtest trade."""

    paper_trade: TradeData
    backtest_trade: TradeData | None = None
    slippage_delta: float | None = None
    pnl_delta: float | None = None
    timing_delta_seconds: float | None = None


class DailyDelta(BaseModel):
    """Daily P&L comparison between paper and backtest."""

    date: date
    paper_pnl: float = 0.0
    backtest_pnl: float = 0.0
    delta: float = 0.0
    paper_trades: int = 0
    backtest_trades: int = 0


class WeeklyDelta(BaseModel):
    """Weekly P&L comparison."""

    week_start: date
    paper_pnl: float = 0.0
    backtest_pnl: float = 0.0
    delta: float = 0.0


class ComparisonReport(BaseModel):
    """Full comparison report between a paper session and a backtest."""

    paper_session_id: int
    backtest_run_id: int

    matched: list[TradeMatch] = Field(default_factory=list)
    paper_only: list[TradeMatch] = Field(default_factory=list)
    backtest_only: list[TradeData] = Field(default_factory=list)

    # Aggregates
    avg_slippage_delta: float | None = None
    avg_pnl_delta: float | None = None
    total_pnl_paper: float = 0.0
    total_pnl_backtest: float = 0.0
    pnl_difference: float = 0.0
    trades_paper: int = 0
    trades_backtest: int = 0
    win_rate_paper: float = 0.0
    win_rate_backtest: float = 0.0
    profit_factor_paper: float | None = None
    profit_factor_backtest: float | None = None

    daily_comparison: list[DailyDelta] = Field(default_factory=list)
    weekly_comparison: list[WeeklyDelta] = Field(default_factory=list)


def compare_sessions(
    session_id: int | None = None,
    backtest_id: int | None = None,
    match_window_seconds: int = 300,
) -> ComparisonReport:
    """Compare a paper trading session against a backtest run.

    If IDs are not provided, uses the most recent of each for the same
    strategy and instrument.
    """
    _setup_django()
    from pyfx.web.dashboard.models import BacktestRun, PaperTrade, PaperTradingSession, Trade

    # --- Resolve session and backtest ---
    if session_id is not None:
        session = PaperTradingSession.objects.get(pk=session_id)
    else:
        session_or_none = PaperTradingSession.objects.order_by("-started_at").first()
        if session_or_none is None:
            msg = "No paper trading sessions found"
            raise ValueError(msg)
        session = session_or_none

    if backtest_id is not None:
        bt_run = BacktestRun.objects.get(pk=backtest_id)
    else:
        bt_run_or_none = (
            BacktestRun.objects
            .filter(
                strategy=session.strategy,
                instrument=session.instrument,
                status=BacktestRun.STATUS_COMPLETED,
            )
            .order_by("-created_at")
            .first()
        )
        if bt_run_or_none is None:
            msg = (
                f"No completed backtest found for {session.strategy} "
                f"on {session.instrument}"
            )
            raise ValueError(msg)
        bt_run = bt_run_or_none

    # --- Load trades ---
    paper_trades = list(
        PaperTrade.objects.filter(session=session, closed_at__isnull=False)
        .order_by("opened_at")
    )
    backtest_trades = list(
        Trade.objects.filter(run=bt_run).order_by("opened_at")
    )

    # Normalise
    p_data = [_paper_to_data(t) for t in paper_trades]
    b_data = [_backtest_to_data(t) for t in backtest_trades]

    # --- Match trades ---
    matched, paper_only, backtest_only = _match_trades(
        p_data, b_data, match_window_seconds,
    )

    # --- Compute aggregates ---
    total_pnl_paper = sum(t.realized_pnl or 0 for t in paper_trades)
    total_pnl_backtest = sum(t.realized_pnl for t in backtest_trades)

    paper_wins = sum(1 for t in paper_trades if (t.realized_pnl or 0) > 0)
    bt_wins = sum(1 for t in backtest_trades if t.realized_pnl > 0)
    win_rate_paper = paper_wins / len(paper_trades) if paper_trades else 0.0
    win_rate_backtest = bt_wins / len(backtest_trades) if backtest_trades else 0.0

    # Profit factors
    p_gross_profit = sum((t.realized_pnl or 0) for t in paper_trades if (t.realized_pnl or 0) > 0)
    p_gross_loss = sum(abs(t.realized_pnl or 0) for t in paper_trades if (t.realized_pnl or 0) < 0)
    b_gross_profit = sum(t.realized_pnl for t in backtest_trades if t.realized_pnl > 0)
    b_gross_loss = sum(abs(t.realized_pnl) for t in backtest_trades if t.realized_pnl < 0)

    pf_paper = p_gross_profit / p_gross_loss if p_gross_loss > 0 else None
    pf_backtest = b_gross_profit / b_gross_loss if b_gross_loss > 0 else None

    # Avg slippage and P&L deltas from matched trades
    slip_deltas = [m.slippage_delta for m in matched if m.slippage_delta is not None]
    pnl_deltas = [m.pnl_delta for m in matched if m.pnl_delta is not None]
    avg_slip = sum(slip_deltas) / len(slip_deltas) if slip_deltas else None
    avg_pnl = sum(pnl_deltas) / len(pnl_deltas) if pnl_deltas else None

    # --- Daily comparison ---
    daily = _compute_daily(paper_trades, backtest_trades)
    weekly = _compute_weekly(daily)

    return ComparisonReport(
        paper_session_id=session.pk,
        backtest_run_id=bt_run.pk,
        matched=matched,
        paper_only=paper_only,
        backtest_only=backtest_only,
        avg_slippage_delta=avg_slip,
        avg_pnl_delta=avg_pnl,
        total_pnl_paper=total_pnl_paper,
        total_pnl_backtest=total_pnl_backtest,
        pnl_difference=total_pnl_paper - total_pnl_backtest,
        trades_paper=len(paper_trades),
        trades_backtest=len(backtest_trades),
        win_rate_paper=win_rate_paper,
        win_rate_backtest=win_rate_backtest,
        profit_factor_paper=pf_paper,
        profit_factor_backtest=pf_backtest,
        daily_comparison=daily,
        weekly_comparison=weekly,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _paper_to_data(trade: object) -> TradeData:
    return TradeData(
        source="paper",
        instrument=trade.instrument,  # type: ignore[attr-defined]
        side=trade.side,  # type: ignore[attr-defined]
        quantity=trade.quantity,  # type: ignore[attr-defined]
        open_price=trade.open_price,  # type: ignore[attr-defined]
        close_price=trade.close_price,  # type: ignore[attr-defined]
        realized_pnl=trade.realized_pnl,  # type: ignore[attr-defined]
        opened_at=str(trade.opened_at),  # type: ignore[attr-defined]
        closed_at=str(trade.closed_at) if trade.closed_at else None,  # type: ignore[attr-defined]
    )


def _backtest_to_data(trade: object) -> TradeData:
    return TradeData(
        source="backtest",
        instrument=trade.instrument,  # type: ignore[attr-defined]
        side=trade.side,  # type: ignore[attr-defined]
        quantity=trade.quantity,  # type: ignore[attr-defined]
        open_price=trade.open_price,  # type: ignore[attr-defined]
        close_price=trade.close_price,  # type: ignore[attr-defined]
        realized_pnl=trade.realized_pnl,  # type: ignore[attr-defined]
        opened_at=str(trade.opened_at),  # type: ignore[attr-defined]
        closed_at=str(trade.closed_at),  # type: ignore[attr-defined]
    )


def _match_trades(
    paper: list[TradeData],
    backtest: list[TradeData],
    window_seconds: int,
) -> tuple[list[TradeMatch], list[TradeMatch], list[TradeData]]:
    """Match paper trades to backtest trades by time proximity and side."""
    from datetime import datetime

    def _parse_dt(s: str) -> datetime:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt

    matched: list[TradeMatch] = []
    paper_only: list[TradeMatch] = []
    bt_used: set[int] = set()

    for p in paper:
        p_time = _parse_dt(p.opened_at)
        best_idx: int | None = None
        best_diff = float("inf")

        for i, b in enumerate(backtest):
            if i in bt_used:
                continue
            if b.side != p.side:
                continue
            b_time = _parse_dt(b.opened_at)
            diff = abs((p_time - b_time).total_seconds())
            if diff <= window_seconds and diff < best_diff:
                best_diff = diff
                best_idx = i

        if best_idx is not None:
            bt_used.add(best_idx)
            b = backtest[best_idx]
            slip = p.open_price - b.open_price if b.open_price else None
            pnl_d = (
                (p.realized_pnl or 0) - (b.realized_pnl or 0)
                if p.realized_pnl is not None and b.realized_pnl is not None
                else None
            )
            matched.append(TradeMatch(
                paper_trade=p,
                backtest_trade=b,
                slippage_delta=slip,
                pnl_delta=pnl_d,
                timing_delta_seconds=best_diff,
            ))
        else:
            paper_only.append(TradeMatch(paper_trade=p))

    backtest_only = [b for i, b in enumerate(backtest) if i not in bt_used]
    return matched, paper_only, backtest_only


def _compute_daily(
    paper_trades: Sequence[object],
    backtest_trades: Sequence[object],
) -> list[DailyDelta]:
    paper_daily: dict[date, tuple[float, int]] = defaultdict(lambda: (0.0, 0))
    bt_daily: dict[date, tuple[float, int]] = defaultdict(lambda: (0.0, 0))

    for t in paper_trades:
        if t.closed_at:  # type: ignore[attr-defined]
            d = t.closed_at.date()  # type: ignore[attr-defined]
            pnl, cnt = paper_daily[d]
            paper_daily[d] = (pnl + (t.realized_pnl or 0), cnt + 1)  # type: ignore[attr-defined]

    for t in backtest_trades:
        d = t.closed_at.date()  # type: ignore[attr-defined]
        pnl, cnt = bt_daily[d]
        bt_daily[d] = (pnl + t.realized_pnl, cnt + 1)  # type: ignore[attr-defined]

    all_dates = sorted(set(paper_daily.keys()) | set(bt_daily.keys()))
    result: list[DailyDelta] = []
    for d in all_dates:
        p_pnl, p_cnt = paper_daily.get(d, (0.0, 0))
        b_pnl, b_cnt = bt_daily.get(d, (0.0, 0))
        result.append(DailyDelta(
            date=d,
            paper_pnl=p_pnl,
            backtest_pnl=b_pnl,
            delta=p_pnl - b_pnl,
            paper_trades=p_cnt,
            backtest_trades=b_cnt,
        ))
    return result


def _compute_weekly(daily: list[DailyDelta]) -> list[WeeklyDelta]:
    weekly: dict[date, tuple[float, float]] = defaultdict(lambda: (0.0, 0.0))
    for d in daily:
        week_start = d.date - timedelta(days=d.date.weekday())
        p, b = weekly[week_start]
        weekly[week_start] = (p + d.paper_pnl, b + d.backtest_pnl)

    result: list[WeeklyDelta] = []
    for ws in sorted(weekly.keys()):
        p, b = weekly[ws]
        result.append(WeeklyDelta(
            week_start=ws, paper_pnl=p, backtest_pnl=b, delta=p - b,
        ))
    return result


def _setup_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pyfx.web.pyfx_web.settings")
    import django

    django.setup()
