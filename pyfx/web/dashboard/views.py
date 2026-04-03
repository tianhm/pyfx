from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.db.models import Avg, Count, Max, Min  # type: ignore[import-untyped]
from django.http import HttpRequest, HttpResponse, JsonResponse  # type: ignore[import-untyped]
from django.shortcuts import get_object_or_404, redirect, render  # type: ignore[import-untyped]

from .models import BacktestRun, Dataset, EquitySnapshot, Trade


def overview(request: HttpRequest) -> HttpResponse:
    runs = BacktestRun.objects.filter(status=BacktestRun.STATUS_COMPLETED)
    total_runs: int = runs.count()

    running_runs = BacktestRun.objects.filter(
        status=BacktestRun.STATUS_RUNNING
    ).count()

    context: dict[str, object] = {
        "active_nav": "overview",
        "total_runs": total_runs,
        "running_runs": running_runs,
    }

    if total_runs > 0:
        agg = runs.aggregate(
            best_return=Max("total_return_pct"),
            worst_drawdown=Min("max_drawdown_pct"),
            avg_win_rate=Avg("win_rate"),
            best_profit_factor=Max("profit_factor"),
        )
        strategies_tested = (
            runs.values("strategy").annotate(c=Count("id")).count()
        )
        best_run = runs.order_by("-total_return_pct").first()
        worst_dd_run = runs.order_by("max_drawdown_pct").first()

        best_pf_run = runs.filter(profit_factor__isnull=False).order_by(
            "-profit_factor"
        ).first()
        context.update({
            "strategies_tested": strategies_tested,
            "best_return": agg["best_return"] or 0,
            "best_strategy": best_run.strategy if best_run else "",
            "worst_drawdown": agg["worst_drawdown"] or 0,
            "worst_dd_strategy": worst_dd_run.strategy if worst_dd_run else "",
            "avg_win_rate": (agg["avg_win_rate"] or 0) * 100,
            "best_profit_factor": agg["best_profit_factor"],
            "best_pf_strategy": best_pf_run.strategy if best_pf_run else "",
            "recent_runs": BacktestRun.objects.exclude(
                status=BacktestRun.STATUS_RUNNING
            )[:10],
        })

    return render(request, "dashboard/overview.html", context)


def backtest_list(request: HttpRequest) -> HttpResponse:
    runs = BacktestRun.objects.all()
    running_count = runs.filter(status=BacktestRun.STATUS_RUNNING).count()
    return render(request, "dashboard/backtest_list.html", {
        "active_nav": "backtests",
        "runs": runs,
        "running_count": running_count,
    })


def backtest_detail(request: HttpRequest, pk: int) -> HttpResponse:
    run = get_object_or_404(BacktestRun, pk=pk)
    trades = run.trade_set.all()

    cumulative_pnl: list[dict[str, object]] = []
    running = 0.0
    for t in trades:
        running += t.realized_pnl
        cumulative_pnl.append({
            "time": int(t.closed_at.timestamp()),
            "value": round(running, 2),
        })

    # Compute quant metrics
    expectancy = 0.0
    win_loss_ratio: float | None = None
    num_wins = 0
    num_losses = 0
    if run.status == BacktestRun.STATUS_COMPLETED and run.num_trades > 0:
        wr = run.win_rate or 0.0
        avg_w = run.avg_win or 0.0
        avg_l = run.avg_loss or 0.0
        expectancy = round((wr * avg_w) - ((1 - wr) * abs(avg_l)), 2)
        if avg_l:
            win_loss_ratio = round(avg_w / abs(avg_l), 2)
        num_wins = round(run.num_trades * wr)
        num_losses = run.num_trades - num_wins

    return render(request, "dashboard/backtest_detail.html", {
        "active_nav": "backtests",
        "run": run,
        "trades": trades,
        "cumulative_pnl_json": json.dumps(cumulative_pnl),
        "expectancy": expectancy,
        "win_loss_ratio": win_loss_ratio,
        "num_wins": num_wins,
        "num_losses": num_losses,
    })


def backtest_delete(request: HttpRequest, pk: int) -> HttpResponse:
    run = get_object_or_404(BacktestRun, pk=pk)
    if request.method == "POST":
        run.delete()
        return redirect("dashboard:backtest_list")
    return redirect("dashboard:backtest_detail", pk=pk)


def backtest_rerun(request: HttpRequest, pk: int) -> HttpResponse:
    """Clone an existing backtest run and re-execute it."""
    original = get_object_or_404(BacktestRun, pk=pk)
    if request.method != "POST":
        return redirect("dashboard:backtest_detail", pk=pk)

    run = BacktestRun.objects.create(
        strategy=original.strategy,
        instrument=original.instrument,
        start=original.start,
        end=original.end,
        bar_type=original.bar_type,
        extra_bar_types=original.extra_bar_types,
        trade_size=original.trade_size,
        balance=original.balance,
        leverage=original.leverage,
        strategy_params=original.strategy_params,
        status=BacktestRun.STATUS_RUNNING,
        data_file=original.data_file,
    )

    cmd = [
        sys.executable, "-m", "django", "run_backtest_web",
        "--run-id", str(run.pk),
    ]
    subprocess.Popen(  # noqa: S603
        cmd,
        env={
            **__import__("os").environ,
            "DJANGO_SETTINGS_MODULE": "pyfx.web.pyfx_web.settings",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    return redirect("dashboard:backtest_list")


def backtest_new(request: HttpRequest) -> HttpResponse:
    return render(request, "dashboard/backtest_new.html", {
        "active_nav": "backtests",
    })


def backtest_start(request: HttpRequest) -> HttpResponse:
    """Start a new backtest via POST. Creates a running BacktestRun and spawns
    the management command in a subprocess."""
    if request.method != "POST":
        return redirect("dashboard:backtest_list")

    strategy = request.POST.get("strategy", "").strip()
    instrument = request.POST.get("instrument", "EUR/USD").strip()
    start_str = request.POST.get("start", "").strip()
    end_str = request.POST.get("end", "").strip()
    data_file = request.POST.get("data_file", "").strip()
    balance = float(request.POST.get("balance", "100000"))
    leverage = float(request.POST.get("leverage", "50"))
    trade_size = request.POST.get("trade_size", "100000")
    bar_type = request.POST.get("bar_type", "1-MINUTE-LAST-EXTERNAL").strip()

    # Collect strategy params
    strategy_params: dict[str, object] = {}
    for key, value in request.POST.items():
        if key.startswith("param_"):
            param_name = key[6:]
            if value.lower() in ("true", "false"):
                strategy_params[param_name] = value.lower() == "true"
            else:
                try:
                    strategy_params[param_name] = int(value)
                except ValueError:
                    try:
                        strategy_params[param_name] = float(value)
                    except ValueError:
                        strategy_params[param_name] = value

    start_dt = datetime.fromisoformat(start_str)
    end_dt = datetime.fromisoformat(end_str)

    # Expand ~ in data_file path
    expanded_path = str(Path(data_file).expanduser())

    run = BacktestRun.objects.create(
        strategy=strategy,
        instrument=instrument,
        start=start_dt,
        end=end_dt,
        bar_type=bar_type,
        trade_size=float(Decimal(trade_size)),
        balance=balance,
        leverage=leverage,
        strategy_params=strategy_params,
        status=BacktestRun.STATUS_RUNNING,
        data_file=expanded_path,
    )

    # Spawn subprocess to run the backtest
    cmd = [
        sys.executable, "-m", "django", "run_backtest_web",
        "--run-id", str(run.pk),
    ]
    subprocess.Popen(  # noqa: S603
        cmd,
        env={
            **__import__("os").environ,
            "DJANGO_SETTINGS_MODULE": "pyfx.web.pyfx_web.settings",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    return redirect("dashboard:backtest_list")


def api_strategies(request: HttpRequest) -> JsonResponse:
    """Return available strategies and their configurable parameters."""
    import msgspec.structs

    from pyfx.strategies.base import PyfxStrategyConfig
    from pyfx.strategies.loader import discover_strategies

    strategies = discover_strategies()
    base_fields = set(PyfxStrategyConfig.__struct_fields__)
    result = []

    for name, cls in sorted(strategies.items()):
        params = []
        try:
            config_cls = _find_strategy_config(cls)
            if config_cls is not None:
                for field_info in msgspec.structs.fields(config_cls):
                    if field_info.name in base_fields:
                        continue
                    field_type = "string"
                    ft = field_info.type
                    # Check bool before int (bool is subclass of int)
                    if ft is bool:
                        field_type = "bool"
                    elif ft is int:
                        field_type = "int"
                    elif ft is float:
                        field_type = "float"
                    default = field_info.default
                    if default is msgspec.NODEFAULT:
                        default = None
                    else:
                        # Convert Decimal and other types to JSON-safe values
                        default = _to_json_safe(default)
                    params.append({
                        "name": field_info.name,
                        "type": field_type,
                        "default": default,
                        "category": _categorize_param(field_info.name),
                    })
        except Exception:  # noqa: BLE001
            pass

        result.append({"name": name, "params": params})

    return JsonResponse(result, safe=False)


def _to_json_safe(value: object) -> object:
    """Convert a value to a JSON-serialisable type."""
    if isinstance(value, Decimal):
        return float(value)
    return value


def _categorize_param(name: str) -> str:
    """Categorise a strategy parameter by name pattern."""
    if name in ("entry_mode", "rsi_level_threshold") or name.endswith("_period"):
        return "indicators"
    if name in ("exit_mode",) or name.startswith((
        "take_profit", "stop_loss", "trailing", "atr_",
        "spread", "macd_reversal",
    )):
        return "exits"
    if name.startswith(("session_", "max_signal_")) or "confirm" in name:
        return "timing"
    return "advanced"


def _find_strategy_config(strategy_cls: type) -> type | None:
    """Find the config class for a strategy via __init__ signature or module scan."""
    import importlib
    import inspect

    sig = inspect.signature(strategy_cls.__init__)  # type: ignore[misc]
    for param in sig.parameters.values():
        if param.name == "config" and param.annotation != inspect.Parameter.empty:
            ann = param.annotation
            if isinstance(ann, str):
                module = importlib.import_module(strategy_cls.__module__)
                result: type | None = getattr(module, ann, None)
                return result
            return ann  # type: ignore[no-any-return]

    module = importlib.import_module(strategy_cls.__module__)
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and attr_name.endswith("Config")
            and attr_name != "StrategyConfig"
        ):
            return attr

    return None


def api_equity_curve(request: HttpRequest, pk: int) -> JsonResponse:
    run = get_object_or_404(BacktestRun, pk=pk)
    snapshots = EquitySnapshot.objects.filter(run=run)
    data = [
        {"time": s.timestamp.isoformat(), "value": round(s.balance, 2)}
        for s in snapshots
    ]
    return JsonResponse(data, safe=False)


def api_trades(request: HttpRequest, pk: int) -> JsonResponse:
    run = get_object_or_404(BacktestRun, pk=pk)
    trades = Trade.objects.filter(run=run)
    data = [
        {
            "instrument": t.instrument,
            "side": t.side,
            "quantity": t.quantity,
            "open_price": t.open_price,
            "close_price": t.close_price,
            "pnl": round(t.realized_pnl, 2),
            "return_pct": round(t.realized_return_pct, 2),
            "opened_at": t.opened_at.isoformat(),
            "closed_at": t.closed_at.isoformat(),
            "duration_s": t.duration_seconds,
        }
        for t in trades
    ]
    return JsonResponse(data, safe=False)


def api_backtest_status(request: HttpRequest, pk: int) -> JsonResponse:
    run = get_object_or_404(BacktestRun, pk=pk)
    return JsonResponse({
        "status": run.status,
        "error_message": run.error_message,
        "progress_pct": run.progress_pct,
        "progress_message": run.progress_message,
        "total_bars": run.total_bars,
    })


def api_running_count(request: HttpRequest) -> JsonResponse:
    count = BacktestRun.objects.filter(status=BacktestRun.STATUS_RUNNING).count()
    return JsonResponse({"running": count})


def api_running_backtests(request: HttpRequest) -> JsonResponse:
    running = BacktestRun.objects.filter(status=BacktestRun.STATUS_RUNNING)
    data = [
        {
            "id": r.pk,
            "strategy": r.strategy,
            "instrument": r.instrument,
            "start": r.start.strftime("%Y-%m-%d"),
            "end": r.end.strftime("%Y-%m-%d"),
            "created_at": r.created_at.isoformat(),
            "progress_pct": r.progress_pct,
            "progress_message": r.progress_message,
            "total_bars": r.total_bars,
        }
        for r in running
    ]
    return JsonResponse(data, safe=False)


# ── Dataset views ──


def dataset_list(request: HttpRequest) -> HttpResponse:
    datasets = Dataset.objects.all()
    downloading_count = datasets.filter(
        status__in=[Dataset.STATUS_DOWNLOADING, Dataset.STATUS_INGESTING]
    ).count()
    return render(request, "dashboard/dataset_list.html", {
        "active_nav": "data",
        "datasets": datasets,
        "downloading_count": downloading_count,
    })


def dataset_new(request: HttpRequest) -> HttpResponse:
    from pyfx.data.dukascopy import DUKASCOPY_INSTRUMENTS

    instruments = sorted(DUKASCOPY_INSTRUMENTS.keys())
    return render(request, "dashboard/dataset_new.html", {
        "active_nav": "data",
        "instruments": instruments,
    })


def dataset_start(request: HttpRequest) -> HttpResponse:
    """Start a data download via POST."""
    if request.method != "POST":
        return redirect("dashboard:dataset_list")

    from pyfx.core.config import settings
    from pyfx.data.dukascopy import canonical_parquet_name

    instrument = request.POST.get("instrument", "EUR/USD").strip()
    start_str = request.POST.get("start", "").strip()
    end_str = request.POST.get("end", "").strip()
    timeframe = request.POST.get("timeframe", "M1").strip()

    start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
    end_date = datetime.strptime(end_str, "%Y-%m-%d").date()

    filename = canonical_parquet_name(instrument, start_date, end_date, timeframe)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    file_path = str((settings.data_dir / filename).resolve())

    dataset = Dataset.objects.create(
        instrument=instrument,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        file_path=file_path,
        source=Dataset.SOURCE_DUKASCOPY,
        status=Dataset.STATUS_DOWNLOADING,
    )

    cmd = [
        sys.executable, "-m", "django", "run_download_web",
        "--dataset-id", str(dataset.pk),
    ]
    subprocess.Popen(  # noqa: S603
        cmd,
        env={
            **__import__("os").environ,
            "DJANGO_SETTINGS_MODULE": "pyfx.web.pyfx_web.settings",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    return redirect("dashboard:dataset_list")


def dataset_delete(request: HttpRequest, pk: int) -> HttpResponse:
    dataset = get_object_or_404(Dataset, pk=pk)
    if request.method == "POST":
        Path(dataset.file_path).unlink(missing_ok=True)
        dataset.delete()
        return JsonResponse({"ok": True})
    return JsonResponse({"error": "POST required"}, status=405)


def dataset_redownload(request: HttpRequest, pk: int) -> HttpResponse:
    dataset = get_object_or_404(Dataset, pk=pk)
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    if dataset.status in (Dataset.STATUS_DOWNLOADING, Dataset.STATUS_INGESTING):
        return JsonResponse({"error": "Download already in progress"}, status=409)

    # Remove old file
    Path(dataset.file_path).unlink(missing_ok=True)

    # Reset state
    dataset.status = Dataset.STATUS_DOWNLOADING
    dataset.progress_pct = 0
    dataset.progress_message = ""
    dataset.error_message = ""
    dataset.save()

    cmd = [
        sys.executable, "-m", "django", "run_download_web",
        "--dataset-id", str(dataset.pk),
    ]
    subprocess.Popen(  # noqa: S603
        cmd,
        env={
            **__import__("os").environ,
            "DJANGO_SETTINGS_MODULE": "pyfx.web.pyfx_web.settings",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    return JsonResponse({"ok": True})


def api_datasets(request: HttpRequest) -> JsonResponse:
    """Return ready datasets for the backtest form dropdown."""
    datasets = Dataset.objects.filter(status=Dataset.STATUS_READY)
    data = [
        {
            "id": ds.pk,
            "instrument": ds.instrument,
            "timeframe": ds.timeframe,
            "start_date": ds.start_date.isoformat(),
            "end_date": ds.end_date.isoformat(),
            "file_path": ds.file_path,
            "row_count": ds.row_count,
            "display_size": ds.display_size,
        }
        for ds in datasets
    ]
    return JsonResponse(data, safe=False)


def api_dataset_status(request: HttpRequest, pk: int) -> JsonResponse:
    dataset = get_object_or_404(Dataset, pk=pk)
    return JsonResponse({
        "status": dataset.status,
        "progress_pct": dataset.progress_pct,
        "progress_message": dataset.progress_message,
        "error_message": dataset.error_message,
    })


def api_running_downloads(request: HttpRequest) -> JsonResponse:
    running = Dataset.objects.filter(
        status__in=[Dataset.STATUS_DOWNLOADING, Dataset.STATUS_INGESTING]
    )
    data = [
        {
            "id": ds.pk,
            "instrument": ds.instrument,
            "timeframe": ds.timeframe,
            "start_date": ds.start_date.isoformat(),
            "end_date": ds.end_date.isoformat(),
            "progress_pct": ds.progress_pct,
            "progress_message": ds.progress_message,
            "status": ds.status,
        }
        for ds in running
    ]
    return JsonResponse(data, safe=False)
