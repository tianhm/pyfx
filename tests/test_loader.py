"""Tests for strategy discovery and loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyfx.strategies.base import PyfxStrategy
from pyfx.strategies.loader import (
    _camel_to_snake,
    _load_directory_strategies,
    discover_strategies,
    get_strategy,
)


def test_camel_to_snake() -> None:
    assert _camel_to_snake("SMACrossStrategy") == "sma_cross_strategy"
    assert _camel_to_snake("MyStrategy") == "my_strategy"
    assert _camel_to_snake("Strategy") == "strategy"
    assert _camel_to_snake("RSITrendStrategy") == "rsi_trend_strategy"


def test_base_chart_indicators_default() -> None:
    """Base PyfxStrategy.chart_indicators() returns empty list."""
    assert PyfxStrategy.chart_indicators() == []


def test_discover_entry_point_strategies() -> None:
    """Entry point strategies should be discoverable after uv pip install -e."""
    strategies = discover_strategies()
    from pyfx.strategies.sample_sma import SMACrossStrategy

    assert "sample_sma" in strategies
    assert strategies["sample_sma"] is SMACrossStrategy


def test_discover_with_extra_dir(tmp_path: Path) -> None:
    """Strategies from an extra directory should also be discovered."""
    # Create a minimal strategy file
    (tmp_path / "my_strat.py").write_text(
        "from pyfx.strategies.base import PyfxStrategy, PyfxStrategyConfig\n"
        "class MyTestConfig(PyfxStrategyConfig, frozen=True):\n"
        "    pass\n"
        "class MyTestStrategy(PyfxStrategy):\n"
        "    def __init__(self, config: MyTestConfig) -> None:\n"
        "        super().__init__(config)\n"
    )
    strategies = discover_strategies(tmp_path)
    assert "my_test_strategy" in strategies


def test_load_directory_strategies_nonexistent() -> None:
    """Non-existent directory returns empty dict."""
    strategies = _load_directory_strategies(Path("/nonexistent"))
    assert strategies == {}


def test_load_directory_skips_underscore(tmp_path: Path) -> None:
    """Files starting with _ should be skipped."""
    (tmp_path / "_private.py").write_text("class Foo: pass\n")
    strategies = _load_directory_strategies(tmp_path)
    assert strategies == {}


def test_load_directory_skips_bad_spec(tmp_path: Path) -> None:
    """Files with no valid spec should be skipped."""
    # Create a file that exists but whose spec might not load normally
    (tmp_path / "plain.py").write_text("x = 1\n")
    strategies = _load_directory_strategies(tmp_path)
    assert strategies == {}


def test_get_strategy_found() -> None:
    cls = get_strategy("sample_sma")
    from pyfx.strategies.sample_sma import SMACrossStrategy

    assert cls is SMACrossStrategy


def test_get_strategy_not_found() -> None:
    with pytest.raises(KeyError, match="not found"):
        get_strategy("nonexistent_strategy_xyz")
