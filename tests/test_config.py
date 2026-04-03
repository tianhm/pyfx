"""Tests for application configuration."""

from __future__ import annotations

from pyfx.core.config import PyfxSettings


class TestPyfxSettings:
    def test_strategies_dir_expansion(self, monkeypatch: object) -> None:
        """strategies_dir with ~ should be expanded."""
        import pytest

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("PYFX_STRATEGIES_DIR", "~/my_strategies")
        settings = PyfxSettings()
        assert "~" not in str(settings.strategies_dir)
        assert settings.strategies_dir is not None
        monkeypatch.undo()

    def test_default_strategies_dir_is_none(self) -> None:
        settings = PyfxSettings()
        assert settings.strategies_dir is None
