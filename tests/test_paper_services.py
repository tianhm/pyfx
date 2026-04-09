"""Tests for paper trading service functions in pyfx.web.dashboard.services."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from django.test import TestCase

from pyfx.core.types import LiveTradingConfig
from pyfx.web.dashboard.models import (
    PaperTrade,
    PaperTradingSession,
    RiskSnapshot,
    SessionEvent,
)
from pyfx.web.dashboard.services import (
    create_paper_session,
    save_paper_trade,
    save_risk_snapshot,
    save_session_event,
    stop_paper_session,
)


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


class CreatePaperSessionTests(TestCase):
    def test_create_session_from_config(self) -> None:
        config = LiveTradingConfig(
            strategy="coban_reborn",
            instrument="XAU/USD",
            bar_type="1-MINUTE-LAST-EXTERNAL",
            trade_size=Decimal("100000"),
            account_currency="USD",
        )
        session = create_paper_session(config, account_id="DU1234567")

        assert session.pk is not None
        assert session.status == PaperTradingSession.STATUS_RUNNING
        assert session.strategy == "coban_reborn"
        assert session.instrument == "XAU/USD"
        assert session.bar_type == "1-MINUTE-LAST-EXTERNAL"
        assert session.account_currency == "USD"
        assert session.account_id == "DU1234567"
        assert session.started_at is not None
        assert session.config_json is not None

    def test_create_session_config_json_contains_fields(self) -> None:
        config = LiveTradingConfig(
            strategy="sample_sma",
            instrument="EUR/USD",
            bar_type="5-MINUTE-LAST-EXTERNAL",
            extra_bar_types=["15-MINUTE-LAST-EXTERNAL"],
            strategy_params={"fast_period": 10},
            trade_size=Decimal("50000"),
            account_currency="EUR",
        )
        session = create_paper_session(config, account_id="DU9999999")

        assert session.config_json["strategy"] == "sample_sma"
        assert session.config_json["instrument"] == "EUR/USD"
        assert session.config_json["extra_bar_types"] == ["15-MINUTE-LAST-EXTERNAL"]
        assert session.config_json["strategy_params"] == {"fast_period": 10}

    def test_create_session_persists(self) -> None:
        config = LiveTradingConfig(
            strategy="coban_reborn",
            instrument="XAU/USD",
        )
        session = create_paper_session(config, account_id="DU0000001")

        reloaded = PaperTradingSession.objects.get(pk=session.pk)
        assert reloaded.strategy == "coban_reborn"
        assert reloaded.status == PaperTradingSession.STATUS_RUNNING


class StopPaperSessionTests(TestCase):
    def test_stop_session(self) -> None:
        session = _create_session(status=PaperTradingSession.STATUS_RUNNING)

        stop_paper_session(session.pk)

        session.refresh_from_db()
        assert session.status == PaperTradingSession.STATUS_STOPPED
        assert session.stopped_at is not None

    def test_stop_already_stopped(self) -> None:
        session = _create_session(
            status=PaperTradingSession.STATUS_STOPPED,
            stopped_at=datetime(2024, 1, 2, tzinfo=UTC),
        )

        # Should not raise -- just overwrites
        stop_paper_session(session.pk)

        session.refresh_from_db()
        assert session.status == PaperTradingSession.STATUS_STOPPED

    def test_stop_sends_sigterm(self) -> None:
        from unittest.mock import patch

        session = _create_session(
            status=PaperTradingSession.STATUS_RUNNING,
            process_pid=99999,
        )

        with patch("os.kill") as mock_kill:
            stop_paper_session(session.pk)
            mock_kill.assert_called_once()

        session.refresh_from_db()
        assert session.process_pid is None

    def test_stop_handles_missing_process(self) -> None:
        from unittest.mock import patch

        session = _create_session(
            status=PaperTradingSession.STATUS_RUNNING,
            process_pid=99999,
        )

        with patch("os.kill", side_effect=ProcessLookupError):
            stop_paper_session(session.pk)  # should not raise

        session.refresh_from_db()
        assert session.status == PaperTradingSession.STATUS_STOPPED

    def test_stop_clears_pid(self) -> None:
        session = _create_session(
            status=PaperTradingSession.STATUS_RUNNING,
            process_pid=None,
        )

        stop_paper_session(session.pk)

        session.refresh_from_db()
        assert session.process_pid is None


class SavePaperTradeTests(TestCase):
    def test_save_open_trade(self) -> None:
        session = _create_session()
        trade_pk = save_paper_trade(
            session_id=session.pk,
            instrument="XAU/USD",
            side="BUY",
            quantity=100.0,
            open_price=2050.50,
            opened_at=datetime(2024, 1, 2, tzinfo=UTC),
        )

        assert trade_pk is not None
        trade = PaperTrade.objects.get(pk=trade_pk)
        assert trade.instrument == "XAU/USD"
        assert trade.side == "BUY"
        assert trade.quantity == 100.0
        assert trade.open_price == 2050.50
        assert trade.close_price is None
        assert trade.realized_pnl is None
        assert trade.closed_at is None
        assert trade.is_open is True

    def test_save_closed_trade(self) -> None:
        session = _create_session()
        trade_pk = save_paper_trade(
            session_id=session.pk,
            instrument="XAU/USD",
            side="SELL",
            quantity=50.0,
            open_price=2060.00,
            opened_at=datetime(2024, 1, 2, tzinfo=UTC),
            close_price=2050.00,
            realized_pnl=500.0,
            realized_return_pct=0.5,
            closed_at=datetime(2024, 1, 2, 1, 0, tzinfo=UTC),
            duration_seconds=3600.0,
        )

        trade = PaperTrade.objects.get(pk=trade_pk)
        assert trade.close_price == 2050.00
        assert trade.realized_pnl == 500.0
        assert trade.realized_return_pct == 0.5
        assert trade.closed_at is not None
        assert trade.duration_seconds == 3600.0
        assert trade.is_open is False

    def test_save_trade_with_live_metrics(self) -> None:
        session = _create_session()
        trade_pk = save_paper_trade(
            session_id=session.pk,
            instrument="EUR/USD",
            side="BUY",
            quantity=100000.0,
            open_price=1.10000,
            opened_at=datetime(2024, 1, 2, tzinfo=UTC),
            fill_latency_ms=23.5,
            slippage_ticks=0.5,
            spread_at_entry=0.00012,
        )

        trade = PaperTrade.objects.get(pk=trade_pk)
        assert trade.fill_latency_ms == 23.5
        assert trade.slippage_ticks == 0.5
        assert trade.spread_at_entry == 0.00012


class SaveSessionEventTests(TestCase):
    def test_save_event(self) -> None:
        session = _create_session()
        save_session_event(
            session_id=session.pk,
            event_type="info",
            message="Session started",
        )

        events = SessionEvent.objects.filter(session=session)
        assert events.count() == 1
        event = events.first()
        assert event is not None
        assert event.event_type == "info"
        assert event.message == "Session started"
        assert event.timestamp is not None
        assert event.details_json == {}

    def test_save_event_with_details(self) -> None:
        session = _create_session()
        details = {"drawdown": 5.2, "reason": "daily limit"}
        save_session_event(
            session_id=session.pk,
            event_type="circuit_breaker",
            message="Daily loss limit reached",
            details=details,
        )

        event = SessionEvent.objects.filter(session=session).first()
        assert event is not None
        assert event.details_json == details

    def test_save_event_with_explicit_timestamp(self) -> None:
        session = _create_session()
        ts = datetime(2024, 3, 15, 14, 30, tzinfo=UTC)
        save_session_event(
            session_id=session.pk,
            event_type="risk_warning",
            message="High drawdown",
            timestamp=ts,
        )

        event = SessionEvent.objects.filter(session=session).first()
        assert event is not None
        assert event.timestamp == ts

    def test_save_event_auto_timestamp(self) -> None:
        session = _create_session()
        before = datetime.now(UTC)
        save_session_event(
            session_id=session.pk,
            event_type="info",
            message="test",
        )
        after = datetime.now(UTC)

        event = SessionEvent.objects.filter(session=session).first()
        assert event is not None
        assert before <= event.timestamp <= after


class SaveRiskSnapshotTests(TestCase):
    def test_save_snapshot(self) -> None:
        session = _create_session()
        save_risk_snapshot(
            session_id=session.pk,
            equity=102000.0,
            daily_pnl=500.0,
            open_positions=2,
            drawdown_pct=1.5,
            utilization_pct=30.0,
        )

        snapshots = RiskSnapshot.objects.filter(session=session)
        assert snapshots.count() == 1
        snap = snapshots.first()
        assert snap is not None
        assert snap.equity == 102000.0
        assert snap.daily_pnl == 500.0
        assert snap.open_positions == 2
        assert snap.drawdown_pct == 1.5
        assert snap.utilization_pct == 30.0

    def test_save_snapshot_with_explicit_timestamp(self) -> None:
        session = _create_session()
        ts = datetime(2024, 2, 10, 12, 0, tzinfo=UTC)
        save_risk_snapshot(
            session_id=session.pk,
            equity=99000.0,
            daily_pnl=-1000.0,
            open_positions=0,
            drawdown_pct=3.0,
            utilization_pct=0.0,
            timestamp=ts,
        )

        snap = RiskSnapshot.objects.filter(session=session).first()
        assert snap is not None
        assert snap.timestamp == ts

    def test_save_snapshot_auto_timestamp(self) -> None:
        session = _create_session()
        before = datetime.now(UTC)
        save_risk_snapshot(
            session_id=session.pk,
            equity=100000.0,
            daily_pnl=0.0,
            open_positions=0,
            drawdown_pct=0.0,
            utilization_pct=0.0,
        )
        after = datetime.now(UTC)

        snap = RiskSnapshot.objects.filter(session=session).first()
        assert snap is not None
        assert before <= snap.timestamp <= after

    def test_multiple_snapshots(self) -> None:
        session = _create_session()
        for i in range(5):
            save_risk_snapshot(
                session_id=session.pk,
                equity=100000.0 + i * 100,
                daily_pnl=float(i * 100),
                open_positions=i,
                drawdown_pct=float(i) * 0.5,
                utilization_pct=float(i) * 10,
                timestamp=datetime(2024, 1, 1, i, 0, tzinfo=UTC),
            )

        assert RiskSnapshot.objects.filter(session=session).count() == 5


class InstrumentListPropertyTests(TestCase):
    """Test PaperTradingSession.instrument_list property."""

    def test_single_instrument(self) -> None:
        session = _create_session(instrument="XAU/USD")
        assert session.instrument_list == ["XAU/USD"]

    def test_comma_separated_instruments(self) -> None:
        session = _create_session(instrument="XAU/USD, EUR/USD")
        assert session.instrument_list == ["XAU/USD", "EUR/USD"]

    def test_comma_separated_no_spaces(self) -> None:
        session = _create_session(instrument="XAU/USD,EUR/USD,GBP/USD")
        assert session.instrument_list == ["XAU/USD", "EUR/USD", "GBP/USD"]

    def test_empty_instrument(self) -> None:
        session = _create_session(instrument="")
        assert session.instrument_list == []

    def test_strips_whitespace(self) -> None:
        session = _create_session(instrument="  XAU/USD , EUR/USD  ")
        assert session.instrument_list == ["XAU/USD", "EUR/USD"]


class ModelStrTests(TestCase):
    """Test __str__ methods on paper trading models."""

    def test_session_str_with_pnl(self) -> None:
        session = _create_session(total_pnl=150.0)
        assert "coban_reborn" in str(session)
        assert "$+150.00" in str(session)

    def test_session_str_without_pnl(self) -> None:
        session = _create_session(total_pnl=None)
        assert "n/a" in str(session)

    def test_paper_trade_str_closed(self) -> None:
        session = _create_session()
        trade = PaperTrade.objects.create(
            session=session,
            instrument="XAU/USD",
            side="BUY",
            quantity=100,
            open_price=2000.0,
            close_price=2010.0,
            realized_pnl=50.0,
            opened_at=datetime(2024, 1, 1, tzinfo=UTC),
            closed_at=datetime(2024, 1, 1, 1, tzinfo=UTC),
        )
        s = str(trade)
        assert "BUY" in s
        assert "XAU/USD" in s
        assert "+50.00" in s

    def test_paper_trade_str_open(self) -> None:
        session = _create_session()
        trade = PaperTrade.objects.create(
            session=session,
            instrument="XAU/USD",
            side="SELL",
            quantity=100,
            open_price=2000.0,
            opened_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        assert "open" in str(trade)

    def test_session_event_str(self) -> None:
        session = _create_session()
        event = SessionEvent.objects.create(
            session=session,
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            event_type="order_filled",
            message="BUY 100 XAU/USD @ 2000.0",
        )
        s = str(event)
        assert "[order_filled]" in s
        assert "BUY" in s
