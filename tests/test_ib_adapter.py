"""Tests for pyfx.adapters.ib – IB adapter factory."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from pyfx.adapters.ib import build_trading_node_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides: object) -> MagicMock:
    """Create a mock PyfxSettings with sensible defaults."""
    settings = MagicMock()
    settings.ib_username = "testuser"
    settings.ib_password = "testpass"
    settings.ib_trading_mode = "paper"
    settings.ib_read_only_api = False
    settings.ib_host = "127.0.0.1"
    settings.ib_port = 4002
    settings.ib_client_id = 1
    settings.ib_account_id = "DU1234567"
    settings.account_currency = "USD"
    settings.risk_max_notional_per_order = 500_000
    settings.catalog_dir = Path("/tmp/test-catalog")
    settings.get_log_dir.return_value = Path("/tmp/test-logs")
    for k, v in overrides.items():
        setattr(settings, k, v)
    return settings


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildTradingNodeConfig:
    def test_returns_trading_node_config(self) -> None:
        from nautilus_trader.config import TradingNodeConfig

        result = build_trading_node_config(
            settings=_make_settings(),
            strategy_configs=[MagicMock()],
        )
        assert isinstance(result, TradingNodeConfig)

    def test_data_and_exec_clients_have_ib_key(self) -> None:
        result = build_trading_node_config(
            settings=_make_settings(),
            strategy_configs=[MagicMock()],
        )
        assert "IB" in result.data_clients
        assert "IB" in result.exec_clients

    def test_strategies_passed_through(self) -> None:
        strat1 = MagicMock()
        strat2 = MagicMock()
        result = build_trading_node_config(
            settings=_make_settings(),
            strategy_configs=[strat1, strat2],
        )
        assert result.strategies == [strat1, strat2]

    def test_actors_passed_through(self) -> None:
        actor = MagicMock()
        result = build_trading_node_config(
            settings=_make_settings(),
            strategy_configs=[MagicMock()],
            actor_configs=[actor],
        )
        assert result.actors == [actor]

    def test_no_actors_defaults_to_empty_list(self) -> None:
        result = build_trading_node_config(
            settings=_make_settings(),
            strategy_configs=[MagicMock()],
        )
        assert result.actors == []

    def test_timeouts(self) -> None:
        result = build_trading_node_config(
            settings=_make_settings(),
            strategy_configs=[MagicMock()],
        )
        assert result.timeout_connection == 60.0
        assert result.timeout_reconciliation == 30.0
        assert result.timeout_portfolio == 30.0
        assert result.timeout_disconnection == 10.0
        assert result.timeout_post_stop == 5.0

    def test_save_and_load_state(self) -> None:
        result = build_trading_node_config(
            settings=_make_settings(),
            strategy_configs=[MagicMock()],
        )
        assert result.save_state is True
        assert result.load_state is True

    def test_streaming_catalog_path(self) -> None:
        result = build_trading_node_config(
            settings=_make_settings(catalog_dir=Path("/data/catalog")),
            strategy_configs=[MagicMock()],
        )
        assert result.streaming is not None
        assert result.streaming.catalog_path == "/data/catalog/live"

    def test_logging_config(self) -> None:
        settings = _make_settings()
        settings.get_log_dir.return_value = Path("/my/logs")
        result = build_trading_node_config(
            settings=settings,
            strategy_configs=[MagicMock()],
        )
        assert result.logging is not None
        assert result.logging.log_level == "INFO"
        assert result.logging.log_level_file == "DEBUG"
        assert result.logging.log_directory == "/my/logs"
        assert result.logging.log_file_name == "paper_trading"
        assert result.logging.log_colors is True

    def test_risk_engine_max_notional(self) -> None:
        result = build_trading_node_config(
            settings=_make_settings(
                account_currency="EUR",
                risk_max_notional_per_order=250_000,
            ),
            strategy_configs=[MagicMock()],
        )
        assert result.risk_engine is not None
        assert result.risk_engine.max_notional_per_order == {"EUR": 250_000}

    def test_risk_engine_order_submit_rate(self) -> None:
        result = build_trading_node_config(
            settings=_make_settings(),
            strategy_configs=[MagicMock()],
        )
        assert result.risk_engine is not None
        assert result.risk_engine.max_order_submit_rate == "10/00:00:01"

    def test_gateway_credentials(self) -> None:
        result = build_trading_node_config(
            settings=_make_settings(
                ib_username="myuser",
                ib_password="mypass",
                ib_trading_mode="paper",
                ib_read_only_api=True,
            ),
            strategy_configs=[MagicMock()],
        )
        data_config = result.data_clients["IB"]
        gw = data_config.dockerized_gateway
        assert gw is not None
        assert gw.username == "myuser"
        assert gw.password == "mypass"
        assert gw.trading_mode == "paper"
        assert gw.read_only_api is True

    def test_host_and_port(self) -> None:
        result = build_trading_node_config(
            settings=_make_settings(ib_host="10.0.0.1", ib_port=7497, ib_client_id=5),
            strategy_configs=[MagicMock()],
        )
        data_config = result.data_clients["IB"]
        assert data_config.ibg_host == "10.0.0.1"
        assert data_config.ibg_port == 7497
        assert data_config.ibg_client_id == 5

        exec_config = result.exec_clients["IB"]
        assert exec_config.ibg_host == "10.0.0.1"
        assert exec_config.ibg_port == 7497
        assert exec_config.ibg_client_id == 5

    def test_account_id_on_exec_client(self) -> None:
        result = build_trading_node_config(
            settings=_make_settings(ib_account_id="DU9999999"),
            strategy_configs=[MagicMock()],
        )
        exec_config = result.exec_clients["IB"]
        assert exec_config.account_id == "DU9999999"

    def test_instrument_ids_loaded(self) -> None:
        from nautilus_trader.model.identifiers import InstrumentId

        result = build_trading_node_config(
            settings=_make_settings(),
            strategy_configs=[MagicMock()],
            instrument_ids=["EUR/USD.IDEALPRO", "XAUUSD.SMART"],
        )
        data_config = result.data_clients["IB"]
        load_ids = data_config.instrument_provider.load_ids
        assert len(load_ids) == 2
        assert InstrumentId.from_str("EUR/USD.IDEALPRO") in load_ids
        assert InstrumentId.from_str("XAUUSD.SMART") in load_ids

    def test_no_instrument_ids_empty_frozenset(self) -> None:
        result = build_trading_node_config(
            settings=_make_settings(),
            strategy_configs=[MagicMock()],
            instrument_ids=None,
        )
        data_config = result.data_clients["IB"]
        assert len(data_config.instrument_provider.load_ids) == 0

    def test_data_engine_debug_off(self) -> None:
        result = build_trading_node_config(
            settings=_make_settings(),
            strategy_configs=[MagicMock()],
        )
        assert result.data_engine is not None
        assert result.data_engine.debug is False

    def test_exec_engine_debug_off(self) -> None:
        result = build_trading_node_config(
            settings=_make_settings(),
            strategy_configs=[MagicMock()],
        )
        assert result.exec_engine is not None
        assert result.exec_engine.debug is False
