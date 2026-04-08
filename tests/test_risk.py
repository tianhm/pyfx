"""Tests for pyfx.live.risk – RiskMonitorActor logic."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import msgspec
import pytest

# ---------------------------------------------------------------------------
# Mock nautilus_trader before importing the module under test.
#
# We save the real module references, install mocks, import the module under
# test (which re-defines the classes against our lightweight stubs), then
# RESTORE the real modules so other test files that need the real
# nautilus_trader package are unaffected.
# ---------------------------------------------------------------------------

_NT_KEYS = [
    "nautilus_trader",
    "nautilus_trader.common",
    "nautilus_trader.common.actor",
    "nautilus_trader.config",
    "nautilus_trader.core",
    "nautilus_trader.core.message",
    "nautilus_trader.model",
    "nautilus_trader.model.events",
]

# 1. Save originals
_saved: dict[str, object] = {}
for _k in _NT_KEYS:
    if _k in sys.modules:
        _saved[_k] = sys.modules[_k]

# 2. Install mocks
_nt_actor = MagicMock()
_nt_config = MagicMock()
_nt_message = MagicMock()
_nt_events = MagicMock()

sys.modules["nautilus_trader"] = MagicMock()  # type: ignore[assignment]
sys.modules["nautilus_trader.common"] = MagicMock()  # type: ignore[assignment]
sys.modules["nautilus_trader.common.actor"] = _nt_actor  # type: ignore[assignment]
sys.modules["nautilus_trader.config"] = _nt_config  # type: ignore[assignment]
sys.modules["nautilus_trader.core"] = MagicMock()  # type: ignore[assignment]
sys.modules["nautilus_trader.core.message"] = _nt_message  # type: ignore[assignment]
sys.modules["nautilus_trader.model"] = MagicMock()  # type: ignore[assignment]
sys.modules["nautilus_trader.model.events"] = _nt_events  # type: ignore[assignment]

# Provide sentinel classes for isinstance checks
_nt_events.OrderFilled = type("OrderFilled", (), {})
_nt_events.PositionOpened = type("PositionOpened", (), {})
_nt_events.PositionClosed = type("PositionClosed", (), {})

# ActorConfig must be a real msgspec.Struct subclass so RiskMonitorConfig
# can inherit from it and get proper __init__ / frozen behaviour.
class _MockActorConfig(msgspec.Struct, frozen=True):
    component_id: str = ""

_nt_config.ActorConfig = _MockActorConfig

# Actor base class – just enough for super().__init__
_MockActor = type("Actor", (), {"__init__": lambda self, config: None})
_nt_actor.Actor = _MockActor

# 3. Force-reload the module under test against our mocks
if "pyfx.live.risk" in sys.modules:
    del sys.modules["pyfx.live.risk"]

from pyfx.live.risk import RiskMonitorActor, RiskMonitorConfig, _serialise  # noqa: E402

# 4. Restore real modules (so test_ib_adapter etc. see the real package)
for _k in _NT_KEYS:
    if _k in _saved:
        sys.modules[_k] = _saved[_k]  # type: ignore[assignment]
    else:
        sys.modules.pop(_k, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_actor(
    *,
    max_positions: int = 3,
    daily_loss_limit: float = 2000.0,
    max_drawdown_pct: float = 10.0,
    position_size_pct: float = 2.0,
    sizing_method: str = "fixed_fractional",
    session_db_id: int = 0,
    starting_equity: float = 100_000.0,
) -> RiskMonitorActor:
    config = RiskMonitorConfig(
        max_positions=max_positions,
        daily_loss_limit=daily_loss_limit,
        max_drawdown_pct=max_drawdown_pct,
        position_size_pct=position_size_pct,
        sizing_method=sizing_method,
        session_db_id=session_db_id,
        starting_equity=starting_equity,
    )
    actor = RiskMonitorActor(config)
    # Provide mock log and cache objects
    actor.log = MagicMock()
    actor.cache = MagicMock()
    actor.cancel_order = MagicMock()
    actor.close_position = MagicMock()
    return actor


def _make_position(
    *,
    instrument_id: str = "EUR/USD.IDEALPRO",
    side_name: str = "LONG",
    entry_name: str = "BUY",
    quantity: float = 100_000,
    avg_px_open: float = 1.10000,
    avg_px_close: float = 1.10050,
    realized_pnl: float = 50.0,
    duration_ns: int = 3_600_000_000_000,
    position_id: str = "P-001",
) -> MagicMock:
    pos = MagicMock()
    pos.instrument_id = instrument_id
    pos.side.name = side_name
    pos.entry.name = entry_name
    pos.quantity = quantity
    pos.avg_px_open = avg_px_open
    pos.avg_px_close = avg_px_close
    pos.realized_pnl = realized_pnl
    pos.duration_ns = duration_ns
    pos.id = position_id
    return pos


def _make_order_filled_event(
    *,
    order_side_name: str = "BUY",
    last_qty: float = 100_000,
    instrument_id: str = "EUR/USD.IDEALPRO",
    last_px: float = 1.10000,
) -> object:
    event = _nt_events.OrderFilled()
    event.order_side = MagicMock()
    event.order_side.name = order_side_name
    event.last_qty = last_qty
    event.instrument_id = instrument_id
    event.last_px = last_px
    return event


# ---------------------------------------------------------------------------
# Tests: compute_position_size
# ---------------------------------------------------------------------------


class TestComputePositionSize:
    def test_fixed_fractional_basic(self) -> None:
        actor = _make_actor(starting_equity=100_000, position_size_pct=2.0)
        # risk_amount = 100_000 * 0.02 = 2_000
        # stop_distance = 0.001, point_value = 1.0
        # raw_size = 2000 / (0.001 * 1.0) = 2_000_000
        # clamped to max 100_000
        result = actor.compute_position_size(stop_distance=0.001)
        assert result == Decimal("100000")

    def test_fixed_fractional_small_stop(self) -> None:
        actor = _make_actor(starting_equity=50_000, position_size_pct=1.0)
        # risk = 50_000 * 0.01 = 500
        # raw = 500 / (0.01 * 1.0) = 50_000 -> clamped to 50_000
        result = actor.compute_position_size(stop_distance=0.01)
        assert result == Decimal("50000")

    def test_zero_stop_distance_returns_zero(self) -> None:
        actor = _make_actor()
        result = actor.compute_position_size(stop_distance=0.0)
        assert result == Decimal("0")

    def test_negative_stop_distance_returns_zero(self) -> None:
        actor = _make_actor()
        result = actor.compute_position_size(stop_distance=-1.0)
        assert result == Decimal("0")

    def test_zero_point_value_returns_zero(self) -> None:
        actor = _make_actor()
        result = actor.compute_position_size(stop_distance=0.01, point_value=0.0)
        assert result == Decimal("0")

    def test_atr_based_with_atr_value(self) -> None:
        actor = _make_actor(sizing_method="atr_based", starting_equity=100_000)
        # risk = 100_000 * 0.02 = 2_000
        # effective_stop = atr_value = 0.005
        # raw = 2000 / (0.005 * 1.0) = 400_000 -> clamped to 100_000
        result = actor.compute_position_size(
            stop_distance=0.001, atr_value=0.005,
        )
        assert result == Decimal("100000")

    def test_atr_based_falls_back_to_stop_distance(self) -> None:
        actor = _make_actor(sizing_method="atr_based", starting_equity=100_000)
        # No atr_value -> uses stop_distance
        result = actor.compute_position_size(stop_distance=0.001)
        assert result == Decimal("100000")

    def test_fixed_fractional_ignores_atr_value(self) -> None:
        actor = _make_actor(sizing_method="fixed_fractional")
        result1 = actor.compute_position_size(stop_distance=0.001)
        result2 = actor.compute_position_size(stop_distance=0.001, atr_value=0.005)
        assert result1 == result2

    def test_position_size_accounts_for_pnl(self) -> None:
        actor = _make_actor(starting_equity=100_000, position_size_pct=2.0)
        actor._total_pnl = -10_000  # equity now 90_000
        # risk = 90_000 * 0.02 = 1_800
        # raw = 1_800 / (1.0 * 1.0) = 1800
        result = actor.compute_position_size(stop_distance=1.0)
        assert result == Decimal("1800")

    def test_position_size_with_point_value(self) -> None:
        actor = _make_actor(starting_equity=100_000, position_size_pct=2.0)
        # risk = 2_000
        # raw = 2000 / (10.0 * 5.0) = 40
        result = actor.compute_position_size(stop_distance=10.0, point_value=5.0)
        assert result == Decimal("40")


# ---------------------------------------------------------------------------
# Tests: circuit breaker logic
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_daily_loss_limit_triggers(self) -> None:
        actor = _make_actor(daily_loss_limit=1000.0, session_db_id=0)
        actor._daily_pnl = -1000.0
        actor._check_circuit_breakers()
        assert actor.circuit_breaker_triggered is True
        actor.log.error.assert_called_once()

    def test_below_limit_does_not_trigger(self) -> None:
        actor = _make_actor(daily_loss_limit=1000.0)
        actor._daily_pnl = -500.0
        actor._check_circuit_breakers()
        assert actor.circuit_breaker_triggered is False

    def test_75_percent_warning(self) -> None:
        actor = _make_actor(daily_loss_limit=1000.0)
        actor._daily_pnl = -800.0  # 80% of limit
        actor._check_circuit_breakers()
        assert actor.circuit_breaker_triggered is False
        actor.log.warning.assert_called_once()

    def test_max_drawdown_triggers(self) -> None:
        actor = _make_actor(
            starting_equity=100_000,
            max_drawdown_pct=10.0,
            daily_loss_limit=999_999,  # high to not trigger daily
        )
        # Lose 10_000 -> 10% DD
        actor._total_pnl = -10_000.0
        actor._equity_high_water = 100_000.0
        actor._check_circuit_breakers()
        assert actor.circuit_breaker_triggered is True

    def test_equity_high_water_updates(self) -> None:
        actor = _make_actor(starting_equity=100_000)
        actor._total_pnl = 5_000.0  # equity = 105_000
        actor._equity_high_water = 100_000.0
        actor._check_circuit_breakers()
        assert actor._equity_high_water == 105_000.0

    def test_already_triggered_skips(self) -> None:
        actor = _make_actor(daily_loss_limit=100.0)
        actor._circuit_breaker_triggered = True
        actor._daily_pnl = -999_999.0
        actor._check_circuit_breakers()
        # Should not log again since already triggered
        actor.log.error.assert_not_called()

    def test_trigger_cancels_orders_and_closes_positions(self) -> None:
        actor = _make_actor(daily_loss_limit=100.0, session_db_id=0)
        mock_order = MagicMock()
        mock_position = MagicMock()
        actor.cache.orders_open.return_value = [mock_order]
        actor.cache.positions_open.return_value = [mock_position]
        actor._daily_pnl = -100.0
        actor._check_circuit_breakers()
        actor.cancel_order.assert_called_once_with(mock_order)
        actor.close_position.assert_called_once_with(mock_position)


# ---------------------------------------------------------------------------
# Tests: daily reset
# ---------------------------------------------------------------------------


class TestDailyReset:
    def test_reset_after_midnight(self) -> None:
        actor = _make_actor()
        actor._daily_pnl = -500.0
        actor._circuit_breaker_triggered = True
        # Set reset date to yesterday
        yesterday = datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0,
        ) - timedelta(days=1)
        actor._daily_reset_date = yesterday
        actor._maybe_reset_daily()
        assert actor._daily_pnl == 0.0
        assert actor._circuit_breaker_triggered is False

    def test_no_reset_same_day(self) -> None:
        actor = _make_actor()
        actor._daily_pnl = -500.0
        actor._circuit_breaker_triggered = True
        # Reset date is today
        today = datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        actor._daily_reset_date = today
        actor._maybe_reset_daily()
        assert actor._daily_pnl == -500.0
        assert actor._circuit_breaker_triggered is True


# ---------------------------------------------------------------------------
# Tests: on_event dispatch
# ---------------------------------------------------------------------------


class TestOnEvent:
    def test_dispatches_order_filled(self) -> None:
        actor = _make_actor()
        event = _make_order_filled_event()
        actor.on_event(event)
        actor.log.info.assert_called()

    def test_dispatches_position_opened(self) -> None:
        actor = _make_actor()
        actor.cache.positions_open.return_value = []
        event = _nt_events.PositionOpened()
        event.position = _make_position()
        actor.on_event(event)
        actor.log.info.assert_called()

    def test_dispatches_position_closed(self) -> None:
        actor = _make_actor()
        actor.cache.orders_open.return_value = []
        actor.cache.positions_open.return_value = []
        event = _nt_events.PositionClosed()
        event.position = _make_position(realized_pnl=100.0)
        actor.on_event(event)
        assert actor.trade_count == 1
        assert actor.total_pnl == 100.0

    def test_ignores_unknown_events(self) -> None:
        actor = _make_actor()
        event = MagicMock()
        actor.on_event(event)
        # No crash, no log

    def test_position_closed_loss(self) -> None:
        actor = _make_actor()
        actor.cache.orders_open.return_value = []
        actor.cache.positions_open.return_value = []
        event = _nt_events.PositionClosed()
        event.position = _make_position(realized_pnl=-200.0)
        actor.on_event(event)
        assert actor._win_count == 0
        assert actor._gross_loss == 200.0
        assert actor._daily_pnl == -200.0

    def test_position_closed_win(self) -> None:
        actor = _make_actor()
        actor.cache.orders_open.return_value = []
        actor.cache.positions_open.return_value = []
        event = _nt_events.PositionClosed()
        event.position = _make_position(realized_pnl=300.0)
        actor.on_event(event)
        assert actor._win_count == 1
        assert actor._gross_profit == 300.0


# ---------------------------------------------------------------------------
# Tests: position limit check
# ---------------------------------------------------------------------------


class TestPositionLimit:
    def test_warns_when_exceeded(self) -> None:
        actor = _make_actor(max_positions=2)
        actor.cache.positions_open.return_value = [1, 2, 3]  # 3 > 2
        actor._check_position_limit()
        actor.log.warning.assert_called_once()

    def test_no_warning_within_limit(self) -> None:
        actor = _make_actor(max_positions=3)
        actor.cache.positions_open.return_value = [1, 2]
        actor._check_position_limit()
        actor.log.warning.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: DB persistence (session_db_id > 0)
# ---------------------------------------------------------------------------


class TestPersistence:
    @patch("pyfx.live.events.save_session_event")
    @patch("pyfx.live.events.save_paper_trade_open", return_value=42)
    def test_position_opened_saves_to_db(
        self, mock_save: MagicMock, mock_event: MagicMock,
    ) -> None:
        actor = _make_actor(session_db_id=1)
        actor.cache.positions_open.return_value = []
        event = _nt_events.PositionOpened()
        event.position = _make_position(position_id="P-001")
        actor._on_position_opened(event)
        mock_save.assert_called_once()
        assert actor._position_trade_map["P-001"] == 42

    @patch("pyfx.live.events.save_paper_trade_close")
    @patch("pyfx.live.events.update_session_metrics")
    @patch("pyfx.live.events.save_session_event")
    def test_position_closed_updates_db(
        self,
        mock_event: MagicMock,
        mock_metrics: MagicMock,
        mock_close: MagicMock,
    ) -> None:
        actor = _make_actor(session_db_id=1)
        actor.cache.orders_open.return_value = []
        actor.cache.positions_open.return_value = []
        actor._position_trade_map["P-001"] = 42
        event = _nt_events.PositionClosed()
        event.position = _make_position(
            position_id="P-001", realized_pnl=100.0,
        )
        actor._on_position_closed(event)
        mock_close.assert_called_once()
        assert mock_close.call_args.kwargs["trade_id"] == 42
        mock_metrics.assert_called_once()

    def test_no_db_write_when_session_id_zero(self) -> None:
        actor = _make_actor(session_db_id=0)
        actor.cache.positions_open.return_value = []
        event = _nt_events.PositionOpened()
        event.position = _make_position()
        # Should not crash or import events module
        actor._on_position_opened(event)

    @patch("pyfx.live.events.save_session_event")
    def test_log_event_with_details(self, mock_event: MagicMock) -> None:
        actor = _make_actor(session_db_id=1)
        actor._log_event("test_type", "test message", {"key": Decimal("1.5")})
        mock_event.assert_called_once()
        call_kwargs = mock_event.call_args.kwargs
        assert call_kwargs["details"]["key"] == "1.5"

    @patch("pyfx.live.events.update_session_metrics")
    @patch("pyfx.live.events.save_session_event")
    def test_flush_metrics_computes_correctly(
        self,
        mock_event: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        actor = _make_actor(session_db_id=1, starting_equity=100_000)
        actor._trade_count = 10
        actor._win_count = 6
        actor._total_pnl = 500.0
        actor._gross_profit = 1200.0
        actor._gross_loss = 700.0
        actor._equity_high_water = 100_500.0

        actor._flush_metrics()

        mock_metrics.assert_called_once()
        kwargs = mock_metrics.call_args.kwargs
        assert kwargs["num_trades"] == 10
        assert kwargs["win_rate"] == pytest.approx(0.6)
        assert kwargs["profit_factor"] == pytest.approx(1200 / 700)
        assert kwargs["total_pnl"] == 500.0
        assert kwargs["avg_trade_pnl"] == pytest.approx(50.0)

    @patch("pyfx.live.events.update_session_metrics")
    @patch("pyfx.live.events.save_session_event")
    def test_flush_metrics_no_trades(
        self,
        mock_event: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        actor = _make_actor(session_db_id=1)
        actor._flush_metrics()
        kwargs = mock_metrics.call_args.kwargs
        assert kwargs["win_rate"] == 0.0
        assert kwargs["profit_factor"] is None
        assert kwargs["avg_trade_pnl"] == 0.0


# ---------------------------------------------------------------------------
# Tests: lifecycle hooks
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_on_start_logs(self) -> None:
        actor = _make_actor()
        actor.clock = MagicMock()
        actor.on_start()
        # Logs info about config + instruments (empty mock list)
        assert actor.log.info.call_count >= 2

    def test_on_start_logs_instruments(self) -> None:
        actor = _make_actor()
        actor.clock = MagicMock()
        mock_inst = MagicMock()
        mock_inst.id = "EUR/USD.IDEALPRO"
        actor.cache.instruments.return_value = [mock_inst]
        actor.on_start()
        # Should log instrument specs
        logged = [str(c) for c in actor.log.info.call_args_list]
        assert any("EUR/USD" in s for s in logged)

    @patch("pyfx.live.events.save_risk_snapshot")
    def test_on_start_sets_timer(self, mock_snap: MagicMock) -> None:
        actor = _make_actor(session_db_id=1)
        actor.clock = MagicMock()
        actor.on_start()
        actor.clock.set_timer.assert_called_once()
        args, kwargs = actor.clock.set_timer.call_args
        timer_name = args[0] if args else kwargs.get("name", "")
        assert timer_name == "risk_snapshot"
        # Initial snapshot saved
        mock_snap.assert_called_once()

    @patch("pyfx.live.events.save_risk_snapshot")
    def test_on_start_no_timer_when_interval_zero(self, mock_snap: MagicMock) -> None:
        actor = _make_actor(session_db_id=1)
        actor._config = RiskMonitorConfig(
            session_db_id=1, risk_snapshot_interval_minutes=0,
        )
        actor.clock = MagicMock()
        actor.on_start()
        actor.clock.set_timer.assert_not_called()

    @patch("pyfx.live.events.save_risk_snapshot")
    @patch("pyfx.live.events.update_session_metrics")
    @patch("pyfx.live.events.save_session_event")
    def test_on_stop_flushes(
        self,
        mock_event: MagicMock,
        mock_metrics: MagicMock,
        mock_snap: MagicMock,
    ) -> None:
        actor = _make_actor(session_db_id=1)
        actor.on_stop()
        actor.log.info.assert_called_once()
        mock_metrics.assert_called_once()
        mock_snap.assert_called_once()

    def test_on_stop_no_db_when_id_zero(self) -> None:
        actor = _make_actor(session_db_id=0)
        actor.on_stop()
        actor.log.info.assert_called_once()

    @patch("pyfx.live.events.save_risk_snapshot")
    def test_save_snapshot_computes_metrics(self, mock_snap: MagicMock) -> None:
        actor = _make_actor(session_db_id=1, starting_equity=100_000)
        actor._total_pnl = -500.0
        actor._daily_pnl = -200.0
        actor.cache.positions_open.return_value = [MagicMock(), MagicMock()]
        actor._save_snapshot()
        mock_snap.assert_called_once()
        call_kw = mock_snap.call_args[1]
        assert call_kw["equity"] == 99_500.0
        assert call_kw["daily_pnl"] == -200.0
        assert call_kw["open_positions"] == 2

    @patch("pyfx.live.events.save_risk_snapshot")
    def test_save_snapshot_updates_high_water(self, mock_snap: MagicMock) -> None:
        actor = _make_actor(session_db_id=1, starting_equity=100_000)
        actor._total_pnl = 500.0  # Positive P&L -> equity exceeds high-water
        actor.cache.positions_open.return_value = []
        actor._save_snapshot()
        assert actor._equity_high_water == 100_500.0

    @patch("pyfx.live.events.save_risk_snapshot")
    def test_save_snapshot_skips_when_no_session(self, mock_snap: MagicMock) -> None:
        actor = _make_actor(session_db_id=0)
        actor._save_snapshot()
        mock_snap.assert_not_called()

    def test_on_risk_snapshot_timer_delegates(self) -> None:
        actor = _make_actor(session_db_id=0)
        actor._save_snapshot = MagicMock()  # type: ignore[method-assign]
        actor._on_risk_snapshot_timer(MagicMock())
        actor._save_snapshot.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: properties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_circuit_breaker_triggered(self) -> None:
        actor = _make_actor()
        assert actor.circuit_breaker_triggered is False
        actor._circuit_breaker_triggered = True
        assert actor.circuit_breaker_triggered is True

    def test_daily_pnl(self) -> None:
        actor = _make_actor()
        assert actor.daily_pnl == 0.0
        actor._daily_pnl = -100.0
        assert actor.daily_pnl == -100.0

    def test_total_pnl(self) -> None:
        actor = _make_actor()
        assert actor.total_pnl == 0.0

    def test_trade_count(self) -> None:
        actor = _make_actor()
        assert actor.trade_count == 0


# ---------------------------------------------------------------------------
# Tests: _serialise helper
# ---------------------------------------------------------------------------


class TestSerialise:
    def test_decimal_to_string(self) -> None:
        assert _serialise(Decimal("1.5")) == "1.5"

    def test_string_passthrough(self) -> None:
        assert _serialise("hello") == "hello"

    def test_int_passthrough(self) -> None:
        assert _serialise(42) == 42

    def test_float_passthrough(self) -> None:
        assert _serialise(3.14) == 3.14

    def test_none_passthrough(self) -> None:
        assert _serialise(None) is None


# ---------------------------------------------------------------------------
# Tests: RiskMonitorConfig defaults
# ---------------------------------------------------------------------------


class TestRiskMonitorConfig:
    def test_defaults(self) -> None:
        config = RiskMonitorConfig()
        assert config.max_positions == 3
        assert config.daily_loss_limit == 2000.0
        assert config.max_drawdown_pct == 10.0
        assert config.position_size_pct == 2.0
        assert config.sizing_method == "fixed_fractional"
        assert config.session_db_id == 0
        assert config.starting_equity == 100_000.0
        assert config.account_currency == "USD"
        assert config.risk_snapshot_interval_minutes == 5

    def test_custom_values(self) -> None:
        config = RiskMonitorConfig(
            max_positions=5,
            daily_loss_limit=5000.0,
            session_db_id=42,
        )
        assert config.max_positions == 5
        assert config.daily_loss_limit == 5000.0
        assert config.session_db_id == 42
