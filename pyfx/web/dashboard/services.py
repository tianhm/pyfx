"""Shared business logic for saving backtest results to the Django database."""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyfx.core.types import BacktestResult
    from pyfx.web.dashboard.models import BacktestRun


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
