"""Tests for pyfx.live.runner – session status/history and helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from pyfx.web.dashboard.models import (
    PaperTrade,
    PaperTradingSession,
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


def _create_paper_trade(
    session: PaperTradingSession,
    *,
    side: str = "BUY",
    open_price: float = 2050.0,
    close_price: float | None = None,
    realized_pnl: float | None = None,
    opened_at: datetime | None = None,
    closed_at: datetime | None = None,
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
        closed_at=closed_at,
    )


def _create_event(
    session: PaperTradingSession,
    *,
    event_type: str = "info",
    message: str = "test",
    timestamp: datetime | None = None,
) -> SessionEvent:
    return SessionEvent.objects.create(
        session=session,
        event_type=event_type,
        message=message,
        timestamp=timestamp or datetime(2024, 1, 2, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Tests: get_session_status
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("pyfx.live.runner._setup_django")
class TestGetSessionStatus:
    def test_returns_specific_session(self, _mock_setup: object) -> None:
        from pyfx.live.runner import get_session_status

        session = _create_session(total_pnl=500.0, num_trades=5, win_rate=0.6)
        result = get_session_status(session_id=session.pk)
        assert result["session_id"] == session.pk
        assert result["status"] == "running"
        assert result["strategy"] == "coban_reborn"
        assert result["instrument"] == "XAU/USD"
        assert result["total_pnl"] == 500.0
        assert result["num_trades"] == 5

    def test_returns_most_recent_when_no_id(self, _mock_setup: object) -> None:
        from pyfx.live.runner import get_session_status

        _create_session(
            strategy="old_strategy",
            started_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        newer = _create_session(
            strategy="newer_strategy",
            started_at=datetime(2024, 6, 1, tzinfo=UTC),
        )
        result = get_session_status()
        assert result["session_id"] == newer.pk
        assert result["strategy"] == "newer_strategy"

    def test_returns_error_when_no_sessions(self, _mock_setup: object) -> None:
        from pyfx.live.runner import get_session_status

        result = get_session_status()
        assert result["error"] == "No paper trading sessions found"

    def test_includes_open_trades(self, _mock_setup: object) -> None:
        from pyfx.live.runner import get_session_status

        session = _create_session()
        _create_paper_trade(session)  # open trade (no closed_at)
        result = get_session_status(session_id=session.pk)
        assert len(result["open_trades"]) == 1  # type: ignore[arg-type]
        assert result["closed_trades"] == 0

    def test_includes_closed_trade_count(self, _mock_setup: object) -> None:
        from pyfx.live.runner import get_session_status

        session = _create_session()
        _create_paper_trade(
            session,
            close_price=2060.0,
            realized_pnl=10.0,
            closed_at=datetime(2024, 1, 2, 11, 0, tzinfo=UTC),
        )
        result = get_session_status(session_id=session.pk)
        assert len(result["open_trades"]) == 0  # type: ignore[arg-type]
        assert result["closed_trades"] == 1

    def test_includes_recent_events(self, _mock_setup: object) -> None:
        from pyfx.live.runner import get_session_status

        session = _create_session()
        for i in range(15):
            _create_event(
                session,
                message=f"Event {i}",
                timestamp=datetime(2024, 1, 2, i, 0, tzinfo=UTC),
            )
        result = get_session_status(session_id=session.pk)
        # Should return at most 10 recent events
        assert len(result["recent_events"]) == 10  # type: ignore[arg-type]

    def test_stopped_at_none_when_running(self, _mock_setup: object) -> None:
        from pyfx.live.runner import get_session_status

        session = _create_session(status="running")
        result = get_session_status(session_id=session.pk)
        assert result["stopped_at"] is None

    def test_stopped_at_present_when_stopped(self, _mock_setup: object) -> None:
        from pyfx.live.runner import get_session_status

        stop_ts = datetime(2024, 1, 5, tzinfo=UTC)
        session = _create_session(status="stopped", stopped_at=stop_ts)
        result = get_session_status(session_id=session.pk)
        assert result["stopped_at"] is not None

    def test_raises_for_missing_session(self, _mock_setup: object) -> None:
        from django.core.exceptions import ObjectDoesNotExist

        from pyfx.live.runner import get_session_status

        with pytest.raises(ObjectDoesNotExist):
            get_session_status(session_id=999_999)


# ---------------------------------------------------------------------------
# Tests: get_session_history
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("pyfx.live.runner._setup_django")
class TestGetSessionHistory:
    def test_returns_events_and_trades(self, _mock_setup: object) -> None:
        from pyfx.live.runner import get_session_history

        session = _create_session()
        _create_event(session, timestamp=datetime.now(UTC))
        _create_paper_trade(session, opened_at=datetime.now(UTC))
        result = get_session_history()
        assert len(result) == 1
        assert "events" in result[0]
        assert "trades" in result[0]

    def test_filters_by_since(self, _mock_setup: object) -> None:
        from pyfx.live.runner import get_session_history

        session = _create_session()
        old_ts = datetime(2024, 1, 1, tzinfo=UTC)
        recent_ts = datetime.now(UTC) - timedelta(hours=1)
        _create_event(session, message="old", timestamp=old_ts)
        _create_event(session, message="recent", timestamp=recent_ts)
        _create_paper_trade(session, opened_at=old_ts)
        _create_paper_trade(session, opened_at=recent_ts)

        result = get_session_history(since=datetime.now(UTC) - timedelta(hours=2))
        events = result[0]["events"]
        trades = result[0]["trades"]
        assert len(events) == 1
        assert len(trades) == 1

    def test_limits_results(self, _mock_setup: object) -> None:
        from pyfx.live.runner import get_session_history

        session = _create_session()
        now = datetime.now(UTC)
        for i in range(5):
            _create_event(
                session,
                message=f"Event {i}",
                timestamp=now - timedelta(minutes=i),
            )
            _create_paper_trade(
                session,
                opened_at=now - timedelta(minutes=i),
            )

        result = get_session_history(last_n=3, since=now - timedelta(hours=1))
        assert len(result[0]["events"]) == 3
        assert len(result[0]["trades"]) == 3

    def test_last_n_only_skips_since(self, _mock_setup: object) -> None:
        """last_n without since should not apply a time filter."""
        from pyfx.live.runner import get_session_history

        session = _create_session()
        now = datetime.now(UTC)
        for i in range(5):
            _create_event(session, message=f"E{i}", timestamp=now - timedelta(days=i * 30))
            _create_paper_trade(session, opened_at=now - timedelta(days=i * 30))

        result = get_session_history(last_n=2)
        assert len(result[0]["events"]) == 2
        assert len(result[0]["trades"]) == 2

    def test_defaults_to_last_24h(self, _mock_setup: object) -> None:
        from pyfx.live.runner import get_session_history

        session = _create_session()
        now = datetime.now(UTC)
        _create_event(session, message="recent", timestamp=now - timedelta(hours=1))
        _create_event(
            session,
            message="old",
            timestamp=now - timedelta(hours=48),
        )
        result = get_session_history()
        events = result[0]["events"]
        assert len(events) == 1

    def test_empty_when_no_data(self, _mock_setup: object) -> None:
        from pyfx.live.runner import get_session_history

        result = get_session_history()
        assert result[0]["events"] == []
        assert result[0]["trades"] == []


# ---------------------------------------------------------------------------
# Tests: helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_strategy_import_path(self) -> None:
        from pyfx.live.runner import _strategy_import_path

        class FakeStrategy:
            pass

        FakeStrategy.__module__ = "pyfx.strategies.my_strat"
        FakeStrategy.__name__ = "MyStrat"  # type: ignore[attr-defined]
        FakeStrategy.__qualname__ = "MyStrat"

        strategies = {"my_strat": FakeStrategy}  # type: ignore[dict-item]
        result = _strategy_import_path("my_strat", strategies)
        assert result == "pyfx.strategies.my_strat:MyStrat"

    def test_config_import_path(self) -> None:
        from pyfx.live.runner import _config_import_path

        class FakeConfig:
            pass

        FakeConfig.__module__ = "pyfx.strategies.my_strat"
        FakeConfig.__name__ = "MyStratConfig"  # type: ignore[attr-defined]
        FakeConfig.__qualname__ = "MyStratConfig"

        result = _config_import_path(FakeConfig)
        assert result == "pyfx.strategies.my_strat:MyStratConfig"

    def test_msgspec_to_dict(self) -> None:
        import msgspec

        from pyfx.live.runner import _msgspec_to_dict

        class DemoStruct(msgspec.Struct, frozen=True):
            name: str = "default_name"
            value: int = 10
            other: str = "other"

        obj = DemoStruct(name="custom", value=42)
        result = _msgspec_to_dict(obj)
        # "name" and "value" differ from defaults -> included
        assert result["name"] == "custom"
        assert result["value"] == 42
        # "other" == default -> excluded
        assert "other" not in result

    def test_msgspec_to_dict_all_defaults(self) -> None:
        import msgspec

        from pyfx.live.runner import _msgspec_to_dict

        class DemoStruct2(msgspec.Struct, frozen=True):
            x: int = 0
            y: str = ""

        result = _msgspec_to_dict(DemoStruct2())
        assert result == {}
