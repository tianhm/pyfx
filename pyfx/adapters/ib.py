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
    *,
    client_id: int | None = None,
    catalog_path: str | None = None,
    log_file_name: str | None = None,
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
    from nautilus_trader.live.config import RoutingConfig
    from nautilus_trader.model.identifiers import InstrumentId

    # --- Docker gateway (manages IB Gateway container lifecycle) ---
    gateway_config = DockerizedIBGatewayConfig(
        username=settings.ib_username,
        password=settings.ib_password,
        trading_mode=settings.ib_trading_mode,  # type: ignore[arg-type]
        read_only_api=settings.ib_read_only_api,
    )

    # --- Instrument provider ---
    # Use load_contracts (IBContract objects) for reliable instrument
    # resolution.  load_ids alone can fail because the instrument isn't
    # resolved in time before strategies subscribe to bars.
    from nautilus_trader.adapters.interactive_brokers.common import IBContract as _IBContract

    from pyfx.adapters.instruments import get_ib_contract

    load_contracts: frozenset[_IBContract] = frozenset()
    load_ids: frozenset[InstrumentId] = frozenset()
    if instrument_ids:
        load_ids = frozenset(InstrumentId.from_str(iid) for iid in instrument_ids)
        # Also resolve IBContract objects from pyfx instrument mapping
        from pyfx.adapters.instruments import IB_INSTRUMENT_IDS

        # Reverse-map: IB id string -> pyfx name
        reverse_map = {v: k for k, v in IB_INSTRUMENT_IDS.items()}
        contracts = []
        for iid_str in instrument_ids:
            pyfx_name = reverse_map.get(iid_str)
            if pyfx_name:
                contracts.append(get_ib_contract(pyfx_name))
        if contracts:
            load_contracts = frozenset(contracts)

    instrument_provider_config = InteractiveBrokersInstrumentProviderConfig(
        load_ids=load_ids,
        load_contracts=load_contracts if load_contracts else None,
    )

    # --- Data client (default=True routes all venues through IB) ---
    # When using DockerizedIBGatewayConfig, ibg_port must be None (managed
    # by the Docker container).  ibg_host is also managed by the container.
    effective_client_id = client_id if client_id is not None else settings.ib_client_id
    data_client_config = InteractiveBrokersDataClientConfig(
        ibg_host=settings.ib_host,
        ibg_port=None,
        ibg_client_id=effective_client_id,
        dockerized_gateway=gateway_config,
        instrument_provider=instrument_provider_config,
        routing=RoutingConfig(default=True),
    )

    # --- Execution client ---
    exec_client_config = InteractiveBrokersExecClientConfig(
        ibg_host=settings.ib_host,
        ibg_port=None,
        ibg_client_id=effective_client_id,
        account_id=settings.ib_account_id,
        dockerized_gateway=gateway_config,
        instrument_provider=instrument_provider_config,
        routing=RoutingConfig(default=True),
    )

    # --- Native risk engine ---
    # Note: max_notional_per_order keys must be InstrumentId strings (not
    # currency codes).  Our RiskMonitorActor enforces position limits, so we
    # only set the order-submit rate limiter here.
    risk_config = LiveRiskEngineConfig(
        max_order_submit_rate="10/00:00:01",
    )

    # --- Streaming persistence ---
    # Disabled: NautilusTrader 1.224.0 StreamingWriter crashes on
    # ExecutionMassStatus events during reconciliation (KeyError on schema).
    # Our Django DB persistence (RiskMonitorActor + events.py) handles all
    # trade/event storage, so Parquet streaming is not needed.
    streaming_config = None

    # --- Logging ---
    log_dir = settings.get_log_dir()
    effective_log_name = log_file_name or "paper_trading"
    logging_config = LoggingConfig(
        log_level="INFO",
        log_level_file="DEBUG",
        log_directory=str(log_dir),
        log_file_name=effective_log_name,
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
        timeout_connection=120.0,
        timeout_reconciliation=30.0,
        timeout_portfolio=30.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
    )
