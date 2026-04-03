import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .models import BacktestRun, EquitySnapshot, Trade


def backtest_list(request):
    runs = BacktestRun.objects.all()
    return render(request, "dashboard/backtest_list.html", {"runs": runs})


def backtest_detail(request, pk):
    run = get_object_or_404(BacktestRun, pk=pk)
    trades = run.trade_set.all()

    # Compute cumulative PnL for the trades chart
    cumulative_pnl = []
    running = 0.0
    for t in trades:
        running += t.realized_pnl
        cumulative_pnl.append(round(running, 2))

    return render(request, "dashboard/backtest_detail.html", {
        "run": run,
        "trades": trades,
        "cumulative_pnl_json": json.dumps(cumulative_pnl),
        "strategy_params_json": json.dumps(run.strategy_params, indent=2),
    })


def backtest_delete(request, pk):
    run = get_object_or_404(BacktestRun, pk=pk)
    if request.method == "POST":
        run.delete()
        return redirect("dashboard:backtest_list")
    return redirect("dashboard:backtest_detail", pk=pk)


def api_equity_curve(request, pk):
    run = get_object_or_404(BacktestRun, pk=pk)
    snapshots = EquitySnapshot.objects.filter(run=run)
    data = [
        {"time": s.timestamp.isoformat(), "value": round(s.balance, 2)}
        for s in snapshots
    ]
    return JsonResponse(data, safe=False)


def api_trades(request, pk):
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
