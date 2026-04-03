"""Django management command to run a backtest and save results."""

from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run a backtest and save results to the database"

    def add_arguments(self, parser):
        parser.add_argument("--strategy", "-s", required=True)
        parser.add_argument("--instrument", "-i", default="EUR/USD")
        parser.add_argument("--start", required=True)
        parser.add_argument("--end", required=True)
        parser.add_argument("--data-file", required=True, type=Path)
        parser.add_argument("--bar-type", default="1-MINUTE-LAST-EXTERNAL")
        parser.add_argument("--trade-size", default="100000")
        parser.add_argument("--balance", type=float, default=100_000.0)
        parser.add_argument("--leverage", type=float, default=50.0)
        parser.add_argument("--log-level", default="ERROR")
        parser.add_argument("--param", "-p", action="append", default=[])

    def handle(self, **options):
        from datetime import datetime

        import pandas as pd

        from pyfx.backtest.runner import run_backtest
        from pyfx.core.types import BacktestConfig
        from pyfx.web.dashboard.models import BacktestRun, EquitySnapshot, Trade

        # Parse params
        strategy_params = {}
        for p in options["param"]:
            key, _, value = p.partition("=")
            try:
                strategy_params[key] = int(value)
            except ValueError:
                try:
                    strategy_params[key] = float(value)
                except ValueError:
                    strategy_params[key] = value

        start = datetime.fromisoformat(options["start"])
        end = datetime.fromisoformat(options["end"])

        config = BacktestConfig(
            strategy=options["strategy"],
            instrument=options["instrument"],
            start=start,
            end=end,
            bar_type=options["bar_type"],
            trade_size=Decimal(options["trade_size"]),
            balance=options["balance"],
            leverage=options["leverage"],
            strategy_params=strategy_params,
        )

        # Load data
        data_file = options["data_file"]
        if data_file.suffix == ".parquet":
            bars_df = pd.read_parquet(data_file)
        else:
            bars_df = pd.read_csv(data_file, index_col=0, parse_dates=True)

        if bars_df.index.tz is None:
            bars_df.index = bars_df.index.tz_localize("UTC")
        bars_df = bars_df.loc[start:end]

        if bars_df.empty:
            self.stderr.write("No data in the specified date range")
            return

        self.stdout.write(
            f"Running: {config.strategy} on {config.instrument} ({len(bars_df)} bars)"
        )

        result = run_backtest(config, bars_df, log_level=options["log_level"])

        # Save to DB
        run = BacktestRun.objects.create(
            strategy=result.config.strategy,
            instrument=result.config.instrument,
            start=result.config.start,
            end=result.config.end,
            bar_type=result.config.bar_type,
            trade_size=float(result.config.trade_size),
            balance=result.config.balance,
            leverage=result.config.leverage,
            strategy_params=result.config.strategy_params,
            total_pnl=result.total_pnl,
            total_return_pct=result.total_return_pct,
            num_trades=result.num_trades,
            win_rate=result.win_rate,
            max_drawdown_pct=result.max_drawdown_pct,
            avg_trade_pnl=result.avg_trade_pnl,
            avg_win=result.avg_win,
            avg_loss=result.avg_loss,
            profit_factor=result.profit_factor,
            duration_seconds=result.duration_seconds,
        )

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

        self.stdout.write(self.style.SUCCESS(
            f"Saved: {result.num_trades} trades, "
            f"P&L ${result.total_pnl:,.2f} ({result.total_return_pct:+.2f}%)"
        ))
