"""Tests for IB instrument mapping (pyfx/adapters/instruments.py)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from pyfx.adapters.instruments import (
    IB_INSTRUMENT_IDS,
    INSTRUMENT_METADATA,
    InstrumentMeta,
    get_ib_instrument_id_str,
    get_instrument_meta,
    list_supported_instruments,
    log_resolved_instrument,
)

_IB_COMMON_MOD = (
    "nautilus_trader.adapters.interactive_brokers.common"
)
_IDENTIFIERS_MOD = "nautilus_trader.model.identifiers"


# ---------------------------------------------------------------------------
# IB_INSTRUMENT_IDS constant
# ---------------------------------------------------------------------------


class TestIBInstrumentIDs:
    """The IB_INSTRUMENT_IDS mapping is correct and complete."""

    def test_contains_all_expected_instruments(self) -> None:
        expected = {
            "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF",
            "EUR/GBP", "AUD/USD", "NZD/USD", "XAU/USD",
        }
        assert set(IB_INSTRUMENT_IDS.keys()) == expected

    def test_fx_pairs_use_idealpro(self) -> None:
        fx_pairs = [
            "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF",
            "EUR/GBP", "AUD/USD", "NZD/USD",
        ]
        for pair in fx_pairs:
            assert IB_INSTRUMENT_IDS[pair].endswith(".IDEALPRO"), (
                f"{pair} should use IDEALPRO"
            )

    def test_xau_usd_uses_smart(self) -> None:
        assert IB_INSTRUMENT_IDS["XAU/USD"] == "XAUUSD.SMART"

    def test_specific_id_values(self) -> None:
        assert IB_INSTRUMENT_IDS["EUR/USD"] == "EUR/USD.IDEALPRO"
        assert IB_INSTRUMENT_IDS["USD/JPY"] == "USD/JPY.IDEALPRO"


# ---------------------------------------------------------------------------
# get_ib_instrument_id_str()
# ---------------------------------------------------------------------------


class TestGetIBInstrumentIdStr:
    """get_ib_instrument_id_str returns the correct string or raises."""

    def test_known_instrument(self) -> None:
        assert get_ib_instrument_id_str("EUR/USD") == "EUR/USD.IDEALPRO"

    def test_xau_usd(self) -> None:
        assert get_ib_instrument_id_str("XAU/USD") == "XAUUSD.SMART"

    def test_all_instruments_resolve(self) -> None:
        for inst in IB_INSTRUMENT_IDS:
            result = get_ib_instrument_id_str(inst)
            assert isinstance(result, str)
            assert "." in result

    def test_unsupported_raises_keyerror(self) -> None:
        with pytest.raises(KeyError, match="Unsupported instrument"):
            get_ib_instrument_id_str("BTC/USD")

    def test_error_lists_supported(self) -> None:
        with pytest.raises(KeyError, match="Supported:") as exc_info:
            get_ib_instrument_id_str("FAKE/PAIR")
        assert "EUR/USD" in str(exc_info.value)


# ---------------------------------------------------------------------------
# list_supported_instruments()
# ---------------------------------------------------------------------------


class TestListSupportedInstruments:
    """list_supported_instruments returns sorted list of all instruments."""

    def test_returns_list(self) -> None:
        result = list_supported_instruments()
        assert isinstance(result, list)

    def test_sorted(self) -> None:
        result = list_supported_instruments()
        assert result == sorted(result)

    def test_count(self) -> None:
        result = list_supported_instruments()
        assert len(result) == 8

    def test_includes_expected(self) -> None:
        result = list_supported_instruments()
        assert "EUR/USD" in result
        assert "XAU/USD" in result
        assert "USD/JPY" in result


# ---------------------------------------------------------------------------
# get_ib_contract() -- requires mocking ibapi
# ---------------------------------------------------------------------------


class TestGetIBContract:
    """get_ib_contract returns IBContract instances (ibapi mocked)."""

    def test_known_instrument_returns_contract(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_ibcontract_cls = MagicMock()
        mock_ibcontract_instance = MagicMock()
        mock_ibcontract_cls.return_value = mock_ibcontract_instance

        mock_ib_common = MagicMock()
        mock_ib_common.IBContract = mock_ibcontract_cls

        monkeypatch.setitem(
            sys.modules, _IB_COMMON_MOD, mock_ib_common,
        )

        from pyfx.adapters.instruments import get_ib_contract

        result = get_ib_contract("EUR/USD")
        assert result is not None
        mock_ibcontract_cls.assert_called()

    def test_unsupported_instrument_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_ib_common = MagicMock()
        mock_ib_common.IBContract = MagicMock()

        monkeypatch.setitem(
            sys.modules, _IB_COMMON_MOD, mock_ib_common,
        )

        from pyfx.adapters.instruments import get_ib_contract

        with pytest.raises(KeyError, match="Unsupported instrument"):
            get_ib_contract("BTC/USD")

    def test_error_lists_supported_instruments(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_ib_common = MagicMock()
        mock_ib_common.IBContract = MagicMock()

        monkeypatch.setitem(
            sys.modules, _IB_COMMON_MOD, mock_ib_common,
        )

        from pyfx.adapters.instruments import get_ib_contract

        with pytest.raises(KeyError, match="Supported:") as exc_info:
            get_ib_contract("FAKE/PAIR")
        assert "EUR/USD" in str(exc_info.value)


# ---------------------------------------------------------------------------
# get_ib_instrument_id() -- requires mocking nautilus InstrumentId
# ---------------------------------------------------------------------------


class TestGetIBInstrumentId:
    """get_ib_instrument_id returns InstrumentId (nautilus mocked)."""

    def test_known_instrument(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_instrument_id_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instrument_id_cls.from_str.return_value = mock_instance

        mock_identifiers = MagicMock()
        mock_identifiers.InstrumentId = mock_instrument_id_cls

        monkeypatch.setitem(
            sys.modules, _IDENTIFIERS_MOD, mock_identifiers,
        )

        from pyfx.adapters.instruments import get_ib_instrument_id

        result = get_ib_instrument_id("EUR/USD")
        assert result is mock_instance
        mock_instrument_id_cls.from_str.assert_called_once_with(
            "EUR/USD.IDEALPRO",
        )

    def test_xau_usd_uses_smart(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_instrument_id_cls = MagicMock()
        mock_identifiers = MagicMock()
        mock_identifiers.InstrumentId = mock_instrument_id_cls

        monkeypatch.setitem(
            sys.modules, _IDENTIFIERS_MOD, mock_identifiers,
        )

        from pyfx.adapters.instruments import get_ib_instrument_id

        get_ib_instrument_id("XAU/USD")
        mock_instrument_id_cls.from_str.assert_called_once_with(
            "XAUUSD.SMART",
        )

    def test_unsupported_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_identifiers = MagicMock()

        monkeypatch.setitem(
            sys.modules, _IDENTIFIERS_MOD, mock_identifiers,
        )

        from pyfx.adapters.instruments import get_ib_instrument_id

        with pytest.raises(KeyError, match="Unsupported instrument"):
            get_ib_instrument_id("BTC/USD")

    def test_error_lists_supported(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_identifiers = MagicMock()

        monkeypatch.setitem(
            sys.modules, _IDENTIFIERS_MOD, mock_identifiers,
        )

        from pyfx.adapters.instruments import get_ib_instrument_id

        with pytest.raises(KeyError, match="Supported:") as exc_info:
            get_ib_instrument_id("FAKE/PAIR")
        assert "EUR/USD" in str(exc_info.value)


# ---------------------------------------------------------------------------
# INSTRUMENT_METADATA constant
# ---------------------------------------------------------------------------


class TestInstrumentMetadata:
    """The INSTRUMENT_METADATA dict has correct entries."""

    def test_contains_all_supported_instruments(self) -> None:
        expected = {
            "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF",
            "EUR/GBP", "AUD/USD", "NZD/USD", "XAU/USD",
        }
        assert set(INSTRUMENT_METADATA.keys()) == expected

    def test_all_values_are_instrument_meta(self) -> None:
        for inst, meta in INSTRUMENT_METADATA.items():
            assert isinstance(meta, InstrumentMeta), f"{inst} is not InstrumentMeta"

    def test_fx_pairs_share_same_specs(self) -> None:
        fx_pairs = ["EUR/USD", "GBP/USD", "USD/CHF", "EUR/GBP", "AUD/USD", "NZD/USD"]
        metas = [INSTRUMENT_METADATA[p] for p in fx_pairs]
        assert all(m == metas[0] for m in metas)

    def test_jpy_pair_has_different_tick_size(self) -> None:
        jpy = INSTRUMENT_METADATA["USD/JPY"]
        eur = INSTRUMENT_METADATA["EUR/USD"]
        assert jpy.tick_size != eur.tick_size
        assert jpy.tick_size == 0.001

    def test_gold_has_different_specs_than_fx(self) -> None:
        gold = INSTRUMENT_METADATA["XAU/USD"]
        fx = INSTRUMENT_METADATA["EUR/USD"]
        assert gold.lot_size != fx.lot_size
        assert gold.min_quantity == 1
        assert gold.tick_size == 0.01


# ---------------------------------------------------------------------------
# get_instrument_meta()
# ---------------------------------------------------------------------------


class TestGetInstrumentMeta:
    """get_instrument_meta returns correct metadata or raises."""

    def test_eur_usd_returns_fx_meta(self) -> None:
        meta = get_instrument_meta("EUR/USD")
        assert isinstance(meta, InstrumentMeta)
        assert meta.tick_size == 0.00001
        assert meta.min_quantity == 1000
        assert meta.lot_size == 1000
        assert meta.pip_value == 0.0001

    def test_xau_usd_returns_gold_meta(self) -> None:
        meta = get_instrument_meta("XAU/USD")
        assert meta.tick_size == 0.01
        assert meta.min_quantity == 1
        assert meta.lot_size == 1
        assert meta.pip_value == 0.01

    def test_xau_differs_from_fx(self) -> None:
        gold = get_instrument_meta("XAU/USD")
        fx = get_instrument_meta("EUR/USD")
        assert gold != fx

    def test_usd_jpy_returns_jpy_meta(self) -> None:
        meta = get_instrument_meta("USD/JPY")
        assert meta.tick_size == 0.001
        assert meta.pip_value == 0.01

    def test_unsupported_raises_keyerror(self) -> None:
        with pytest.raises(KeyError, match="Unsupported instrument"):
            get_instrument_meta("INVALID")

    def test_error_lists_supported(self) -> None:
        with pytest.raises(KeyError, match="Supported:") as exc_info:
            get_instrument_meta("BTC/USD")
        assert "EUR/USD" in str(exc_info.value)

    def test_all_instruments_resolve(self) -> None:
        for inst in INSTRUMENT_METADATA:
            meta = get_instrument_meta(inst)
            assert isinstance(meta, InstrumentMeta)


# ---------------------------------------------------------------------------
# log_resolved_instrument()
# ---------------------------------------------------------------------------


class TestLogResolvedInstrument:
    """log_resolved_instrument reads specs from a NautilusTrader cache."""

    def test_resolved_instrument(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_instrument = MagicMock()
        mock_instrument.id = "EUR/USD.IDEALPRO"
        mock_instrument.price_increment = 0.00001
        mock_instrument.lot_size = 1000
        mock_instrument.multiplier = 1
        mock_instrument.min_quantity = 1000
        mock_instrument.max_quantity = 10_000_000
        mock_instrument.quote_currency = "USD"

        mock_instrument_id_cls = MagicMock()
        mock_instrument_id_cls.from_str.return_value = MagicMock()

        mock_identifiers = MagicMock()
        mock_identifiers.InstrumentId = mock_instrument_id_cls

        monkeypatch.setitem(
            sys.modules, _IDENTIFIERS_MOD, mock_identifiers,
        )

        mock_cache = MagicMock()
        mock_cache.instrument.return_value = mock_instrument

        result = log_resolved_instrument(mock_cache, "EUR/USD.IDEALPRO")

        assert result["id"] == "EUR/USD.IDEALPRO"
        assert result["tick_size"] == str(0.00001)
        assert result["lot_size"] == "1000"
        assert result["multiplier"] == "1"
        assert result["min_quantity"] == "1000"
        assert result["max_quantity"] == "10000000"
        assert result["currency"] == "USD"

        mock_instrument_id_cls.from_str.assert_called_once_with(
            "EUR/USD.IDEALPRO",
        )

    def test_unresolved_instrument(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_instrument_id_cls = MagicMock()
        mock_identifiers = MagicMock()
        mock_identifiers.InstrumentId = mock_instrument_id_cls

        monkeypatch.setitem(
            sys.modules, _IDENTIFIERS_MOD, mock_identifiers,
        )

        mock_cache = MagicMock()
        mock_cache.instrument.return_value = None

        result = log_resolved_instrument(mock_cache, "XAU/USD.SMART")

        assert "error" in result
        assert "XAU/USD.SMART" in result["error"]

    def test_missing_attributes_fallback(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When instrument lacks certain attrs, getattr returns '?'."""
        mock_instrument = MagicMock(spec=[])
        mock_instrument.id = "TEST.EXCHANGE"

        mock_instrument_id_cls = MagicMock()
        mock_identifiers = MagicMock()
        mock_identifiers.InstrumentId = mock_instrument_id_cls

        monkeypatch.setitem(
            sys.modules, _IDENTIFIERS_MOD, mock_identifiers,
        )

        mock_cache = MagicMock()
        mock_cache.instrument.return_value = mock_instrument

        result = log_resolved_instrument(mock_cache, "TEST.EXCHANGE")
        assert result["tick_size"] == "?"
        assert result["lot_size"] == "?"
        assert result["currency"] == "?"
