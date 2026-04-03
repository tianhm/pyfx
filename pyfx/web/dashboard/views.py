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

from .models import BacktestRun, EquitySnapshot, Trade


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
        )
        strategies_tested = (
            runs.values("strategy").annotate(c=Count("id")).count()
        )
        best_run = runs.order_by("-total_return_pct").first()
        worst_dd_run = runs.order_by("max_drawdown_pct").first()

        context.update({
            "strategies_tested": strategies_tested,
            "best_return": agg["best_return"] or 0,
            "best_strategy": best_run.strategy if best_run else "",
            "worst_drawdown": agg["worst_drawdown"] or 0,
            "worst_dd_strategy": worst_dd_run.strategy if worst_dd_run else "",
            "avg_win_rate": (agg["avg_win_rate"] or 0) * 100,
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

    cumulative_pnl: list[float] = []
    running = 0.0
    for t in trades:
        running += t.realized_pnl
        cumulative_pnl.append(round(running, 2))

    return render(request, "dashboard/backtest_detail.html", {
        "active_nav": "backtests",
        "run": run,
        "trades": trades,
        "cumulative_pnl_json": json.dumps(cumulative_pnl),
        "strategy_params_json": json.dumps(run.strategy_params, indent=2),
    })


def backtest_delete(request: HttpRequest, pk: int) -> HttpResponse:
    run = get_object_or_404(BacktestRun, pk=pk)
    if request.method == "POST":
        run.delete()
        return redirect("dashboard:backtest_list")
    return redirect("dashboard:backtest_detail", pk=pk)


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
                    if ft is int:
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
    })


def api_running_count(request: HttpRequest) -> JsonResponse:
    count = BacktestRun.objects.filter(status=BacktestRun.STATUS_RUNNING).count()
    return JsonResponse({"running": count})
