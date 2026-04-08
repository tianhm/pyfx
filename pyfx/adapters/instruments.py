"""Map pyfx instrument names to Interactive Brokers contract specs."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from nautilus_trader.adapters.interactive_brokers.common import IBContract
    from nautilus_trader.model.identifiers import InstrumentId


class InstrumentMeta(NamedTuple):
    """Display/validation metadata for a supported instrument.

    These are *expected* values; IB resolves the authoritative specs at runtime.
    """

    tick_size: float
    min_quantity: float
    lot_size: float
    pip_value: float


_FX = InstrumentMeta(tick_size=0.00001, min_quantity=1000, lot_size=1000, pip_value=0.0001)
_JPY = InstrumentMeta(tick_size=0.001, min_quantity=1000, lot_size=1000, pip_value=0.01)
_GOLD = InstrumentMeta(tick_size=0.01, min_quantity=1, lot_size=1, pip_value=0.01)

INSTRUMENT_METADATA: dict[str, InstrumentMeta] = {
    "EUR/USD": _FX,
    "GBP/USD": _FX,
    "USD/JPY": _JPY,
    "USD/CHF": _FX,
    "EUR/GBP": _FX,
    "AUD/USD": _FX,
    "NZD/USD": _FX,
    "XAU/USD": _GOLD,
}


def get_instrument_meta(instrument: str) -> InstrumentMeta:
    """Return metadata for a supported instrument.

    Raises ``KeyError`` if the instrument is not supported.
    """
    if instrument not in INSTRUMENT_METADATA:
        supported = ", ".join(sorted(INSTRUMENT_METADATA.keys()))
        msg = f"Unsupported instrument '{instrument}'. Supported: {supported}"
        raise KeyError(msg)
    return INSTRUMENT_METADATA[instrument]


def log_resolved_instrument(cache: object, instrument_id_str: str) -> dict[str, str]:
    """Read resolved instrument specs from a NautilusTrader cache.

    Returns a dict of spec fields for logging/display.
    """
    from nautilus_trader.model.identifiers import InstrumentId

    iid = InstrumentId.from_str(instrument_id_str)
    instrument = cache.instrument(iid)  # type: ignore[attr-defined]
    if instrument is None:
        return {"error": f"Instrument {instrument_id_str} not resolved"}
    return {
        "id": str(instrument.id),
        "tick_size": str(getattr(instrument, "price_increment", "?")),
        "lot_size": str(getattr(instrument, "lot_size", "?")),
        "multiplier": str(getattr(instrument, "multiplier", "?")),
        "min_quantity": str(getattr(instrument, "min_quantity", "?")),
        "max_quantity": str(getattr(instrument, "max_quantity", "?")),
        "currency": str(getattr(instrument, "quote_currency", "?")),
    }


def _build_contracts() -> dict[str, IBContract]:
    """Lazily build the instrument mapping (requires ibapi)."""
    from nautilus_trader.adapters.interactive_brokers.common import IBContract

    return {
        "EUR/USD": IBContract(
            secType="CASH", exchange="IDEALPRO", symbol="EUR", currency="USD",
        ),
        "GBP/USD": IBContract(
            secType="CASH", exchange="IDEALPRO", symbol="GBP", currency="USD",
        ),
        "USD/JPY": IBContract(
            secType="CASH", exchange="IDEALPRO", symbol="USD", currency="JPY",
        ),
        "USD/CHF": IBContract(
            secType="CASH", exchange="IDEALPRO", symbol="USD", currency="CHF",
        ),
        "EUR/GBP": IBContract(
            secType="CASH", exchange="IDEALPRO", symbol="EUR", currency="GBP",
        ),
        "AUD/USD": IBContract(
            secType="CASH", exchange="IDEALPRO", symbol="AUD", currency="USD",
        ),
        "NZD/USD": IBContract(
            secType="CASH", exchange="IDEALPRO", symbol="NZD", currency="USD",
        ),
        "XAU/USD": IBContract(
            secType="CMDTY", exchange="SMART", symbol="XAUUSD", currency="USD",
        ),
    }


# Mapping from pyfx instrument name to IB simplified instrument-id string.
# FX pairs use IDEALPRO, commodities use SMART.
IB_INSTRUMENT_IDS: dict[str, str] = {
    "EUR/USD": "EUR/USD.IDEALPRO",
    "GBP/USD": "GBP/USD.IDEALPRO",
    "USD/JPY": "USD/JPY.IDEALPRO",
    "USD/CHF": "USD/CHF.IDEALPRO",
    "EUR/GBP": "EUR/GBP.IDEALPRO",
    "AUD/USD": "AUD/USD.IDEALPRO",
    "NZD/USD": "NZD/USD.IDEALPRO",
    "XAU/USD": "XAUUSD.SMART",
}


def get_ib_contract(instrument: str) -> IBContract:
    """Return the IBContract for a pyfx instrument name.

    Raises ``KeyError`` if the instrument is not supported.
    """
    contracts = _build_contracts()
    if instrument not in contracts:
        supported = ", ".join(sorted(contracts.keys()))
        msg = f"Unsupported instrument '{instrument}'. Supported: {supported}"
        raise KeyError(msg)
    return contracts[instrument]


def get_ib_instrument_id(instrument: str) -> InstrumentId:
    """Return the NautilusTrader InstrumentId for a pyfx instrument name."""
    from nautilus_trader.model.identifiers import InstrumentId

    if instrument not in IB_INSTRUMENT_IDS:
        supported = ", ".join(sorted(IB_INSTRUMENT_IDS.keys()))
        msg = f"Unsupported instrument '{instrument}'. Supported: {supported}"
        raise KeyError(msg)
    return InstrumentId.from_str(IB_INSTRUMENT_IDS[instrument])


def get_ib_instrument_id_str(instrument: str) -> str:
    """Return the IB instrument-id string for a pyfx instrument name."""
    if instrument not in IB_INSTRUMENT_IDS:
        supported = ", ".join(sorted(IB_INSTRUMENT_IDS.keys()))
        msg = f"Unsupported instrument '{instrument}'. Supported: {supported}"
        raise KeyError(msg)
    return IB_INSTRUMENT_IDS[instrument]


def list_supported_instruments() -> list[str]:
    """Return the list of supported pyfx instrument names."""
    return sorted(IB_INSTRUMENT_IDS.keys())
