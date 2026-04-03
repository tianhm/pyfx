"""Instrument specification registry.

Centralizes all instrument-specific knowledge (precision, pip size, pricing,
exchange rate needs) so the rest of the codebase can look up any instrument
without hardcoded assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass

# Currencies whose pairs use 3-decimal pricing (pip = 0.01)
_JPY_LIKE_CURRENCIES = frozenset({"JPY"})


@dataclass(frozen=True)
class InstrumentSpec:
    """Specification for an instrument's precision, pricing, and data generation.

    Attributes:
        price_precision: Number of decimal places for price display.
        tick_scheme: NautilusTrader tick scheme name (empty for default FX).
        lot_size: Standard lot size string for NautilusTrader.
        min_quantity: Minimum order quantity string.
        is_fx: If True, use TestInstrumentProvider.default_fx_ccy().
        base_price: Typical price level for synthetic data generation.
        volatility: Per-bar noise standard deviation for synthetic data.
        spread: Typical half-spread for synthetic data.
        needs_quote_conversion: True when quote currency != USD (P&L needs
            conversion to account currency).
    """

    price_precision: int
    tick_scheme: str
    lot_size: str
    min_quantity: str
    is_fx: bool
    base_price: float
    volatility: float
    spread: float
    needs_quote_conversion: bool


def _fx_5dec(
    base_price: float = 1.10,
    needs_quote_conversion: bool = False,
) -> InstrumentSpec:
    """Standard FX pair with 5-decimal pricing (e.g. EUR/USD)."""
    return InstrumentSpec(
        price_precision=5,
        tick_scheme="",
        lot_size="1000",
        min_quantity="1000",
        is_fx=True,
        base_price=base_price,
        volatility=0.0001,
        spread=0.00005,
        needs_quote_conversion=needs_quote_conversion,
    )


def _fx_3dec(
    base_price: float = 150.0,
    needs_quote_conversion: bool = True,
) -> InstrumentSpec:
    """JPY FX pair with 3-decimal pricing (e.g. USD/JPY)."""
    return InstrumentSpec(
        price_precision=3,
        tick_scheme="",
        lot_size="1000",
        min_quantity="1000",
        is_fx=True,
        base_price=base_price,
        volatility=0.02,
        spread=0.005,
        needs_quote_conversion=needs_quote_conversion,
    )


def _commodity(
    base_price: float,
    volatility: float = 0.02,
    spread: float = 0.03,
) -> InstrumentSpec:
    """Commodity with 2-decimal pricing (e.g. XAU/USD, OIL/USD)."""
    return InstrumentSpec(
        price_precision=2,
        tick_scheme="FOREX_3DECIMAL",
        lot_size="1",
        min_quantity="1",
        is_fx=False,
        base_price=base_price,
        volatility=volatility,
        spread=spread,
        needs_quote_conversion=False,
    )


# Known instrument specifications.
# Key: instrument string (e.g. "EUR/USD").
_REGISTRY: dict[str, InstrumentSpec] = {
    # -- Standard FX (5-decimal, USD-quoted) -----------------------------------
    "EUR/USD": _fx_5dec(),
    "GBP/USD": _fx_5dec(base_price=1.27),
    "AUD/USD": _fx_5dec(base_price=0.65),
    "NZD/USD": _fx_5dec(base_price=0.60),
    # -- JPY pairs (3-decimal) -------------------------------------------------
    "USD/JPY": _fx_3dec(),
    "EUR/JPY": _fx_3dec(base_price=165.0),
    "GBP/JPY": _fx_3dec(base_price=190.0),
    # -- Commodities (2-decimal, USD-quoted) -----------------------------------
    "XAU/USD": _commodity(base_price=3000.0, volatility=0.50, spread=0.15),
    "OIL/USD": _commodity(base_price=75.0),
    "WTI/USD": _commodity(base_price=75.0),
    "BCO/USD": _commodity(base_price=80.0),
}


def get_instrument_spec(instrument_str: str) -> InstrumentSpec:
    """Look up the specification for an instrument.

    Returns the spec from the registry if known, otherwise synthesizes
    a default based on the quote currency:
    - JPY quote -> 3-decimal FX with conversion
    - USD quote -> 5-decimal FX without conversion
    - Other quote -> 5-decimal FX with conversion

    Args:
        instrument_str: Instrument identifier, e.g. ``"EUR/USD"``.

    Returns:
        InstrumentSpec for the instrument.
    """
    spec = _REGISTRY.get(instrument_str)
    if spec is not None:
        return spec

    # Auto-detect from quote currency
    quote = instrument_str[-3:]
    if quote in _JPY_LIKE_CURRENCIES:
        return _fx_3dec()

    return _fx_5dec(needs_quote_conversion=(quote != "USD"))
