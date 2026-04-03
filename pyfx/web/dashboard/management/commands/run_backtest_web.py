"""Management command to run a backtest for a pre-created BacktestRun row."""

from __future__ import annotations

import traceback
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandParser  # type: ignore[import-untyped]


class Command(BaseCommand):  # type: ignore[misc]
    help = "Run a backtest for a pre-created BacktestRun row (used by the web UI)"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--run-id", required=True, type=int)
        parser.add_argument("--log-level", default="ERROR")

    def handle(self, **options: object) -> None:  # noqa: C901
        import pandas as pd  # type: ignore[import-untyped]

        from pyfx.backtest.runner import run_backtest
        from pyfx.core.types import BacktestConfig
        from pyfx.web.dashboard.models import BacktestRun, EquitySnapshot, Trade

        run_id: int = options["run_id"]  # type: ignore[assignment]
        try:
            run = BacktestRun.objects.get(pk=run_id)
        except BacktestRun.DoesNotExist:
            self.stderr.write(f"BacktestRun {run_id} not found")
            return

        try:
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

            data_file = Path(run.data_file)
            if not data_file.exists():
                raise FileNotFoundError(f"Data file not found: {data_file}")

            if data_file.suffix == ".parquet":
                bars_df = pd.read_parquet(data_file)
            else:
                bars_df = pd.read_csv(data_file, index_col=0, parse_dates=True)

            if bars_df.index.tz is None:
                bars_df.index = bars_df.index.tz_localize("UTC")
            bars_df = bars_df.loc[config.start : config.end]  # type: ignore[misc]

            if bars_df.empty:
                raise ValueError("No data in the specified date range")

            result = run_backtest(config, bars_df, log_level=str(options["log_level"]))

            # Update the run with results
            run.total_pnl = result.total_pnl
            run.total_return_pct = result.total_return_pct
            run.num_trades = result.num_trades
            run.win_rate = result.win_rate
            run.max_drawdown_pct = result.max_drawdown_pct
            run.avg_trade_pnl = result.avg_trade_pnl
            run.avg_win = result.avg_win
            run.avg_loss = result.avg_loss
            run.profit_factor = result.profit_factor
            run.duration_seconds = result.duration_seconds
            run.status = BacktestRun.STATUS_COMPLETED
            run.save()

            Trade.objects.bulk_create([
                Trade(
                    run=run,
                    instrument=t.instrument,
                    side=t.side,
                    quantity=t.quantity,
                    open_price=t.open_price,
                    close_price=t.close_price,
                    realized_pnl=t.realized_pnl,
                    realized_return_pct=t.realized_return_pct,
                    opened_at=t.opened_at,
                    closed_at=t.closed_at,
                    duration_seconds=t.duration_seconds,
                )
                for t in result.trades
            ])

            EquitySnapshot.objects.bulk_create([
                EquitySnapshot(run=run, timestamp=ep.timestamp, balance=ep.balance)
                for ep in result.equity_curve
            ])

        except Exception:
            run.status = BacktestRun.STATUS_FAILED
            run.error_message = traceback.format_exc()
            run.save()
