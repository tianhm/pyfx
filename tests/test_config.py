"""Tests for application configuration."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from pyfx.core.config import PyfxSettings


class TestPyfxSettings:
    def test_strategies_dir_expansion(self, monkeypatch: object) -> None:
        """strategies_dir with ~ should be expanded."""
        mp = pytest.MonkeyPatch()
        mp.setenv("PYFX_STRATEGIES_DIR", "~/my_strategies")
        settings = PyfxSettings()
        assert "~" not in str(settings.strategies_dir)
        assert settings.strategies_dir is not None
        mp.undo()

    def test_default_strategies_dir_is_none(self) -> None:
        settings = PyfxSettings()
        assert settings.strategies_dir is None


# ---------------------------------------------------------------------------
# IB configuration fields
# ---------------------------------------------------------------------------


class TestIBDefaults:
    """IB fields have sensible defaults."""

    def test_ib_defaults(self) -> None:
        s = PyfxSettings()
        assert s.ib_username is None
        assert s.ib_password is None
        assert s.ib_account_id is None
        assert s.ib_host == "127.0.0.1"
        assert s.ib_port == 4002
        assert s.ib_client_id == 1
        assert s.ib_trading_mode == "paper"
        assert s.ib_read_only_api is False
        assert s.ib_gateway_image == "ghcr.io/gnzsnz/ib-gateway:stable"


class TestIBTradingModeValidator:
    """ib_trading_mode must be 'paper' or 'live'."""

    def test_paper_mode(self) -> None:
        s = PyfxSettings(ib_trading_mode="paper")
        assert s.ib_trading_mode == "paper"

    def test_live_mode(self) -> None:
        s = PyfxSettings(ib_trading_mode="live")
        assert s.ib_trading_mode == "live"

    def test_case_insensitive(self) -> None:
        s = PyfxSettings(ib_trading_mode="PAPER")
        assert s.ib_trading_mode == "paper"

    def test_invalid_mode_rejected(self) -> None:
        with pytest.raises(ValidationError, match="ib_trading_mode must be 'paper' or 'live'"):
            PyfxSettings(ib_trading_mode="demo")

    def test_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PYFX_IB_TRADING_MODE", "Live")
        s = PyfxSettings()
        assert s.ib_trading_mode == "live"


# ---------------------------------------------------------------------------
# Account currency validator
# ---------------------------------------------------------------------------


class TestAccountCurrencyValidator:
    """account_currency must be one of the allowed set."""

    def test_default_usd(self) -> None:
        s = PyfxSettings()
        assert s.account_currency == "USD"

    def test_valid_currencies(self) -> None:
        for cur in ("USD", "EUR", "GBP", "CHF"):
            s = PyfxSettings(account_currency=cur)
            assert s.account_currency == cur

    def test_case_insensitive(self) -> None:
        s = PyfxSettings(account_currency="eur")
        assert s.account_currency == "EUR"

    def test_invalid_currency_rejected(self) -> None:
        with pytest.raises(ValidationError, match="account_currency must be one of"):
            PyfxSettings(account_currency="JPY")


# ---------------------------------------------------------------------------
# Risk management validators
# ---------------------------------------------------------------------------


class TestRiskSizingMethodValidator:
    """risk_sizing_method must be 'fixed_fractional' or 'atr_based'."""

    def test_default(self) -> None:
        s = PyfxSettings()
        assert s.risk_sizing_method == "fixed_fractional"

    def test_atr_based(self) -> None:
        s = PyfxSettings(risk_sizing_method="atr_based")
        assert s.risk_sizing_method == "atr_based"

    def test_case_insensitive(self) -> None:
        s = PyfxSettings(risk_sizing_method="ATR_BASED")
        assert s.risk_sizing_method == "atr_based"

    def test_invalid_method_rejected(self) -> None:
        with pytest.raises(ValidationError, match="risk_sizing_method must be one of"):
            PyfxSettings(risk_sizing_method="kelly")


class TestRiskDailyLossLimitValidator:
    """risk_daily_loss_limit must be positive."""

    def test_default(self) -> None:
        s = PyfxSettings()
        assert s.risk_daily_loss_limit == 2000.0

    def test_positive_value(self) -> None:
        s = PyfxSettings(risk_daily_loss_limit=500.0)
        assert s.risk_daily_loss_limit == 500.0

    def test_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="risk_daily_loss_limit must be positive"):
            PyfxSettings(risk_daily_loss_limit=0.0)

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError, match="risk_daily_loss_limit must be positive"):
            PyfxSettings(risk_daily_loss_limit=-100.0)


class TestRiskMaxDrawdownPctValidator:
    """risk_max_drawdown_pct must be between 0 and 100 (exclusive of 0)."""

    def test_default(self) -> None:
        s = PyfxSettings()
        assert s.risk_max_drawdown_pct == 10.0

    def test_boundary_100(self) -> None:
        s = PyfxSettings(risk_max_drawdown_pct=100.0)
        assert s.risk_max_drawdown_pct == 100.0

    def test_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="between 0 and 100"):
            PyfxSettings(risk_max_drawdown_pct=0.0)

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError, match="between 0 and 100"):
            PyfxSettings(risk_max_drawdown_pct=-5.0)

    def test_above_100_rejected(self) -> None:
        with pytest.raises(ValidationError, match="between 0 and 100"):
            PyfxSettings(risk_max_drawdown_pct=101.0)


class TestRiskPositionSizePctValidator:
    """risk_position_size_pct must be between 0 and 100 (exclusive of 0)."""

    def test_default(self) -> None:
        s = PyfxSettings()
        assert s.risk_position_size_pct == 2.0

    def test_boundary_100(self) -> None:
        s = PyfxSettings(risk_position_size_pct=100.0)
        assert s.risk_position_size_pct == 100.0

    def test_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="between 0 and 100"):
            PyfxSettings(risk_position_size_pct=0.0)

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError, match="between 0 and 100"):
            PyfxSettings(risk_position_size_pct=-1.0)

    def test_above_100_rejected(self) -> None:
        with pytest.raises(ValidationError, match="between 0 and 100"):
            PyfxSettings(risk_position_size_pct=200.0)


class TestRiskMaxPositionsValidator:
    """risk_max_positions must be >= 1."""

    def test_default(self) -> None:
        s = PyfxSettings()
        assert s.risk_max_positions == 3

    def test_one_allowed(self) -> None:
        s = PyfxSettings(risk_max_positions=1)
        assert s.risk_max_positions == 1

    def test_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="risk_max_positions must be at least 1"):
            PyfxSettings(risk_max_positions=0)

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError, match="risk_max_positions must be at least 1"):
            PyfxSettings(risk_max_positions=-1)


class TestRiskFieldDefaults:
    """Risk fields have expected defaults."""

    def test_defaults(self) -> None:
        s = PyfxSettings()
        assert s.risk_max_position_size == Decimal("100000")
        assert s.risk_max_notional_per_order == 500_000


# ---------------------------------------------------------------------------
# validate_ib_config()
# ---------------------------------------------------------------------------


class TestValidateIBConfig:
    """validate_ib_config returns appropriate warnings."""

    def test_no_account_id_warning(self) -> None:
        s = PyfxSettings()
        warnings = s.validate_ib_config()
        assert any("PYFX_IB_ACCOUNT_ID is not set" in w for w in warnings)

    def test_paper_mode_non_du_account(self) -> None:
        s = PyfxSettings(ib_account_id="U1234567", ib_trading_mode="paper")
        warnings = s.validate_ib_config()
        assert any("usually start with 'DU'" in w for w in warnings)

    def test_paper_mode_du_account_no_warning(self) -> None:
        s = PyfxSettings(ib_account_id="DU1234567", ib_trading_mode="paper")
        warnings = s.validate_ib_config()
        assert not any("usually start with 'DU'" in w for w in warnings)
        assert not any("PYFX_IB_ACCOUNT_ID is not set" in w for w in warnings)

    def test_live_port_paper_mode_warning(self) -> None:
        s = PyfxSettings(ib_account_id="DU1234567", ib_port=4001, ib_trading_mode="paper")
        warnings = s.validate_ib_config()
        assert any("typically used for LIVE trading" in w for w in warnings)

    def test_tws_live_port_paper_mode_warning(self) -> None:
        s = PyfxSettings(ib_account_id="DU1234567", ib_port=7496, ib_trading_mode="paper")
        warnings = s.validate_ib_config()
        assert any("typically used for LIVE trading" in w for w in warnings)

    def test_live_mode_warning(self) -> None:
        s = PyfxSettings(ib_account_id="U1234567", ib_trading_mode="live")
        warnings = s.validate_ib_config()
        assert any("trading_mode is 'live'" in w for w in warnings)

    def test_read_only_api_warning(self) -> None:
        s = PyfxSettings(ib_account_id="DU1234567", ib_read_only_api=True)
        warnings = s.validate_ib_config()
        assert any("read_only_api is True" in w for w in warnings)

    def test_clean_paper_config(self) -> None:
        """A properly configured paper account should only have minimal warnings."""
        s = PyfxSettings(
            ib_account_id="DU1234567",
            ib_trading_mode="paper",
            ib_port=4002,
            ib_read_only_api=False,
        )
        warnings = s.validate_ib_config()
        assert warnings == []


# ---------------------------------------------------------------------------
# get_log_dir()
# ---------------------------------------------------------------------------


class TestGetLogDir:
    """get_log_dir returns an existing directory."""

    def test_default_log_dir(self, tmp_path: Path) -> None:
        s = PyfxSettings(data_dir=tmp_path)
        d = s.get_log_dir()
        assert d.exists()
        assert d == tmp_path / "logs"

    def test_explicit_log_dir(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "custom_logs"
        s = PyfxSettings(log_dir=log_dir)
        d = s.get_log_dir()
        assert d.exists()
        assert d == log_dir


# ---------------------------------------------------------------------------
# Path expansion via model_validator
# ---------------------------------------------------------------------------


class TestPathExpansion:
    """Model validator expands ~ in Path fields."""

    def test_data_dir_expanded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PYFX_DATA_DIR", "~/pyfx_data")
        s = PyfxSettings()
        assert "~" not in str(s.data_dir)

    def test_catalog_dir_expanded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PYFX_CATALOG_DIR", "~/pyfx_catalog")
        s = PyfxSettings()
        assert "~" not in str(s.catalog_dir)

    def test_db_path_expanded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PYFX_DB_PATH", "~/pyfx.db")
        s = PyfxSettings()
        assert "~" not in str(s.db_path)

    def test_log_dir_expanded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PYFX_LOG_DIR", "~/pyfx_logs")
        s = PyfxSettings()
        assert s.log_dir is not None
        assert "~" not in str(s.log_dir)
