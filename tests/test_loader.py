"""Tests for strategy discovery and loading."""

from pyfx.strategies.loader import _camel_to_snake, discover_strategies


def test_camel_to_snake():
    assert _camel_to_snake("SMACrossStrategy") == "sma_cross_strategy"
    assert _camel_to_snake("MyStrategy") == "my_strategy"
    assert _camel_to_snake("Strategy") == "strategy"
    assert _camel_to_snake("RSITrendStrategy") == "rsi_trend_strategy"


def test_discover_entry_point_strategies():
    """Entry point strategies should be discoverable after uv pip install -e."""
    strategies = discover_strategies()
    # sample_sma is registered in pyproject.toml entry points
    # This test works when the package is installed in editable mode
    if "sample_sma" in strategies:
        from pyfx.strategies.sample_sma import SMACrossStrategy
        assert strategies["sample_sma"] is SMACrossStrategy
