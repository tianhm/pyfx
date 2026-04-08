"""IB Gateway connection test — validate config, connect, resolve instruments."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyfx.core.config import PyfxSettings
    from pyfx.core.types import ConnectionTestResult


def test_ib_connection(  # pragma: no cover
    settings: PyfxSettings,
    instrument: str = "XAU/USD",
    timeout_seconds: int = 30,
) -> ConnectionTestResult:
    """Validate IB configuration and attempt a live connection.

    Steps:
    1. Validate .env config
    2. Build minimal TradingNodeConfig (no strategies/actors)
    3. Connect to IB via DockerizedIBGateway
    4. Resolve the requested instrument
    5. Log resolved specs and return result
    """
    import time

    from pyfx.core.types import ConnectionTestResult

    diagnostics: list[str] = []
    start = time.monotonic()

    # Step 1: Validate config
    diagnostics.append("Validating IB configuration...")
    warnings = settings.validate_ib_config()
    if settings.ib_username is None or settings.ib_password is None:
        return ConnectionTestResult(
            success=False,
            elapsed_seconds=time.monotonic() - start,
            diagnostics=[*diagnostics, "FAIL: IB credentials not set"],
            warnings=warnings,
            error="Set PYFX_IB_USERNAME and PYFX_IB_PASSWORD in .env",
        )
    diagnostics.append("  Config OK")

    # Step 2: Build minimal node config
    diagnostics.append("Building TradingNodeConfig...")
    from pyfx.adapters.ib import build_trading_node_config
    from pyfx.adapters.instruments import get_ib_instrument_id_str

    instrument_id_str = get_ib_instrument_id_str(instrument)
    node_config = build_trading_node_config(
        settings=settings,
        strategy_configs=[],
        instrument_ids=[instrument_id_str],
    )
    diagnostics.append(f"  Instrument: {instrument} -> {instrument_id_str}")

    # Step 3: Connect
    diagnostics.append(f"Connecting to IB Gateway ({settings.ib_host}:{settings.ib_port})...")
    from nautilus_trader.live.node import TradingNode

    node = TradingNode(config=node_config)
    try:
        node.build()
        diagnostics.append("  Node built")

        node.start()  # type: ignore[attr-defined]
        diagnostics.append("  Node started, waiting for instrument resolution...")

        # Step 4: Wait for instrument resolution
        deadline = time.monotonic() + timeout_seconds
        resolved = None
        from nautilus_trader.model.identifiers import InstrumentId

        iid = InstrumentId.from_str(instrument_id_str)
        while time.monotonic() < deadline:
            resolved = node.cache.instrument(iid)
            if resolved is not None:
                break
            time.sleep(1)

        if resolved is None:
            diagnostics.append(f"  TIMEOUT: instrument not resolved in {timeout_seconds}s")
            return ConnectionTestResult(
                success=False,
                elapsed_seconds=time.monotonic() - start,
                diagnostics=diagnostics,
                warnings=warnings,
                error=f"Instrument {instrument_id_str} not resolved within {timeout_seconds}s",
            )

        # Step 5: Log specs
        specs = {
            "id": str(resolved.id),
            "tick_size": str(getattr(resolved, "price_increment", "?")),
            "lot_size": str(getattr(resolved, "lot_size", "?")),
            "multiplier": str(getattr(resolved, "multiplier", "?")),
            "min_quantity": str(getattr(resolved, "min_quantity", "?")),
            "currency": str(getattr(resolved, "quote_currency", "?")),
        }
        diagnostics.append(f"  Resolved: {specs}")
        diagnostics.append("SUCCESS")

        return ConnectionTestResult(
            success=True,
            elapsed_seconds=time.monotonic() - start,
            diagnostics=diagnostics,
            warnings=warnings,
            instrument_specs=specs,
        )
    except Exception as exc:
        diagnostics.append(f"  ERROR: {exc}")
        return ConnectionTestResult(
            success=False,
            elapsed_seconds=time.monotonic() - start,
            diagnostics=diagnostics,
            warnings=warnings,
            error=str(exc),
        )
    finally:
        try:
            node.stop()
            node.dispose()
        except Exception:
            pass


def validate_ib_config(settings: PyfxSettings) -> ConnectionTestResult:
    """Quick config-only validation without connecting to IB."""
    import time

    from pyfx.core.types import ConnectionTestResult

    diagnostics: list[str] = []
    start = time.monotonic()
    warnings = settings.validate_ib_config()

    if settings.ib_username is None:
        diagnostics.append("PYFX_IB_USERNAME: not set")
    else:
        diagnostics.append(f"PYFX_IB_USERNAME: {settings.ib_username[:2]}***")

    if settings.ib_password is None:
        diagnostics.append("PYFX_IB_PASSWORD: not set")
    else:
        diagnostics.append("PYFX_IB_PASSWORD: ***")

    diagnostics.append(f"PYFX_IB_ACCOUNT_ID: {settings.ib_account_id or 'not set'}")
    diagnostics.append(f"PYFX_IB_HOST: {settings.ib_host}")
    diagnostics.append(f"PYFX_IB_PORT: {settings.ib_port}")
    diagnostics.append(f"PYFX_IB_TRADING_MODE: {settings.ib_trading_mode}")
    diagnostics.append(f"PYFX_ACCOUNT_CURRENCY: {settings.account_currency}")

    has_creds = settings.ib_username is not None and settings.ib_password is not None
    success = has_creds and settings.ib_account_id is not None

    if not has_creds:
        diagnostics.append("FAIL: Credentials missing")
    elif settings.ib_account_id is None:
        diagnostics.append("FAIL: Account ID missing")
    else:
        diagnostics.append("Config validation passed")

    return ConnectionTestResult(
        success=success,
        elapsed_seconds=time.monotonic() - start,
        diagnostics=diagnostics,
        warnings=warnings,
        error="Missing required IB configuration" if not success else None,
    )
