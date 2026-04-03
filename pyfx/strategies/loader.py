"""Dynamic strategy discovery via entry points and directory scanning."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyfx.strategies.base import PyfxStrategy


def _load_entry_point_strategies() -> dict[str, type[PyfxStrategy]]:
    """Discover strategies registered as 'pyfx.strategies' entry points."""
    strategies: dict[str, type[PyfxStrategy]] = {}
    eps = entry_points(group="pyfx.strategies")
    for ep in eps:
        strategies[ep.name] = ep.load()
    return strategies


def _load_directory_strategies(directory: Path) -> dict[str, type[PyfxStrategy]]:
    """Discover strategy classes in Python files within a directory.

    Any class that inherits from PyfxStrategy is registered,
    using its class name in snake_case as the key.
    """
    from pyfx.strategies.base import PyfxStrategy as Base

    strategies: dict[str, type[Base]] = {}

    if not directory.is_dir():
        return strategies

    for py_file in sorted(directory.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        module_name = f"pyfx_ext_strategies.{py_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if spec is None or spec.loader is None:  # pragma: no cover
            continue

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, Base)
                and attr is not Base
            ):
                # Convert CamelCase to snake_case for the key
                key = _camel_to_snake(attr_name)
                strategies[key] = attr

    return strategies


def _camel_to_snake(name: str) -> str:
    """Convert CamelCase to snake_case, handling acronyms like RSI or EMA."""
    import re

    # Insert underscore between a lowercase/digit and an uppercase letter
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    # Insert underscore between consecutive uppercase letters followed by lowercase
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return s.lower()


def discover_strategies(extra_dir: Path | None = None) -> dict[str, type[PyfxStrategy]]:
    """Discover all available strategies from entry points and optional directory.

    Returns a dict mapping strategy name -> strategy class.
    """
    strategies = _load_entry_point_strategies()

    if extra_dir is not None:
        dir_strategies = _load_directory_strategies(extra_dir)
        strategies.update(dir_strategies)

    return strategies


def get_strategy(name: str, extra_dir: Path | None = None) -> type[PyfxStrategy]:
    """Get a strategy class by name. Raises KeyError if not found."""
    strategies = discover_strategies(extra_dir)
    if name not in strategies:
        available = ", ".join(sorted(strategies.keys()))
        raise KeyError(f"Strategy '{name}' not found. Available: {available}")
    return strategies[name]
