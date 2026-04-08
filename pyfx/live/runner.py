"""Live/paper trading runner using NautilusTrader TradingNode."""

from __future__ import annotations

import os
import signal
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyfx.core.config import PyfxSettings
    from pyfx.core.types import LiveTradingConfig


def start_live_trading(  # pragma: no cover
    config: LiveTradingConfig,
    settings: PyfxSettings,
) -> None:
    """Start a live/paper trading session.

    Blocks until the node is stopped (via SIGINT/SIGTERM or ``pyfx live stop``).
    """
    _setup_django()
    from nautilus_trader.config import ImportableActorConfig
    from nautilus_trader.live.node import TradingNode
    from nautilus_trader.trading.config import ImportableStrategyConfig

    from pyfx.adapters.ib import build_trading_node_config
    from pyfx.adapters.instruments import get_ib_instrument_id_str
    from pyfx.live.risk import RiskMonitorActor, RiskMonitorConfig
    from pyfx.strategies.loader import discover_strategies, find_strategy_config_class
    from pyfx.web.dashboard.models import PaperTradingSession

    # --- Resolve instruments ---
    instrument_id_strs = [get_ib_instrument_id_str(i) for i in config.instruments]

    # --- Validate strategy exists ---
    strategies = discover_strategies(settings.strategies_dir)
    if config.strategy not in strategies:
        available = ", ".join(sorted(strategies.keys()))
        msg = f"Unknown strategy '{config.strategy}'. Available: {available}"
        raise ValueError(msg)

    # --- Create DB session ---
    session = PaperTradingSession.objects.create(
        status=PaperTradingSession.STATUS_RUNNING,
        strategy=config.strategy,
        instrument=",".join(config.instruments),
        bar_type=config.bar_type,
        started_at=datetime.now(UTC),
        account_currency=config.account_currency,
        account_id=settings.ib_account_id or "",
        config_json=config.model_dump(mode="json"),
    )
    session_id: int = session.pk

    # --- Build strategy configs (one per instrument) ---
    strategy_configs = []
    for iid_str in instrument_id_strs:
        config_class = find_strategy_config_class(strategies[config.strategy])
        if config_class is None:
            msg = f"No config class found for strategy '{config.strategy}'"
            raise ValueError(msg)
        strategy_params: dict[str, object] = dict(config.strategy_params)
        strategy_params["instrument_id"] = iid_str
        strategy_params["bar_type"] = f"{iid_str}-{config.bar_type}"
        if config.extra_bar_types:
            strategy_params["extra_bar_types"] = tuple(
                f"{iid_str}-{bt}" for bt in config.extra_bar_types
            )
        strategy_params["trade_size"] = str(config.trade_size)
        strategy_configs.append(ImportableStrategyConfig(
            strategy_path=_strategy_import_path(config.strategy, strategies),
            config_path=_config_import_path(config_class),
            config=strategy_params,
        ))

    # --- Build risk actor config ---
    risk_config = RiskMonitorConfig(
        component_id="RiskMonitor-001",
        max_positions=settings.risk_max_positions,
        daily_loss_limit=settings.risk_daily_loss_limit,
        max_drawdown_pct=settings.risk_max_drawdown_pct,
        position_size_pct=settings.risk_position_size_pct,
        sizing_method=settings.risk_sizing_method,
        session_db_id=session_id,
        starting_equity=settings.default_balance,
        account_currency=settings.account_currency,
    )
    actor_config = ImportableActorConfig(
        actor_path=f"{RiskMonitorActor.__module__}:{RiskMonitorActor.__name__}",
        config_path=f"{RiskMonitorConfig.__module__}:{RiskMonitorConfig.__name__}",
        config=_msgspec_to_dict(risk_config),
    )

    # --- Build TradingNodeConfig ---
    node_config = build_trading_node_config(
        settings=settings,
        strategy_configs=strategy_configs,
        actor_configs=[actor_config],
        instrument_ids=instrument_id_strs,
    )

    # --- Create and run node ---
    node = TradingNode(config=node_config)

    def _graceful_stop(signum: int, frame: object) -> None:
        node.stop()

    signal.signal(signal.SIGINT, _graceful_stop)
    signal.signal(signal.SIGTERM, _graceful_stop)

    try:
        from pyfx.live.events import save_session_event

        save_session_event(
            session_id=session_id,
            event_type="info",
            message=f"Session started: {config.strategy} on {', '.join(config.instruments)}",
        )

        node.run()
    finally:
        # Update session as stopped
        session.refresh_from_db()
        session.status = PaperTradingSession.STATUS_STOPPED
        session.stopped_at = datetime.now(UTC)
        session.save()

        from pyfx.live.events import save_session_event

        save_session_event(
            session_id=session_id,
            event_type="info",
            message="Session stopped",
        )
        node.dispose()


def get_session_status(session_id: int | None = None) -> dict[str, Any]:
    """Get current status of a paper trading session.

    If *session_id* is ``None``, returns the most recent running session.
    """
    _setup_django()
    from pyfx.web.dashboard.models import PaperTrade, PaperTradingSession, SessionEvent

    if session_id is not None:
        session = PaperTradingSession.objects.get(pk=session_id)
    else:
        session_or_none = (
            PaperTradingSession.objects
            .order_by("-started_at")
            .first()
        )
        if session_or_none is None:
            return {"error": "No paper trading sessions found"}
        session = session_or_none

    recent_events = list(
        SessionEvent.objects.filter(session=session)
        .order_by("-timestamp")[:10]
        .values("timestamp", "event_type", "message")
    )
    open_trades = list(
        PaperTrade.objects.filter(session=session, closed_at__isnull=True)
        .values("instrument", "side", "quantity", "open_price", "opened_at")
    )
    closed_count = PaperTrade.objects.filter(
        session=session, closed_at__isnull=False,
    ).count()

    return {
        "session_id": session.pk,
        "status": session.status,
        "strategy": session.strategy,
        "instrument": session.instrument,
        "started_at": str(session.started_at),
        "stopped_at": str(session.stopped_at) if session.stopped_at else None,
        "total_pnl": session.total_pnl,
        "total_return_pct": session.total_return_pct,
        "num_trades": session.num_trades,
        "win_rate": session.win_rate,
        "max_drawdown_pct": session.max_drawdown_pct,
        "profit_factor": session.profit_factor,
        "open_trades": open_trades,
        "closed_trades": closed_count,
        "recent_events": recent_events,
    }


def get_session_history(
    last_n: int | None = None,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Get trade and event history for review (e.g. morning review)."""
    _setup_django()
    from pyfx.web.dashboard.models import PaperTrade, SessionEvent

    # Default: last 24 hours
    if since is None and last_n is None:
        from datetime import timedelta

        since = datetime.now(UTC) - timedelta(hours=24)

    events_qs = SessionEvent.objects.all().order_by("-timestamp")
    trades_qs = PaperTrade.objects.all().order_by("-opened_at")

    if since:
        events_qs = events_qs.filter(timestamp__gte=since)
        trades_qs = trades_qs.filter(opened_at__gte=since)

    if last_n:
        events_qs = events_qs[:last_n]
        trades_qs = trades_qs[:last_n]

    events = list(events_qs.values(
        "session_id", "timestamp", "event_type", "message",
    ))
    trades = list(trades_qs.values(
        "session_id", "instrument", "side", "quantity",
        "open_price", "close_price", "realized_pnl",
        "opened_at", "closed_at", "duration_seconds",
    ))

    return [{"events": events, "trades": trades}]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_django() -> None:  # pragma: no cover
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pyfx.web.pyfx_web.settings")
    import django

    django.setup()
    from django.core.management import call_command

    call_command("migrate", "--run-syncdb", verbosity=0)


def _strategy_import_path(
    name: str,
    strategies: dict[str, type],
) -> str:
    cls = strategies[name]
    return f"{cls.__module__}:{cls.__name__}"


def _config_import_path(config_class: type) -> str:
    return f"{config_class.__module__}:{config_class.__name__}"


def _msgspec_to_dict(obj: object) -> dict[str, Any]:
    """Convert a msgspec Struct to a plain dict for ImportableActorConfig."""
    import msgspec.structs

    result: dict[str, Any] = {}
    for field in msgspec.structs.fields(type(obj)):  # type: ignore[type-var]
        val = getattr(obj, field.name)
        if val != field.default:
            result[field.name] = val
    return result
