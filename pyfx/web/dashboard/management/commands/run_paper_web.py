"""Management command to run a paper trading session for a pre-created row."""

from __future__ import annotations

import traceback
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    help = "Run a paper trading session for a pre-created PaperTradingSession row"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--session-id", required=True, type=int)
        parser.add_argument("--log-level", default="ERROR")

    def handle(self, **options: object) -> None:
        from pyfx.core.config import settings as pyfx_settings
        from pyfx.core.types import LiveTradingConfig
        from pyfx.live.runner import start_live_trading
        from pyfx.web.dashboard.models import PaperTradingSession

        session_id: int = options["session_id"]  # type: ignore[assignment]
        try:
            session = PaperTradingSession.objects.get(pk=session_id)
        except PaperTradingSession.DoesNotExist:
            self.stderr.write(f"PaperTradingSession {session_id} not found")
            return

        cj = session.config_json or {}

        config = LiveTradingConfig(
            strategy=cj.get("strategy", session.strategy),
            instruments=cj.get("instruments", session.instrument.split(",")),
            bar_type=cj.get("bar_type", session.bar_type),
            extra_bar_types=cj.get("extra_bar_types", []),
            strategy_params=cj.get("strategy_params", {}),
            trade_size=Decimal(str(cj.get("trade_size", "100000"))),
            account_currency=cj.get("account_currency", session.account_currency),
        )

        # Apply risk overrides to a copy of settings
        risk_overrides = cj.get("risk_overrides", {})
        patched_settings = pyfx_settings.model_copy(update=risk_overrides)

        try:
            start_live_trading(config, patched_settings, session_id=session.pk)
        except Exception:
            session.refresh_from_db()
            session.status = PaperTradingSession.STATUS_ERROR
            session.save()

            from pyfx.live.events import save_session_event

            save_session_event(
                session_id=session.pk,
                event_type="info",
                message=f"Session error: {traceback.format_exc()[-500:]}",
            )
