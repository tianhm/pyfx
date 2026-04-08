"""Interactive Brokers adapter factory for NautilusTrader TradingNode."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nautilus_trader.config import ImportableActorConfig, TradingNodeConfig
    from nautilus_trader.trading.config import ImportableStrategyConfig

    from pyfx.core.config import PyfxSettings


def build_trading_node_config(
    settings: PyfxSettings,
    strategy_configs: list[ImportableStrategyConfig],
    actor_configs: list[ImportableActorConfig] | None = None,
    instrument_ids: list[str] | None = None,
) -> TradingNodeConfig:
    """Build a TradingNodeConfig wired to Interactive Brokers.

    This is the single point of IB integration -- the rest of the live-trading
    system is broker-agnostic.
    """
    from nautilus_trader.adapters.interactive_brokers.config import (
        DockerizedIBGatewayConfig,
        InteractiveBrokersDataClientConfig,
        InteractiveBrokersExecClientConfig,
        InteractiveBrokersInstrumentProviderConfig,
    )
    from nautilus_trader.common.config import LoggingConfig
    from nautilus_trader.config import (
        LiveDataEngineConfig,
        LiveExecEngineConfig,
        LiveRiskEngineConfig,
        TradingNodeConfig,
    )
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.persistence.config import StreamingConfig

    # --- Docker gateway (manages IB Gateway container lifecycle) ---
    gateway_config = DockerizedIBGatewayConfig(
        username=settings.ib_username,
        password=settings.ib_password,
        trading_mode=settings.ib_trading_mode,  # type: ignore[arg-type]
        read_only_api=settings.ib_read_only_api,
    )

    # --- Instrument provider ---
    load_ids: frozenset[InstrumentId] = frozenset()
    if instrument_ids:
        load_ids = frozenset(InstrumentId.from_str(iid) for iid in instrument_ids)

    instrument_provider_config = InteractiveBrokersInstrumentProviderConfig(
        load_ids=load_ids,
    )

    # --- Data client ---
    data_client_config = InteractiveBrokersDataClientConfig(
        ibg_host=settings.ib_host,
        ibg_port=settings.ib_port,
        ibg_client_id=settings.ib_client_id,
        dockerized_gateway=gateway_config,
        instrument_provider=instrument_provider_config,
    )

    # --- Execution client ---
    exec_client_config = InteractiveBrokersExecClientConfig(
        ibg_host=settings.ib_host,
        ibg_port=settings.ib_port,
        ibg_client_id=settings.ib_client_id,
        account_id=settings.ib_account_id,
        dockerized_gateway=gateway_config,
        instrument_provider=instrument_provider_config,
    )

    # --- Native risk engine ---
    risk_config = LiveRiskEngineConfig(
        max_notional_per_order={
            settings.account_currency: settings.risk_max_notional_per_order,
        },
        max_order_submit_rate="10/00:00:01",
    )

    # --- Streaming persistence (all events to Parquet) ---
    catalog_path = str(settings.catalog_dir / "live")
    streaming_config = StreamingConfig(catalog_path=catalog_path)

    # --- Logging ---
    log_dir = settings.get_log_dir()
    logging_config = LoggingConfig(
        log_level="INFO",
        log_level_file="DEBUG",
        log_directory=str(log_dir),
        log_file_name="paper_trading",
        log_colors=True,
    )

    # --- Assemble TradingNodeConfig ---
    return TradingNodeConfig(
        data_clients={"IB": data_client_config},
        exec_clients={"IB": exec_client_config},
        data_engine=LiveDataEngineConfig(debug=False),
        risk_engine=risk_config,
        exec_engine=LiveExecEngineConfig(debug=False),
        streaming=streaming_config,
        logging=logging_config,
        strategies=strategy_configs,
        actors=actor_configs or [],
        save_state=True,
        load_state=True,
        timeout_connection=60.0,
        timeout_reconciliation=30.0,
        timeout_portfolio=30.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
    )
