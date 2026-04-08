"""Risk management Actor for live/paper trading sessions.

Plugs into NautilusTrader's event bus as a native Actor, receiving order and
position events without polling.  Manages circuit breakers, daily P&L tracking,
position limits, and dynamic position sizing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.core.message import Event
from nautilus_trader.model.events import (
    OrderFilled,
    PositionClosed,
    PositionOpened,
)


class RiskMonitorConfig(ActorConfig, frozen=True):
    """Configuration for the RiskMonitorActor."""

    max_positions: int = 3
    daily_loss_limit: float = 2000.0
    max_drawdown_pct: float = 10.0
    position_size_pct: float = 2.0
    sizing_method: str = "fixed_fractional"
    session_db_id: int = 0
    starting_equity: float = 100_000.0
    account_currency: str = "USD"
    risk_snapshot_interval_minutes: int = 5


class RiskMonitorActor(Actor):  # type: ignore[misc]
    """NautilusTrader Actor for risk monitoring and event logging.

    Receives fill/position events via the native message bus.  Has direct
    access to ``self.portfolio`` and ``self.cache`` for real-time state.
    """

    def __init__(self, config: RiskMonitorConfig) -> None:
        super().__init__(config)
        self._config = config
        self._daily_pnl: float = 0.0
        self._daily_reset_date: datetime = datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        self._equity_high_water: float = config.starting_equity
        self._trade_count: int = 0
        self._win_count: int = 0
        self._total_pnl: float = 0.0
        self._gross_profit: float = 0.0
        self._gross_loss: float = 0.0
        self._circuit_breaker_triggered: bool = False
        # Map NautilusTrader position_id -> Django PaperTrade PK
        self._position_trade_map: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_start(self) -> None:
        self.log.info("RiskMonitorActor started")
        self.log.info(
            f"  Max positions: {self._config.max_positions}, "
            f"Daily loss limit: {self._config.daily_loss_limit}, "
            f"Max DD: {self._config.max_drawdown_pct}%",
        )

        # Log resolved instrument specs for verification
        for instrument in self.cache.instruments():
            self.log.info(
                f"  Instrument: {instrument.id} "
                f"tick={getattr(instrument, 'price_increment', '?')} "
                f"lot={getattr(instrument, 'lot_size', '?')}",
            )

        # Start periodic risk snapshot timer
        if (
            self._config.session_db_id
            and self._config.risk_snapshot_interval_minutes > 0
        ):
            from datetime import timedelta

            self.clock.set_timer(
                "risk_snapshot",
                interval=timedelta(
                    minutes=self._config.risk_snapshot_interval_minutes,
                ),
                callback=self._on_risk_snapshot_timer,
            )
        # Capture initial snapshot
        self._save_snapshot()

    def on_stop(self) -> None:
        self.log.info("RiskMonitorActor stopped")
        self._save_snapshot()  # Final snapshot
        self._flush_metrics()

    # ------------------------------------------------------------------
    # Event handlers (called by NautilusTrader message bus)
    # ------------------------------------------------------------------

    def on_event(self, event: Event) -> None:
        if isinstance(event, OrderFilled):
            self._on_order_filled(event)
        elif isinstance(event, PositionOpened):
            self._on_position_opened(event)
        elif isinstance(event, PositionClosed):
            self._on_position_closed(event)

    def _on_order_filled(self, event: OrderFilled) -> None:
        self.log.info(
            f"FILL: {event.order_side.name} {event.last_qty} "
            f"{event.instrument_id} @ {event.last_px}",
        )
        self._maybe_reset_daily()
        self._log_event(
            "order_filled",
            f"{event.order_side.name} {event.last_qty} {event.instrument_id} @ {event.last_px}",
            {
                "order_side": event.order_side.name,
                "quantity": str(event.last_qty),
                "price": str(event.last_px),
                "instrument": str(event.instrument_id),
            },
        )

    def _on_position_opened(self, event: PositionOpened) -> None:
        pos = event.position
        self.log.info(
            f"POSITION OPENED: {pos.side.name} {pos.quantity} {pos.instrument_id}",
        )
        # Persist to DB
        if self._config.session_db_id:
            from pyfx.live.events import save_paper_trade_open

            trade_pk = save_paper_trade_open(
                session_id=self._config.session_db_id,
                instrument=str(pos.instrument_id),
                side=pos.entry.name,
                quantity=float(pos.quantity),
                open_price=float(pos.avg_px_open),
                opened_at=datetime.now(UTC),
            )
            self._position_trade_map[str(pos.id)] = trade_pk

        self._log_event(
            "position_opened",
            f"{pos.side.name} {pos.quantity} {pos.instrument_id} @ {pos.avg_px_open}",
            {"position_id": str(pos.id)},
        )
        self._check_position_limit()

    def _on_position_closed(self, event: PositionClosed) -> None:
        pos = event.position
        pnl = float(pos.realized_pnl)
        self.log.info(
            f"POSITION CLOSED: {pos.instrument_id} P&L={pnl:+.2f} {self._config.account_currency}",
        )

        self._daily_pnl += pnl
        self._total_pnl += pnl
        self._trade_count += 1
        if pnl > 0:
            self._win_count += 1
            self._gross_profit += pnl
        else:
            self._gross_loss += abs(pnl)

        # Close in DB
        if self._config.session_db_id:
            trade_pk = self._position_trade_map.pop(str(pos.id), 0)
            if trade_pk:  # pragma: no branch
                from pyfx.live.events import save_paper_trade_close

                duration = (
                    float(pos.duration_ns) / 1e9 if pos.duration_ns else 0.0
                )
                entry_value = float(pos.avg_px_open) * float(pos.quantity)
                return_pct = (pnl / entry_value * 100) if entry_value else 0.0
                save_paper_trade_close(
                    trade_id=trade_pk,
                    close_price=float(pos.avg_px_close),
                    realized_pnl=pnl,
                    realized_return_pct=return_pct,
                    closed_at=datetime.now(UTC),
                    duration_seconds=duration,
                )

        self._log_event(
            "position_closed",
            f"{pos.instrument_id} P&L={pnl:+.2f} {self._config.account_currency}",
            {
                "realized_pnl": pnl,
                "daily_pnl": self._daily_pnl,
                "total_trades": self._trade_count,
            },
        )

        self._check_circuit_breakers()
        self._flush_metrics()

    # ------------------------------------------------------------------
    # Risk checks
    # ------------------------------------------------------------------

    def _check_position_limit(self) -> None:
        open_count = len(self.cache.positions_open())
        if open_count > self._config.max_positions:
            self.log.warning(
                f"Position limit reached ({open_count}/{self._config.max_positions})",
            )
            self._log_event(
                "risk_warning",
                f"Position limit: {open_count}/{self._config.max_positions}",
            )

    def _check_circuit_breakers(self) -> None:
        if self._circuit_breaker_triggered:
            return

        # Daily loss check
        if self._daily_pnl <= -self._config.daily_loss_limit:
            self._trigger_circuit_breaker(
                f"Daily loss limit breached: {self._daily_pnl:+.2f} "
                f"(limit: -{self._config.daily_loss_limit:.2f})",
            )
            return

        # Warning at 75%
        if self._daily_pnl <= -self._config.daily_loss_limit * 0.75:
            self.log.warning(
                f"Daily loss at 75% of limit: {self._daily_pnl:+.2f}",
            )
            self._log_event(
                "risk_warning",
                f"Daily loss at 75%: {self._daily_pnl:+.2f} / -{self._config.daily_loss_limit:.2f}",
            )

        # Max drawdown check
        current_equity = self._config.starting_equity + self._total_pnl
        if current_equity > self._equity_high_water:
            self._equity_high_water = current_equity
        drawdown_pct = (
            (self._equity_high_water - current_equity) / self._equity_high_water * 100
            if self._equity_high_water > 0
            else 0.0
        )
        if drawdown_pct >= self._config.max_drawdown_pct:
            self._trigger_circuit_breaker(
                f"Max drawdown breached: {drawdown_pct:.1f}% "
                f"(limit: {self._config.max_drawdown_pct}%)",
            )

    def _trigger_circuit_breaker(self, reason: str) -> None:
        self._circuit_breaker_triggered = True
        self.log.error(f"CIRCUIT BREAKER: {reason}")
        self._log_event("circuit_breaker", reason)

        # Cancel all open orders and close all positions
        for order in self.cache.orders_open():
            self.cancel_order(order)
        for position in self.cache.positions_open():
            self.close_position(position)

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def compute_position_size(
        self,
        stop_distance: float,
        point_value: float = 1.0,
        atr_value: float | None = None,
    ) -> Decimal:
        """Compute position size based on the configured sizing method.

        For ``fixed_fractional``: uses ``stop_distance`` (in price units).
        For ``atr_based``: uses ``atr_value`` (in price units) if provided,
        otherwise falls back to ``stop_distance``.
        """
        current_equity = self._config.starting_equity + self._total_pnl
        risk_amount = current_equity * (self._config.position_size_pct / 100)

        if self._config.sizing_method == "atr_based" and atr_value is not None:
            effective_stop = atr_value
        else:
            effective_stop = stop_distance

        if effective_stop <= 0 or point_value <= 0:
            return Decimal("0")

        raw_size = risk_amount / (effective_stop * point_value)
        # Clamp to max position size
        max_size = float(self._config.starting_equity)  # conservative max
        clamped = min(raw_size, max_size)
        # Round to whole units
        return Decimal(str(int(clamped)))

    # ------------------------------------------------------------------
    # Daily reset
    # ------------------------------------------------------------------

    def _maybe_reset_daily(self) -> None:
        """Reset daily counters if we've crossed UTC midnight."""
        now = datetime.now(UTC)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if today > self._daily_reset_date:
            self._daily_pnl = 0.0
            self._daily_reset_date = today
            self._circuit_breaker_triggered = False
            self.log.info("Daily risk counters reset")

    # ------------------------------------------------------------------
    # Risk snapshots
    # ------------------------------------------------------------------

    def _on_risk_snapshot_timer(self, event: object) -> None:
        """Handle periodic timer event for risk snapshots."""
        self._save_snapshot()

    def _save_snapshot(self) -> None:
        """Persist a point-in-time risk snapshot to the database."""
        if not self._config.session_db_id:
            return
        from pyfx.live.events import save_risk_snapshot

        current_equity = self._config.starting_equity + self._total_pnl
        if current_equity > self._equity_high_water:
            self._equity_high_water = current_equity
        dd_pct = (
            (self._equity_high_water - current_equity) / self._equity_high_water * 100
            if self._equity_high_water > 0
            else 0.0
        )
        open_positions = len(self.cache.positions_open())
        utilization = (
            open_positions / self._config.max_positions * 100
            if self._config.max_positions > 0
            else 0.0
        )
        save_risk_snapshot(
            session_id=self._config.session_db_id,
            equity=current_equity,
            daily_pnl=self._daily_pnl,
            open_positions=open_positions,
            drawdown_pct=max(dd_pct, 0.0),
            utilization_pct=utilization,
        )

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _log_event(
        self,
        event_type: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        if not self._config.session_db_id:
            return
        from pyfx.live.events import save_session_event

        save_session_event(
            session_id=self._config.session_db_id,
            event_type=event_type,
            message=message,
            details={k: _serialise(v) for k, v in (details or {}).items()},
        )

    def _flush_metrics(self) -> None:
        """Write aggregate metrics to the PaperTradingSession row."""
        if not self._config.session_db_id:
            return
        from pyfx.live.events import update_session_metrics

        win_rate = (
            self._win_count / self._trade_count if self._trade_count else 0.0
        )
        pf = (
            self._gross_profit / self._gross_loss
            if self._gross_loss > 0
            else None
        )
        avg_pnl = (
            self._total_pnl / self._trade_count if self._trade_count else 0.0
        )
        current_equity = self._config.starting_equity + self._total_pnl
        dd_pct = (
            (self._equity_high_water - current_equity) / self._equity_high_water * 100
            if self._equity_high_water > 0
            else 0.0
        )
        update_session_metrics(
            session_id=self._config.session_db_id,
            total_pnl=self._total_pnl,
            total_return_pct=self._total_pnl / self._config.starting_equity * 100,
            num_trades=self._trade_count,
            win_rate=win_rate,
            max_drawdown_pct=max(dd_pct, 0.0),
            profit_factor=pf,
            avg_trade_pnl=avg_pnl,
        )

    @property
    def circuit_breaker_triggered(self) -> bool:
        return self._circuit_breaker_triggered

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def total_pnl(self) -> float:
        return self._total_pnl

    @property
    def trade_count(self) -> int:
        return self._trade_count


def _serialise(v: object) -> object:
    """Make a value JSON-safe."""
    if isinstance(v, Decimal):
        return str(v)
    return v
