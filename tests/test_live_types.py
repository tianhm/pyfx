"""Tests for LiveTradingConfig and TradingEvent models."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from pyfx.core.types import ConnectionTestResult, LiveTradingConfig, TradingEvent


class TestLiveTradingConfig:
    """LiveTradingConfig model validation and defaults."""

    def test_minimal_construction(self) -> None:
        cfg = LiveTradingConfig(strategy="coban_reborn", instrument="XAU/USD")
        assert cfg.strategy == "coban_reborn"
        assert cfg.instrument == "XAU/USD"

    def test_defaults(self) -> None:
        cfg = LiveTradingConfig(strategy="sma", instrument="EUR/USD")
        assert cfg.bar_type == "1-MINUTE-LAST-EXTERNAL"
        assert cfg.extra_bar_types == []
        assert cfg.strategy_params == {}
        assert cfg.trade_size == Decimal("100000")
        assert cfg.account_currency == "USD"

    def test_full_construction(self) -> None:
        cfg = LiveTradingConfig(
            strategy="coban_reborn",
            instrument="XAU/USD",
            bar_type="5-MINUTE-LAST-EXTERNAL",
            extra_bar_types=["15-MINUTE-LAST-EXTERNAL", "1-HOUR-LAST-EXTERNAL"],
            strategy_params={"entry_mode": "trend_follow", "sma_fast": 3},
            trade_size=Decimal("50000"),
            account_currency="EUR",
        )
        assert cfg.bar_type == "5-MINUTE-LAST-EXTERNAL"
        assert len(cfg.extra_bar_types) == 2
        assert cfg.strategy_params["entry_mode"] == "trend_follow"
        assert cfg.strategy_params["sma_fast"] == 3
        assert cfg.trade_size == Decimal("50000")
        assert cfg.account_currency == "EUR"

    def test_serialization_roundtrip(self) -> None:
        cfg = LiveTradingConfig(
            strategy="coban_reborn",
            instrument="XAU/USD",
            strategy_params={"fast": 3, "slow": 7, "use_atr": True},
        )
        data = cfg.model_dump()
        restored = LiveTradingConfig.model_validate(data)
        assert restored == cfg

    def test_json_roundtrip(self) -> None:
        cfg = LiveTradingConfig(
            strategy="coban_reborn",
            instrument="EUR/USD",
            trade_size=Decimal("200000"),
        )
        json_str = cfg.model_dump_json()
        restored = LiveTradingConfig.model_validate_json(json_str)
        assert restored.strategy == cfg.strategy
        assert restored.trade_size == cfg.trade_size


class TestLiveTradingConfigNormalization:
    """Tests for the _normalize_instruments model validator."""

    def test_single_instrument_populates_instruments_list(self) -> None:
        cfg = LiveTradingConfig(strategy="x", instrument="XAU/USD")
        assert cfg.instruments == ["XAU/USD"]
        assert cfg.instrument == "XAU/USD"

    def test_instruments_list_populates_instrument(self) -> None:
        cfg = LiveTradingConfig(
            strategy="x",
            instruments=["XAU/USD", "EUR/USD"],
        )
        assert cfg.instrument == "XAU/USD"
        assert cfg.instruments == ["XAU/USD", "EUR/USD"]

    def test_both_set_keeps_both(self) -> None:
        cfg = LiveTradingConfig(
            strategy="x",
            instrument="EUR/USD",
            instruments=["EUR/USD", "GBP/USD"],
        )
        assert cfg.instrument == "EUR/USD"
        assert cfg.instruments == ["EUR/USD", "GBP/USD"]

    def test_no_instruments_raises(self) -> None:
        with pytest.raises(ValueError, match="At least one instrument"):
            LiveTradingConfig(strategy="x")

    def test_empty_instrument_with_instruments_list(self) -> None:
        cfg = LiveTradingConfig(
            strategy="x",
            instrument="",
            instruments=["GBP/USD"],
        )
        assert cfg.instrument == "GBP/USD"
        assert cfg.instruments == ["GBP/USD"]

    def test_empty_strings_both_raises(self) -> None:
        with pytest.raises(ValueError, match="At least one instrument"):
            LiveTradingConfig(strategy="x", instrument="", instruments=[])


class TestConnectionTestResult:
    """ConnectionTestResult model construction and defaults."""

    def test_minimal_construction(self) -> None:
        result = ConnectionTestResult(success=True, elapsed_seconds=1.5)
        assert result.success is True
        assert result.elapsed_seconds == 1.5
        assert result.diagnostics == []
        assert result.warnings == []
        assert result.instrument_specs is None
        assert result.error is None

    def test_full_construction(self) -> None:
        result = ConnectionTestResult(
            success=False,
            elapsed_seconds=5.2,
            diagnostics=["step 1", "step 2", "FAIL"],
            warnings=["port mismatch"],
            instrument_specs={"id": "EUR/USD.IDEALPRO", "tick_size": "0.00001"},
            error="Connection refused",
        )
        assert result.success is False
        assert result.elapsed_seconds == 5.2
        assert len(result.diagnostics) == 3
        assert result.diagnostics[2] == "FAIL"
        assert result.warnings == ["port mismatch"]
        assert result.instrument_specs is not None
        assert result.instrument_specs["id"] == "EUR/USD.IDEALPRO"
        assert result.error == "Connection refused"

    def test_serialization_roundtrip(self) -> None:
        result = ConnectionTestResult(
            success=True,
            elapsed_seconds=2.0,
            diagnostics=["Config OK"],
            warnings=[],
            instrument_specs={"id": "XAUUSD.SMART"},
        )
        data = result.model_dump()
        restored = ConnectionTestResult.model_validate(data)
        assert restored == result


class TestTradingEvent:
    """TradingEvent model validation and defaults."""

    def test_minimal_construction(self) -> None:
        ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        evt = TradingEvent(
            timestamp=ts,
            event_type="order_filled",
            message="Buy 100000 EUR/USD @ 1.10500",
        )
        assert evt.timestamp == ts
        assert evt.event_type == "order_filled"
        assert evt.message == "Buy 100000 EUR/USD @ 1.10500"
        assert evt.details == {}

    def test_with_details(self) -> None:
        ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        evt = TradingEvent(
            timestamp=ts,
            event_type="risk_breach",
            message="Daily loss limit exceeded",
            details={"loss": -2500.0, "limit": 2000.0},
        )
        assert evt.details["loss"] == -2500.0
        assert evt.details["limit"] == 2000.0

    def test_all_event_types_accepted(self) -> None:
        """event_type is a free-form string; all documented types work."""
        ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        event_types = [
            "order_submitted",
            "order_filled",
            "position_opened",
            "position_closed",
            "risk_breach",
            "circuit_breaker",
            "connection_lost",
            "connection_restored",
            "info",
        ]
        for et in event_types:
            evt = TradingEvent(timestamp=ts, event_type=et, message="test")
            assert evt.event_type == et

    def test_serialization_roundtrip(self) -> None:
        ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        evt = TradingEvent(
            timestamp=ts,
            event_type="position_closed",
            message="Closed XAU/USD long +$350",
            details={"pnl": 350.0, "instrument": "XAU/USD"},
        )
        data = evt.model_dump()
        restored = TradingEvent.model_validate(data)
        assert restored == evt

    def test_json_roundtrip(self) -> None:
        ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        evt = TradingEvent(
            timestamp=ts,
            event_type="connection_lost",
            message="IB Gateway disconnected",
        )
        json_str = evt.model_dump_json()
        restored = TradingEvent.model_validate_json(json_str)
        assert restored.event_type == evt.event_type
        assert restored.message == evt.message
