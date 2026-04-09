from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from django.db.models import Avg, Count, Max, Min
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .models import (
    BacktestRun,
    Dataset,
    EquitySnapshot,
    PaperTrade,
    PaperTradingSession,
    RiskSnapshot,
    SessionEvent,
    Trade,
)

_DJANGO_SETTINGS_MODULE = "pyfx.web.pyfx_web.settings"


def _spawn_management_command(command: str, *args: str) -> None:
    """Spawn a Django management command as a detached subprocess."""
    cmd = [sys.executable, "-m", "django", command, *args]
    subprocess.Popen(  # noqa: S603
        cmd,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": _DJANGO_SETTINGS_MODULE},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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

    # Compute quant metrics (lightweight — stays in template context)
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

    # Available timeframes for chart selector
    available_timeframes = ["1-MINUTE-LAST-EXTERNAL"]
    if run.extra_bar_types:
        for bt in run.extra_bar_types:
            if bt not in available_timeframes:
                available_timeframes.append(bt)
    for tf in [
        "5-MINUTE-LAST-EXTERNAL",
        "15-MINUTE-LAST-EXTERNAL",
        "60-MINUTE-LAST-EXTERNAL",
        "240-MINUTE-LAST-EXTERNAL",
        "1-DAY-LAST-EXTERNAL",
    ]:
        if tf not in available_timeframes:
            available_timeframes.append(tf)

    # Get strategy chart indicator defaults
    chart_indicators: list[dict[str, object]] = []
    try:
        from pyfx.strategies.loader import get_strategy as _get_strat

        strat_cls = _get_strat(run.strategy, None)
        if hasattr(strat_cls, "chart_indicators"):
            chart_indicators = strat_cls.chart_indicators()
    except (KeyError, ImportError, AttributeError):
        pass

    return render(request, "dashboard/backtest_detail.html", {
        "active_nav": "backtests",
        "run": run,
        "trades": trades,
        "available_timeframes_json": json.dumps(available_timeframes),
        "chart_indicators_json": json.dumps(chart_indicators),
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

    _spawn_management_command("run_backtest_web", "--run-id", str(run.pk))

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
    from pyfx.core.types import parse_strategy_params

    raw_params = {
        key[6:]: str(request.POST[key])
        for key in request.POST
        if key.startswith("param_")
    }
    strategy_params: dict[str, bool | int | float | str] = parse_strategy_params(raw_params)

    start_dt = datetime.fromisoformat(start_str)
    end_dt = datetime.fromisoformat(end_str)

    # Expand ~ in data_file path and reject path traversal attempts
    if ".." in Path(data_file).parts:
        return HttpResponse("Invalid data file path", status=400)
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
    _spawn_management_command("run_backtest_web", "--run-id", str(run.pk))

    return redirect("dashboard:backtest_list")


def api_strategies(request: HttpRequest) -> JsonResponse:
    """Return available strategies and their configurable parameters."""
    import msgspec.structs

    from pyfx.strategies.base import PyfxStrategyConfig
    from pyfx.strategies.loader import discover_strategies, find_strategy_config_class

    strategies = discover_strategies()
    base_fields = set(PyfxStrategyConfig.__struct_fields__)
    result = []

    for name, cls in sorted(strategies.items()):
        params = []
        try:
            config_cls = find_strategy_config_class(cls)
            if config_cls is not None:  # pragma: no branch
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
                    if default is msgspec.NODEFAULT:  # pragma: no cover
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
        except (KeyError, ImportError, AttributeError, TypeError):
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


_VALID_TIMEFRAMES = {
    "1-MINUTE-LAST-EXTERNAL",
    "5-MINUTE-LAST-EXTERNAL",
    "15-MINUTE-LAST-EXTERNAL",
    "60-MINUTE-LAST-EXTERNAL",
    "240-MINUTE-LAST-EXTERNAL",
    "1-DAY-LAST-EXTERNAL",
}
_VALID_INDICATORS = {"sma", "ema", "rsi", "macd", "atr"}
_MAX_RESPONSE_BARS = 10_000
_MAX_INDICATOR_PERIOD = 500


def _serialize_bars(df: object) -> list[dict[str, object]]:
    """Vectorized OHLCV DataFrame to list-of-dicts serialization."""
    import pandas as pd

    assert isinstance(df, pd.DataFrame)
    result_df = pd.DataFrame({
        "time": df.index.astype("int64") // 10**9,
        "open": df["open"].round(6),
        "high": df["high"].round(6),
        "low": df["low"].round(6),
        "close": df["close"].round(6),
    })
    if "volume" in df.columns:
        result_df["volume"] = df["volume"].round(2)
    return list(result_df.to_dict(orient="records"))  # type: ignore[arg-type]


def _serialize_series(series: object) -> list[dict[str, object]]:
    """Vectorized Series to list-of-dicts serialization."""
    import pandas as pd

    assert isinstance(series, pd.Series)
    s = series.dropna()
    out_df = pd.DataFrame({
        "time": s.index.astype("int64") // 10**9,
        "value": s.round(8).values,
    })
    return list(out_df.to_dict(orient="records"))  # type: ignore[arg-type]


def api_bars(request: HttpRequest, pk: int) -> JsonResponse:
    """Return OHLCV bar data for charting."""
    import math

    from pyfx.data.resample import load_bars

    run = get_object_or_404(BacktestRun, pk=pk)
    timeframe = request.GET.get("timeframe")

    if timeframe is not None and timeframe not in _VALID_TIMEFRAMES:
        return JsonResponse({"error": "Invalid timeframe"}, status=400)

    try:
        df = load_bars(run.data_file, timeframe=timeframe)
    except FileNotFoundError:
        return JsonResponse({"error": "Data file not found"}, status=404)

    # Auto-downsample if too many bars and no explicit timeframe
    if len(df) > _MAX_RESPONSE_BARS and timeframe is None:
        factor = math.ceil(len(df) / _MAX_RESPONSE_BARS)
        auto_tf = f"{factor}-MINUTE-LAST-EXTERNAL"
        df = load_bars(run.data_file, timeframe=auto_tf)

    # Hard cap on response size
    if len(df) > _MAX_RESPONSE_BARS:
        df = df.iloc[-_MAX_RESPONSE_BARS:]

    data = _serialize_bars(df)
    return JsonResponse(data, safe=False)


def api_indicators(request: HttpRequest, pk: int) -> JsonResponse:
    """Compute and return indicator values for charting."""
    from pyfx.data.resample import compute_indicator, load_bars

    run = get_object_or_404(BacktestRun, pk=pk)
    name = request.GET.get("name", "")
    period_str = request.GET.get("period", "14")
    timeframe = request.GET.get("timeframe")

    if not name or name not in _VALID_INDICATORS:
        return JsonResponse({"error": "Invalid indicator name"}, status=400)

    try:
        period = int(period_str)
    except ValueError:
        return JsonResponse({"error": "Invalid 'period' parameter"}, status=400)

    if not (1 <= period <= _MAX_INDICATOR_PERIOD):
        return JsonResponse(
            {"error": f"period must be between 1 and {_MAX_INDICATOR_PERIOD}"},
            status=400,
        )

    if timeframe is not None and timeframe not in _VALID_TIMEFRAMES:
        return JsonResponse({"error": "Invalid timeframe"}, status=400)

    try:
        df = load_bars(run.data_file, timeframe=timeframe)
    except FileNotFoundError:
        return JsonResponse({"error": "Data file not found"}, status=404)

    result = compute_indicator(df, name, period)

    if isinstance(result, dict):
        # MACD returns multiple series
        response: dict[str, list[dict[str, object]]] = {}
        for key, series in result.items():
            response[key] = _serialize_series(series)
        return JsonResponse(response)

    data = _serialize_series(result)
    return JsonResponse(data, safe=False)


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


def api_trade_markers(request: HttpRequest, pk: int) -> JsonResponse:
    """Trade entry/exit markers for the price chart (lazy-loaded)."""
    run = get_object_or_404(BacktestRun, pk=pk)
    trades = Trade.objects.filter(run=run)
    markers: list[dict[str, object]] = []
    for i, t in enumerate(trades):
        markers.append({
            "time": int(t.opened_at.timestamp()),
            "side": t.side,
            "type": "entry",
            "price": float(t.open_price),
            "pnl": round(float(t.realized_pnl), 2),
            "tradeIdx": i,
        })
        markers.append({
            "time": int(t.closed_at.timestamp()),
            "side": t.side,
            "type": "exit",
            "price": float(t.close_price),
            "pnl": round(float(t.realized_pnl), 2),
            "tradeIdx": i,
        })
    return JsonResponse(markers, safe=False)


def api_cumulative_pnl(request: HttpRequest, pk: int) -> JsonResponse:
    """Cumulative P&L series for the histogram chart (lazy-loaded)."""
    run = get_object_or_404(BacktestRun, pk=pk)
    trades = Trade.objects.filter(run=run).order_by("closed_at")
    running = 0.0
    data: list[dict[str, object]] = []
    for t in trades:
        running += t.realized_pnl
        data.append({"time": int(t.closed_at.timestamp()), "value": round(running, 2)})
    return JsonResponse(data, safe=False)


def api_chart_data(request: HttpRequest, pk: int) -> JsonResponse:
    """Combined endpoint: bars + indicators in a single request."""
    import math

    from pyfx.data.resample import compute_indicator, load_bars

    run = get_object_or_404(BacktestRun, pk=pk)
    timeframe = request.GET.get("timeframe")
    indicators_param = request.GET.get("indicators", "")

    if timeframe is not None and timeframe not in _VALID_TIMEFRAMES:
        return JsonResponse({"error": "Invalid timeframe"}, status=400)

    # Parse indicators param: "sma:20,rsi:14,macd:12"
    indicator_requests: list[tuple[str, int]] = []
    if indicators_param:
        for part in indicators_param.split(","):
            part = part.strip()
            if ":" not in part:
                return JsonResponse(
                    {"error": f"Invalid indicator format: {part!r}"}, status=400,
                )
            name, period_str = part.split(":", 1)
            if name not in _VALID_INDICATORS:
                return JsonResponse(
                    {"error": f"Invalid indicator: {name!r}"}, status=400,
                )
            try:
                period = int(period_str)
            except ValueError:
                return JsonResponse(
                    {"error": f"Invalid period: {period_str!r}"}, status=400,
                )
            if not (1 <= period <= _MAX_INDICATOR_PERIOD):
                return JsonResponse(
                    {"error": f"period must be between 1 and {_MAX_INDICATOR_PERIOD}"},
                    status=400,
                )
            indicator_requests.append((name, period))

    try:
        df = load_bars(run.data_file, timeframe=timeframe)
    except FileNotFoundError:
        return JsonResponse({"error": "Data file not found"}, status=404)

    # Auto-downsample if too many bars and no explicit timeframe
    if len(df) > _MAX_RESPONSE_BARS and timeframe is None:
        factor = math.ceil(len(df) / _MAX_RESPONSE_BARS)
        auto_tf = f"{factor}-MINUTE-LAST-EXTERNAL"
        df = load_bars(run.data_file, timeframe=auto_tf)

    # Hard cap on response size
    if len(df) > _MAX_RESPONSE_BARS:
        df = df.iloc[-_MAX_RESPONSE_BARS:]

    bars = _serialize_bars(df)

    # Compute requested indicators on same DataFrame
    indicators_response: dict[str, object] = {}
    for name, period in indicator_requests:
        key = f"{name}_{period}"
        result = compute_indicator(df, name, period)
        if isinstance(result, dict):
            indicators_response[key] = {
                k: _serialize_series(s) for k, s in result.items()
            }
        else:
            indicators_response[key] = _serialize_series(result)

    return JsonResponse({"bars": bars, "indicators": indicators_response})


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

    _spawn_management_command("run_download_web", "--dataset-id", str(dataset.pk))

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

    _spawn_management_command("run_download_web", "--dataset-id", str(dataset.pk))

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


# ---------------------------------------------------------------------------
# Paper Trading Views
# ---------------------------------------------------------------------------


def paper_list(request: HttpRequest) -> HttpResponse:
    """List all paper trading sessions."""
    sessions = PaperTradingSession.objects.all()
    running = sessions.filter(status=PaperTradingSession.STATUS_RUNNING)
    return render(request, "dashboard/paper_list.html", {
        "active_nav": "paper",
        "sessions": sessions,
        "running_sessions": running,
    })


def paper_new(request: HttpRequest) -> HttpResponse:
    """Configuration form for starting a new paper trading session."""
    from pyfx.adapters.instruments import list_supported_instruments
    from pyfx.core.config import settings as pyfx_settings

    ib_warnings = pyfx_settings.validate_ib_config()
    return render(request, "dashboard/paper_new.html", {
        "active_nav": "paper",
        "instruments": list_supported_instruments(),
        "ib_config": {
            "host": pyfx_settings.ib_host,
            "port": pyfx_settings.ib_port,
            "account_id": pyfx_settings.ib_account_id or "",
            "trading_mode": pyfx_settings.ib_trading_mode,
            "read_only_api": pyfx_settings.ib_read_only_api,
        },
        "ib_warnings": ib_warnings,
        "ib_configured": pyfx_settings.ib_account_id is not None,
        "risk_defaults": {
            "max_positions": pyfx_settings.risk_max_positions,
            "max_position_size": float(pyfx_settings.risk_max_position_size),
            "daily_loss_limit": pyfx_settings.risk_daily_loss_limit,
            "max_drawdown_pct": pyfx_settings.risk_max_drawdown_pct,
            "position_size_pct": pyfx_settings.risk_position_size_pct,
            "max_notional_per_order": pyfx_settings.risk_max_notional_per_order,
            "risk_sizing_method": pyfx_settings.risk_sizing_method,
            "account_currency": pyfx_settings.account_currency,
        },
        "errors": {},
        "submitted": {},
    })


def _validate_paper_start(post: dict[str, object]) -> dict[str, str]:
    """Validate paper trading form data. Returns field->error mapping."""
    errors: dict[str, str] = {}
    if not post.get("strategy"):
        errors["strategy"] = "Strategy is required"
    if not post.get("instruments"):
        errors["instruments"] = "Select at least one instrument"
    try:
        v = float(post.get("daily_loss_limit", 2000))  # type: ignore[arg-type]
        if v <= 0:
            errors["daily_loss_limit"] = "Must be positive"
    except (ValueError, TypeError):
        errors["daily_loss_limit"] = "Must be a number"
    try:
        v = float(post.get("max_drawdown_pct", 10))  # type: ignore[arg-type]
        if not 0 < v <= 100:
            errors["max_drawdown_pct"] = "Must be between 0 and 100"
    except (ValueError, TypeError):
        errors["max_drawdown_pct"] = "Must be a number"
    try:
        v = float(post.get("position_size_pct", 2))  # type: ignore[arg-type]
        if not 0 < v <= 100:
            errors["position_size_pct"] = "Must be between 0 and 100"
    except (ValueError, TypeError):
        errors["position_size_pct"] = "Must be a number"
    try:
        v_int = int(str(post.get("max_positions", 3)))
        if v_int < 1:
            errors["max_positions"] = "Must be at least 1"
    except (ValueError, TypeError):
        errors["max_positions"] = "Must be a whole number"
    sizing = post.get("risk_sizing_method", "fixed_fractional")
    if sizing not in ("fixed_fractional", "atr_based"):
        errors["risk_sizing_method"] = "Must be fixed_fractional or atr_based"
    return errors


def paper_start(request: HttpRequest) -> HttpResponse:
    """Start a new paper trading session via POST."""
    if request.method != "POST":
        return redirect("dashboard:paper_list")

    from pyfx.core.config import settings as pyfx_settings
    from pyfx.core.types import parse_strategy_params

    # Gather submitted values
    strategy = request.POST.get("strategy", "").strip()
    instruments = request.POST.getlist("instruments")
    bar_type = request.POST.get("bar_type", "1-MINUTE-LAST-EXTERNAL").strip()
    extra_bar_types = [
        v.strip() for v in request.POST.getlist("extra_bar_types") if v.strip()
    ]
    trade_size = request.POST.get("trade_size", "100000").strip()

    # Risk overrides
    max_positions = request.POST.get("max_positions", "3")
    max_position_size = request.POST.get("max_position_size", "100000")
    daily_loss_limit = request.POST.get("daily_loss_limit", "2000")
    max_drawdown_pct = request.POST.get("max_drawdown_pct", "10")
    position_size_pct = request.POST.get("position_size_pct", "2")
    max_notional_per_order = request.POST.get("max_notional_per_order", "500000")
    risk_sizing_method = request.POST.get("risk_sizing_method", "fixed_fractional")

    submitted = {
        "strategy": strategy,
        "instruments": instruments,
        "bar_type": bar_type,
        "extra_bar_types": extra_bar_types,
        "trade_size": trade_size,
        "max_positions": max_positions,
        "max_position_size": max_position_size,
        "daily_loss_limit": daily_loss_limit,
        "max_drawdown_pct": max_drawdown_pct,
        "position_size_pct": position_size_pct,
        "max_notional_per_order": max_notional_per_order,
        "risk_sizing_method": risk_sizing_method,
    }

    errors = _validate_paper_start({
        "strategy": strategy,
        "instruments": instruments,
        "daily_loss_limit": daily_loss_limit,
        "max_drawdown_pct": max_drawdown_pct,
        "position_size_pct": position_size_pct,
        "max_positions": max_positions,
        "risk_sizing_method": risk_sizing_method,
    })

    if errors:
        from pyfx.adapters.instruments import list_supported_instruments

        return render(request, "dashboard/paper_new.html", {
            "active_nav": "paper",
            "instruments": list_supported_instruments(),
            "ib_config": {
                "host": pyfx_settings.ib_host,
                "port": pyfx_settings.ib_port,
                "account_id": pyfx_settings.ib_account_id or "",
                "trading_mode": pyfx_settings.ib_trading_mode,
                "read_only_api": pyfx_settings.ib_read_only_api,
            },
            "ib_warnings": pyfx_settings.validate_ib_config(),
            "ib_configured": pyfx_settings.ib_account_id is not None,
            "risk_defaults": {
                "max_positions": pyfx_settings.risk_max_positions,
                "max_position_size": float(pyfx_settings.risk_max_position_size),
                "daily_loss_limit": pyfx_settings.risk_daily_loss_limit,
                "max_drawdown_pct": pyfx_settings.risk_max_drawdown_pct,
                "position_size_pct": pyfx_settings.risk_position_size_pct,
                "max_notional_per_order": pyfx_settings.risk_max_notional_per_order,
                "risk_sizing_method": pyfx_settings.risk_sizing_method,
                "account_currency": pyfx_settings.account_currency,
            },
            "errors": errors,
            "submitted": submitted,
        })

    # Strategy params
    raw_params = {
        key[6:]: str(request.POST[key])
        for key in request.POST
        if key.startswith("param_")
    }
    strategy_params: dict[str, bool | int | float | str] = parse_strategy_params(
        raw_params,
    )

    risk_overrides = {
        "risk_max_positions": int(max_positions),
        "risk_max_position_size": int(max_position_size),
        "risk_daily_loss_limit": float(daily_loss_limit),
        "risk_max_drawdown_pct": float(max_drawdown_pct),
        "risk_position_size_pct": float(position_size_pct),
        "risk_max_notional_per_order": int(max_notional_per_order),
        "risk_sizing_method": risk_sizing_method,
    }

    config_json = {
        "strategy": strategy,
        "instruments": instruments,
        "bar_type": bar_type,
        "extra_bar_types": extra_bar_types,
        "strategy_params": strategy_params,
        "trade_size": trade_size,
        "account_currency": pyfx_settings.account_currency,
        "risk_overrides": risk_overrides,
    }

    session = PaperTradingSession.objects.create(
        status=PaperTradingSession.STATUS_RUNNING,
        strategy=strategy,
        instrument=",".join(instruments),
        bar_type=bar_type,
        started_at=datetime.now(UTC),
        account_currency=pyfx_settings.account_currency,
        account_id=pyfx_settings.ib_account_id or "",
        config_json=config_json,
    )

    _spawn_management_command("run_paper_web", "--session-id", str(session.pk))

    return redirect("dashboard:paper_list")


def paper_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Detail view for a single paper trading session."""
    session = get_object_or_404(PaperTradingSession, pk=pk)
    trades = PaperTrade.objects.filter(session=session)
    events = SessionEvent.objects.filter(session=session)[:50]
    open_trades = trades.filter(closed_at__isnull=True)
    closed_trades = trades.filter(closed_at__isnull=False)

    matching_backtests = BacktestRun.objects.filter(
        strategy=session.strategy,
        instrument=session.instrument,
        status=BacktestRun.STATUS_COMPLETED,
    ).order_by("-created_at")[:10]

    return render(request, "dashboard/paper_detail.html", {
        "active_nav": "paper",
        "session": session,
        "trades": trades,
        "events": events,
        "open_trades": open_trades,
        "closed_trades": closed_trades,
        "matching_backtests": matching_backtests,
    })


def paper_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete a paper trading session."""
    if request.method != "POST":
        return HttpResponse(status=405)
    session = get_object_or_404(PaperTradingSession, pk=pk)
    session.delete()
    return redirect("dashboard:paper_list")


def comparison_view(request: HttpRequest, paper_pk: int, backtest_pk: int) -> HttpResponse:
    """Side-by-side comparison of paper session vs backtest."""
    session = get_object_or_404(PaperTradingSession, pk=paper_pk)
    bt_run = get_object_or_404(BacktestRun, pk=backtest_pk)

    from pyfx.analysis.comparison import compare_sessions

    report = compare_sessions(session_id=paper_pk, backtest_id=backtest_pk)

    return render(request, "dashboard/comparison.html", {
        "active_nav": "paper",
        "session": session,
        "bt_run": bt_run,
        "report": report,
    })


def risk_dashboard(request: HttpRequest) -> HttpResponse:
    """Risk monitoring dashboard."""
    running_sessions = list(
        PaperTradingSession.objects.filter(
            status=PaperTradingSession.STATUS_RUNNING,
        ).order_by("-started_at")
    )

    # Allow selecting a specific session via ?session_id=N
    session_id = request.GET.get("session_id")
    if session_id:
        session = PaperTradingSession.objects.filter(pk=int(session_id)).first()
    elif running_sessions:
        session = running_sessions[0]
    else:
        session = PaperTradingSession.objects.order_by("-started_at").first()

    snapshots: list[object] = []
    events: list[object] = []
    open_trades: list[object] = []

    if session is not None:
        snapshots = list(
            RiskSnapshot.objects.filter(session=session).order_by("-timestamp")[:50]
        )
        events = list(
            SessionEvent.objects.filter(
                session=session,
                event_type__in=["risk_warning", "circuit_breaker", "position_limit"],
            ).order_by("-timestamp")[:20]
        )
        open_trades = list(
            PaperTrade.objects.filter(session=session, closed_at__isnull=True)
        )

    return render(request, "dashboard/risk.html", {
        "active_nav": "risk",
        "session": session,
        "running_sessions": running_sessions,
        "snapshots": snapshots,
        "risk_events": events,
        "open_trades": open_trades,
    })


def api_paper_trades(request: HttpRequest, pk: int) -> JsonResponse:
    """Return all trades for a paper trading session."""
    session = get_object_or_404(PaperTradingSession, pk=pk)
    trades = PaperTrade.objects.filter(session=session)
    return JsonResponse([
        {
            "id": t.pk,
            "instrument": t.instrument,
            "side": t.side,
            "quantity": t.quantity,
            "open_price": t.open_price,
            "close_price": t.close_price,
            "realized_pnl": t.realized_pnl,
            "opened_at": t.opened_at.isoformat(),
            "closed_at": t.closed_at.isoformat() if t.closed_at else None,
            "fill_latency_ms": t.fill_latency_ms,
            "slippage_ticks": t.slippage_ticks,
            "is_open": t.is_open,
        }
        for t in trades
    ], safe=False)


def api_paper_events(request: HttpRequest, pk: int) -> JsonResponse:
    """Return events for a paper trading session."""
    session = get_object_or_404(PaperTradingSession, pk=pk)
    events = SessionEvent.objects.filter(session=session)[:100]
    return JsonResponse([
        {
            "timestamp": e.timestamp.isoformat(),
            "event_type": e.event_type,
            "message": e.message,
        }
        for e in events
    ], safe=False)


def api_paper_risk_snapshots(request: HttpRequest, pk: int) -> JsonResponse:
    """Return risk snapshots for a paper trading session."""
    session = get_object_or_404(PaperTradingSession, pk=pk)
    snapshots = RiskSnapshot.objects.filter(session=session).order_by("timestamp")
    return JsonResponse([
        {
            "timestamp": s.timestamp.isoformat(),
            "equity": s.equity,
            "daily_pnl": s.daily_pnl,
            "open_positions": s.open_positions,
            "drawdown_pct": s.drawdown_pct,
            "utilization_pct": s.utilization_pct,
        }
        for s in snapshots
    ], safe=False)


def api_comparison_data(
    request: HttpRequest, paper_pk: int, backtest_pk: int,
) -> JsonResponse:
    """Return comparison data as JSON."""
    from pyfx.analysis.comparison import compare_sessions

    report = compare_sessions(session_id=paper_pk, backtest_id=backtest_pk)
    return JsonResponse(report.model_dump(mode="json"))


def api_paper_equity_curve(request: HttpRequest, pk: int) -> JsonResponse:
    """Return equity curve from risk snapshots for a paper session."""
    session = get_object_or_404(PaperTradingSession, pk=pk)
    snapshots = RiskSnapshot.objects.filter(session=session).order_by("timestamp")
    data = [
        {"time": s.timestamp.isoformat(), "value": round(s.equity, 2)}
        for s in snapshots
    ]
    return JsonResponse(data, safe=False)


def api_paper_cumulative_pnl(request: HttpRequest, pk: int) -> JsonResponse:
    """Return cumulative P&L from closed paper trades."""
    session = get_object_or_404(PaperTradingSession, pk=pk)
    trades = (
        PaperTrade.objects
        .filter(session=session, closed_at__isnull=False, realized_pnl__isnull=False)
        .order_by("closed_at")
    )
    running = 0.0
    data = []
    for t in trades:
        running += t.realized_pnl  # type: ignore[operator]
        data.append({
            "time": int(t.closed_at.timestamp()),  # type: ignore[union-attr]
            "value": round(running, 2),
        })
    return JsonResponse(data, safe=False)


def api_paper_trade_markers(request: HttpRequest, pk: int) -> JsonResponse:
    """Return entry/exit markers for paper trades."""
    session = get_object_or_404(PaperTradingSession, pk=pk)
    trades = PaperTrade.objects.filter(session=session).order_by("opened_at")
    markers: list[dict[str, object]] = []
    for idx, t in enumerate(trades):
        markers.append({
            "time": int(t.opened_at.timestamp()),
            "side": t.side,
            "type": "entry",
            "price": t.open_price,
            "pnl": None,
            "tradeIdx": idx,
        })
        if t.closed_at and t.close_price is not None:
            markers.append({
                "time": int(t.closed_at.timestamp()),
                "side": t.side,
                "type": "exit",
                "price": t.close_price,
                "pnl": t.realized_pnl,
                "tradeIdx": idx,
            })
    return JsonResponse(markers, safe=False)


# ---------------------------------------------------------------------------
# API: IB config & instruments
# ---------------------------------------------------------------------------


def api_ib_config(request: HttpRequest) -> JsonResponse:
    """Return IB connection config (no password) and validation warnings."""
    from pyfx.core.config import settings as pyfx_settings

    account_id = pyfx_settings.ib_account_id or ""
    return JsonResponse({
        "host": pyfx_settings.ib_host,
        "port": pyfx_settings.ib_port,
        "account_id": account_id,
        "trading_mode": pyfx_settings.ib_trading_mode,
        "read_only_api": pyfx_settings.ib_read_only_api,
        "account_type": "paper" if account_id.startswith("DU") else "live",
        "configured": bool(pyfx_settings.ib_account_id),
        "warnings": pyfx_settings.validate_ib_config(),
    })


def api_supported_instruments(request: HttpRequest) -> JsonResponse:
    """Return supported instruments with metadata."""
    from pyfx.adapters.instruments import get_instrument_meta, list_supported_instruments

    instruments = list_supported_instruments()
    data = []
    for name in instruments:
        meta = get_instrument_meta(name)
        data.append({
            "name": name,
            "tick_size": float(meta.tick_size),
            "lot_size": meta.lot_size,
            "pip_value": float(meta.pip_value),
        })
    return JsonResponse(data, safe=False)
