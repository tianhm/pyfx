"""Tests for pyfx.live.events – event persistence bridge."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pyfx.web.dashboard.models import (
    PaperTrade,
    PaperTradingSession,
    RiskSnapshot,
    SessionEvent,
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


# ---------------------------------------------------------------------------
# Tests: save_session_event
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSaveSessionEvent:
    def test_creates_event_with_defaults(self) -> None:
        from pyfx.live.events import save_session_event

        session = _create_session()
        save_session_event(
            session_id=session.pk,
            event_type="info",
            message="Test event",
        )
        events = SessionEvent.objects.filter(session=session)
        assert events.count() == 1
        event = events.first()
        assert event is not None
        assert event.event_type == "info"
        assert event.message == "Test event"
        assert event.details_json == {}
        assert event.timestamp is not None

    def test_creates_event_with_details(self) -> None:
        from pyfx.live.events import save_session_event

        session = _create_session()
        save_session_event(
            session_id=session.pk,
            event_type="risk_warning",
            message="Limit reached",
            details={"key": "value", "count": 5},
        )
        event = SessionEvent.objects.get(session=session)
        assert event.details_json == {"key": "value", "count": 5}

    def test_creates_event_with_custom_timestamp(self) -> None:
        from pyfx.live.events import save_session_event

        session = _create_session()
        ts = datetime(2024, 6, 15, 12, 30, 0, tzinfo=UTC)
        save_session_event(
            session_id=session.pk,
            event_type="info",
            message="Custom ts",
            timestamp=ts,
        )
        event = SessionEvent.objects.get(session=session)
        assert event.timestamp == ts

    def test_multiple_events(self) -> None:
        from pyfx.live.events import save_session_event

        session = _create_session()
        for i in range(3):
            save_session_event(
                session_id=session.pk,
                event_type="info",
                message=f"Event {i}",
            )
        assert SessionEvent.objects.filter(session=session).count() == 3


# ---------------------------------------------------------------------------
# Tests: save_paper_trade_open
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSavePaperTradeOpen:
    def test_creates_trade_and_returns_pk(self) -> None:
        from pyfx.live.events import save_paper_trade_open

        session = _create_session()
        ts = datetime(2024, 1, 2, 10, 0, 0, tzinfo=UTC)
        pk = save_paper_trade_open(
            session_id=session.pk,
            instrument="XAU/USD",
            side="BUY",
            quantity=1.0,
            open_price=2050.50,
            opened_at=ts,
        )
        assert isinstance(pk, int)
        trade = PaperTrade.objects.get(pk=pk)
        assert trade.instrument == "XAU/USD"
        assert trade.side == "BUY"
        assert trade.quantity == 1.0
        assert trade.open_price == 2050.50
        assert trade.opened_at == ts
        assert trade.closed_at is None
        assert trade.realized_pnl is None

    def test_creates_trade_with_live_metrics(self) -> None:
        from pyfx.live.events import save_paper_trade_open

        session = _create_session()
        pk = save_paper_trade_open(
            session_id=session.pk,
            instrument="EUR/USD",
            side="SELL",
            quantity=100_000,
            open_price=1.10000,
            opened_at=datetime(2024, 1, 2, tzinfo=UTC),
            fill_latency_ms=12.5,
            slippage_ticks=1.0,
            spread_at_entry=0.00012,
        )
        trade = PaperTrade.objects.get(pk=pk)
        assert trade.fill_latency_ms == 12.5
        assert trade.slippage_ticks == 1.0
        assert trade.spread_at_entry == 0.00012

    def test_defaults_live_metrics_to_none(self) -> None:
        from pyfx.live.events import save_paper_trade_open

        session = _create_session()
        pk = save_paper_trade_open(
            session_id=session.pk,
            instrument="EUR/USD",
            side="BUY",
            quantity=100_000,
            open_price=1.10000,
            opened_at=datetime(2024, 1, 2, tzinfo=UTC),
        )
        trade = PaperTrade.objects.get(pk=pk)
        assert trade.fill_latency_ms is None
        assert trade.slippage_ticks is None
        assert trade.spread_at_entry is None


# ---------------------------------------------------------------------------
# Tests: save_paper_trade_close
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSavePaperTradeClose:
    def test_updates_trade_on_close(self) -> None:
        from pyfx.live.events import save_paper_trade_close, save_paper_trade_open

        session = _create_session()
        pk = save_paper_trade_open(
            session_id=session.pk,
            instrument="XAU/USD",
            side="BUY",
            quantity=1.0,
            open_price=2050.00,
            opened_at=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
        )
        close_ts = datetime(2024, 1, 2, 11, 0, tzinfo=UTC)
        save_paper_trade_close(
            trade_id=pk,
            close_price=2060.00,
            realized_pnl=10.00,
            realized_return_pct=0.49,
            closed_at=close_ts,
            duration_seconds=3600.0,
        )
        trade = PaperTrade.objects.get(pk=pk)
        assert trade.close_price == 2060.00
        assert trade.realized_pnl == 10.00
        assert trade.realized_return_pct == 0.49
        assert trade.closed_at == close_ts
        assert trade.duration_seconds == 3600.0

    def test_nonexistent_trade_id_noop(self) -> None:
        from pyfx.live.events import save_paper_trade_close

        # Should not raise -- .filter().update() on empty queryset is fine
        save_paper_trade_close(
            trade_id=999_999,
            close_price=1.0,
            realized_pnl=0.0,
            realized_return_pct=0.0,
            closed_at=datetime(2024, 1, 1, tzinfo=UTC),
            duration_seconds=0.0,
        )


# ---------------------------------------------------------------------------
# Tests: save_risk_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSaveRiskSnapshot:
    def test_creates_snapshot(self) -> None:
        from pyfx.live.events import save_risk_snapshot

        session = _create_session()
        save_risk_snapshot(
            session_id=session.pk,
            equity=100_500.0,
            daily_pnl=500.0,
            open_positions=2,
            drawdown_pct=1.5,
            utilization_pct=66.7,
        )
        snapshots = RiskSnapshot.objects.filter(session=session)
        assert snapshots.count() == 1
        snap = snapshots.first()
        assert snap is not None
        assert snap.equity == 100_500.0
        assert snap.daily_pnl == 500.0
        assert snap.open_positions == 2
        assert snap.drawdown_pct == 1.5
        assert snap.utilization_pct == 66.7
        assert snap.timestamp is not None

    def test_creates_snapshot_with_timestamp(self) -> None:
        from pyfx.live.events import save_risk_snapshot

        session = _create_session()
        ts = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
        save_risk_snapshot(
            session_id=session.pk,
            equity=95_000.0,
            daily_pnl=-500.0,
            open_positions=0,
            drawdown_pct=5.0,
            utilization_pct=0.0,
            timestamp=ts,
        )
        snap = RiskSnapshot.objects.get(session=session)
        assert snap.timestamp == ts


# ---------------------------------------------------------------------------
# Tests: update_session_metrics
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdateSessionMetrics:
    def test_updates_session_fields(self) -> None:
        from pyfx.live.events import update_session_metrics

        session = _create_session()
        update_session_metrics(
            session_id=session.pk,
            total_pnl=1500.0,
            total_return_pct=1.5,
            num_trades=20,
            win_rate=0.65,
            max_drawdown_pct=3.2,
            profit_factor=2.1,
            avg_trade_pnl=75.0,
        )
        session.refresh_from_db()
        assert session.total_pnl == 1500.0
        assert session.total_return_pct == 1.5
        assert session.num_trades == 20
        assert session.win_rate == 0.65
        assert session.max_drawdown_pct == 3.2
        assert session.profit_factor == 2.1
        assert session.avg_trade_pnl == 75.0

    def test_updates_with_none_profit_factor(self) -> None:
        from pyfx.live.events import update_session_metrics

        session = _create_session()
        update_session_metrics(
            session_id=session.pk,
            total_pnl=0.0,
            total_return_pct=0.0,
            num_trades=0,
            win_rate=0.0,
            max_drawdown_pct=0.0,
            profit_factor=None,
            avg_trade_pnl=0.0,
        )
        session.refresh_from_db()
        assert session.profit_factor is None

    def test_nonexistent_session_noop(self) -> None:
        from pyfx.live.events import update_session_metrics

        # .filter().update() on empty set is fine
        update_session_metrics(
            session_id=999_999,
            total_pnl=0.0,
            total_return_pct=0.0,
            num_trades=0,
            win_rate=0.0,
            max_drawdown_pct=0.0,
            profit_factor=None,
            avg_trade_pnl=0.0,
        )
