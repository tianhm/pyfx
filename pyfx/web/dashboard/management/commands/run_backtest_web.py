"""Management command to run a backtest for a pre-created BacktestRun row."""

from __future__ import annotations

import traceback
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    help = "Run a backtest for a pre-created BacktestRun row (used by the web UI)"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--run-id", required=True, type=int)
        parser.add_argument("--log-level", default="ERROR")

    def handle(self, **options: object) -> None:
        from pyfx.backtest.runner import run_backtest
        from pyfx.core.types import BacktestConfig
        from pyfx.data.loader import load_backtest_data
        from pyfx.web.dashboard.models import BacktestRun
        from pyfx.web.dashboard.services import save_backtest_result

        run_id: int = options["run_id"]  # type: ignore[assignment]
        try:
            run = BacktestRun.objects.get(pk=run_id)
        except BacktestRun.DoesNotExist:
            self.stderr.write(f"BacktestRun {run_id} not found")
            return

        def _update_progress(pct: int, msg: str, **extra: object) -> None:
            run.progress_pct = pct
            run.progress_message = msg
            fields = ["progress_pct", "progress_message"]
            for k, v in extra.items():
                setattr(run, k, v)
                fields.append(k)
            run.save(update_fields=fields)

        try:
            _update_progress(5, "Initializing...")

            config = BacktestConfig(
                strategy=run.strategy,
                instrument=run.instrument,
                start=run.start,
                end=run.end,
                bar_type=run.bar_type,
                extra_bar_types=run.extra_bar_types,
                trade_size=Decimal(str(run.trade_size)),
                balance=run.balance,
                leverage=run.leverage,
                strategy_params=run.strategy_params,
            )

            _update_progress(10, "Loading data...")

            bars_df = load_backtest_data(Path(run.data_file), config.start, config.end)

            _update_progress(20, "Running engine...", total_bars=len(bars_df))

            result = run_backtest(config, bars_df, log_level=str(options["log_level"]))

            _update_progress(90, "Saving results...")

            save_backtest_result(result, existing_run=run)

        except Exception:
            run.status = BacktestRun.STATUS_FAILED
            run.error_message = traceback.format_exc()
            run.progress_message = "Failed"
            run.save()
