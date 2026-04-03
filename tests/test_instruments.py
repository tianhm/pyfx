"""Tests for the instrument specification registry."""

from __future__ import annotations

from pyfx.core.instruments import InstrumentSpec, get_instrument_spec


class TestKnownInstruments:
    """Registry returns correct specs for known instruments."""

    def test_eur_usd(self) -> None:
        spec = get_instrument_spec("EUR/USD")
        assert spec.price_precision == 5
        assert spec.is_fx is True
        assert spec.needs_quote_conversion is False
        assert spec.base_price == 1.10

    def test_usd_jpy(self) -> None:
        spec = get_instrument_spec("USD/JPY")
        assert spec.price_precision == 3
        assert spec.is_fx is True
        assert spec.needs_quote_conversion is True
        assert spec.base_price == 150.0

    def test_gbp_jpy(self) -> None:
        spec = get_instrument_spec("GBP/JPY")
        assert spec.price_precision == 3
        assert spec.needs_quote_conversion is True
        assert spec.base_price == 190.0

    def test_eur_jpy(self) -> None:
        spec = get_instrument_spec("EUR/JPY")
        assert spec.needs_quote_conversion is True
        assert spec.base_price == 165.0

    def test_gbp_usd(self) -> None:
        spec = get_instrument_spec("GBP/USD")
        assert spec.price_precision == 5
        assert spec.needs_quote_conversion is False
        assert spec.base_price == 1.27

    def test_aud_usd(self) -> None:
        spec = get_instrument_spec("AUD/USD")
        assert spec.base_price == 0.65

    def test_nzd_usd(self) -> None:
        spec = get_instrument_spec("NZD/USD")
        assert spec.base_price == 0.60

    def test_xau_usd(self) -> None:
        spec = get_instrument_spec("XAU/USD")
        assert spec.price_precision == 2
        assert spec.is_fx is False
        assert spec.needs_quote_conversion is False
        assert spec.tick_scheme == "FOREX_3DECIMAL"
        assert spec.base_price == 3000.0

    def test_oil_usd(self) -> None:
        spec = get_instrument_spec("OIL/USD")
        assert spec.price_precision == 2
        assert spec.is_fx is False
        assert spec.base_price == 75.0

    def test_wti_usd(self) -> None:
        spec = get_instrument_spec("WTI/USD")
        assert spec.price_precision == 2
        assert spec.base_price == 75.0

    def test_bco_usd(self) -> None:
        spec = get_instrument_spec("BCO/USD")
        assert spec.price_precision == 2
        assert spec.base_price == 80.0


class TestUnknownInstruments:
    """Registry auto-detects specs for unknown instruments."""

    def test_unknown_usd_quoted(self) -> None:
        spec = get_instrument_spec("CHF/USD")
        assert spec.price_precision == 5
        assert spec.is_fx is True
        assert spec.needs_quote_conversion is False

    def test_unknown_jpy_quoted(self) -> None:
        spec = get_instrument_spec("CAD/JPY")
        assert spec.price_precision == 3
        assert spec.is_fx is True
        assert spec.needs_quote_conversion is True

    def test_unknown_cross_pair(self) -> None:
        """Non-USD, non-JPY quote currency triggers conversion."""
        spec = get_instrument_spec("EUR/GBP")
        assert spec.price_precision == 5
        assert spec.needs_quote_conversion is True


class TestInstrumentSpecFrozen:
    """InstrumentSpec is immutable."""

    def test_frozen(self) -> None:
        spec = get_instrument_spec("EUR/USD")
        assert isinstance(spec, InstrumentSpec)
        try:
            spec.price_precision = 3  # type: ignore[misc]
            raise AssertionError("Should be frozen")  # pragma: no cover
        except AttributeError:
            pass
