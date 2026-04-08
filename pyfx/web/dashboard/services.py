"""Shared business logic for saving results to the Django database."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyfx.core.types import BacktestResult, LiveTradingConfig
    from pyfx.web.dashboard.models import BacktestRun, PaperTradingSession


def save_backtest_result(
    result: BacktestResult,
    existing_run: BacktestRun | None = None,
) -> BacktestRun:
    """Save a BacktestResult to the Django database.

    When *existing_run* is provided the row is updated in place (used by the
    web management command).  Otherwise a new ``BacktestRun`` is created (used
    by the CLI ``--save`` flag).

    In both cases child ``Trade`` and ``EquitySnapshot`` rows are bulk-created.

    Returns the saved ``BacktestRun`` instance.
    """
    from pyfx.web.dashboard.models import BacktestRun, EquitySnapshot, Trade

    cfg_start = result.config.start
    cfg_end = result.config.end
    if cfg_start.tzinfo is None:
        cfg_start = cfg_start.replace(tzinfo=UTC)
    if cfg_end.tzinfo is None:
        cfg_end = cfg_end.replace(tzinfo=UTC)

    if existing_run is not None:
        run = existing_run
        run.total_pnl = result.total_pnl
        run.total_return_pct = result.total_return_pct
        run.num_trades = result.num_trades
        run.win_rate = result.win_rate
        run.max_drawdown_pct = result.max_drawdown_pct
        run.avg_trade_pnl = result.avg_trade_pnl
        run.avg_win = result.avg_win
        run.avg_loss = result.avg_loss
        run.profit_factor = result.profit_factor
        run.duration_seconds = result.duration_seconds
        run.status = BacktestRun.STATUS_COMPLETED
        run.progress_pct = 100
        run.progress_message = "Complete"
        run.save()
    else:
        run = BacktestRun.objects.create(
            strategy=result.config.strategy,
            instrument=result.config.instrument,
            start=cfg_start,
            end=cfg_end,
            bar_type=result.config.bar_type,
            extra_bar_types=result.config.extra_bar_types,
            trade_size=float(result.config.trade_size),
            balance=result.config.balance,
            leverage=result.config.leverage,
            strategy_params=result.config.strategy_params,
            total_pnl=result.total_pnl,
            total_return_pct=result.total_return_pct,
            num_trades=result.num_trades,
            win_rate=result.win_rate,
            max_drawdown_pct=result.max_drawdown_pct,
            avg_trade_pnl=result.avg_trade_pnl,
            avg_win=result.avg_win,
            avg_loss=result.avg_loss,
            profit_factor=result.profit_factor,
            duration_seconds=result.duration_seconds,
        )

    Trade.objects.bulk_create([
        Trade(
            run=run,
            instrument=t.instrument,
            side=t.side,
            quantity=t.quantity,
            open_price=t.open_price,
            close_price=t.close_price,
            realized_pnl=t.realized_pnl,
            realized_return_pct=t.realized_return_pct,
            opened_at=t.opened_at,
            closed_at=t.closed_at,
            duration_seconds=t.duration_seconds,
        )
        for t in result.trades
    ])

    EquitySnapshot.objects.bulk_create([
        EquitySnapshot(run=run, timestamp=ep.timestamp, balance=ep.balance)
        for ep in result.equity_curve
    ])

    return run


# ---------------------------------------------------------------------------
# Paper Trading Services
# ---------------------------------------------------------------------------


def create_paper_session(
    config: LiveTradingConfig,
    account_id: str,
) -> PaperTradingSession:
    """Create a new PaperTradingSession from a LiveTradingConfig."""
    from pyfx.web.dashboard.models import PaperTradingSession

    return PaperTradingSession.objects.create(
        status=PaperTradingSession.STATUS_RUNNING,
        strategy=config.strategy,
        instrument=config.instrument,
        bar_type=config.bar_type,
        started_at=datetime.now(UTC),
        account_currency=config.account_currency,
        account_id=account_id,
        config_json=config.model_dump(mode="json"),
    )


def stop_paper_session(session_id: int) -> None:
    """Mark a PaperTradingSession as stopped."""
    from pyfx.web.dashboard.models import PaperTradingSession

    PaperTradingSession.objects.filter(pk=session_id).update(
        status=PaperTradingSession.STATUS_STOPPED,
        stopped_at=datetime.now(UTC),
    )


def save_paper_trade(
    session_id: int,
    instrument: str,
    side: str,
    quantity: float,
    open_price: float,
    opened_at: datetime,
    close_price: float | None = None,
    realized_pnl: float | None = None,
    realized_return_pct: float | None = None,
    closed_at: datetime | None = None,
    duration_seconds: float | None = None,
    fill_latency_ms: float | None = None,
    slippage_ticks: float | None = None,
    spread_at_entry: float | None = None,
) -> int:
    """Create a PaperTrade record and return its PK."""
    from pyfx.web.dashboard.models import PaperTrade

    trade = PaperTrade.objects.create(
        session_id=session_id,
        instrument=instrument,
        side=side,
        quantity=quantity,
        open_price=open_price,
        opened_at=opened_at,
        close_price=close_price,
        realized_pnl=realized_pnl,
        realized_return_pct=realized_return_pct,
        closed_at=closed_at,
        duration_seconds=duration_seconds,
        fill_latency_ms=fill_latency_ms,
        slippage_ticks=slippage_ticks,
        spread_at_entry=spread_at_entry,
    )
    return trade.pk


def save_session_event(
    session_id: int,
    event_type: str,
    message: str,
    details: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> None:
    """Persist a single SessionEvent."""
    from pyfx.web.dashboard.models import SessionEvent

    SessionEvent.objects.create(
        session_id=session_id,
        timestamp=timestamp or datetime.now(UTC),
        event_type=event_type,
        message=message,
        details_json=details or {},
    )


def save_risk_snapshot(
    session_id: int,
    equity: float,
    daily_pnl: float,
    open_positions: int,
    drawdown_pct: float,
    utilization_pct: float,
    timestamp: datetime | None = None,
) -> None:
    """Persist a periodic risk state snapshot."""
    from pyfx.web.dashboard.models import RiskSnapshot

    RiskSnapshot.objects.create(
        session_id=session_id,
        timestamp=timestamp or datetime.now(UTC),
        equity=equity,
        daily_pnl=daily_pnl,
        open_positions=open_positions,
        drawdown_pct=drawdown_pct,
        utilization_pct=utilization_pct,
    )
