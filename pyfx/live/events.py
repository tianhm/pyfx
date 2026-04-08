"""Event persistence bridge: NautilusTrader events -> Django database."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any


def _setup_django_if_needed() -> None:
    """Ensure Django is initialised (idempotent)."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pyfx.web.pyfx_web.settings")
    import django

    django.setup()


def save_session_event(
    session_id: int,
    event_type: str,
    message: str,
    details: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> None:
    """Persist a single event to the SessionEvent table."""
    _setup_django_if_needed()
    from pyfx.web.dashboard.models import SessionEvent

    SessionEvent.objects.create(
        session_id=session_id,
        timestamp=timestamp or datetime.now(UTC),
        event_type=event_type,
        message=message,
        details_json=details or {},
    )


def save_paper_trade_open(
    session_id: int,
    instrument: str,
    side: str,
    quantity: float,
    open_price: float,
    opened_at: datetime,
    fill_latency_ms: float | None = None,
    slippage_ticks: float | None = None,
    spread_at_entry: float | None = None,
) -> int:
    """Create a PaperTrade record when a position is opened. Returns the trade PK."""
    _setup_django_if_needed()
    from pyfx.web.dashboard.models import PaperTrade

    trade = PaperTrade.objects.create(
        session_id=session_id,
        instrument=instrument,
        side=side,
        quantity=quantity,
        open_price=open_price,
        opened_at=opened_at,
        fill_latency_ms=fill_latency_ms,
        slippage_ticks=slippage_ticks,
        spread_at_entry=spread_at_entry,
    )
    return trade.pk


def save_paper_trade_close(
    trade_id: int,
    close_price: float,
    realized_pnl: float,
    realized_return_pct: float,
    closed_at: datetime,
    duration_seconds: float,
) -> None:
    """Update a PaperTrade record when the position is closed."""
    _setup_django_if_needed()
    from pyfx.web.dashboard.models import PaperTrade

    PaperTrade.objects.filter(pk=trade_id).update(
        close_price=close_price,
        realized_pnl=realized_pnl,
        realized_return_pct=realized_return_pct,
        closed_at=closed_at,
        duration_seconds=duration_seconds,
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
    _setup_django_if_needed()
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


def update_session_metrics(
    session_id: int,
    total_pnl: float,
    total_return_pct: float,
    num_trades: int,
    win_rate: float,
    max_drawdown_pct: float,
    profit_factor: float | None,
    avg_trade_pnl: float,
) -> None:
    """Update aggregate metrics on a PaperTradingSession."""
    _setup_django_if_needed()
    from pyfx.web.dashboard.models import PaperTradingSession

    PaperTradingSession.objects.filter(pk=session_id).update(
        total_pnl=total_pnl,
        total_return_pct=total_return_pct,
        num_trades=num_trades,
        win_rate=win_rate,
        max_drawdown_pct=max_drawdown_pct,
        profit_factor=profit_factor,
        avg_trade_pnl=avg_trade_pnl,
    )
