from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("backtests/", views.backtest_list, name="backtest_list"),
    path("backtests/new/", views.backtest_new, name="backtest_new"),
    path("backtests/start/", views.backtest_start, name="backtest_start"),
    path("run/<int:pk>/", views.backtest_detail, name="backtest_detail"),
    path("run/<int:pk>/delete/", views.backtest_delete, name="backtest_delete"),
    path("run/<int:pk>/rerun/", views.backtest_rerun, name="backtest_rerun"),
    path("api/strategies/", views.api_strategies, name="api_strategies"),
    path("api/run/<int:pk>/status/", views.api_backtest_status, name="api_backtest_status"),
    path("api/run/<int:pk>/bars/", views.api_bars, name="api_bars"),
    path("api/run/<int:pk>/chart-data/", views.api_chart_data, name="api_chart_data"),
    path("api/run/<int:pk>/indicators/", views.api_indicators, name="api_indicators"),
    path("api/run/<int:pk>/equity/", views.api_equity_curve, name="api_equity_curve"),
    path("api/run/<int:pk>/trades/", views.api_trades, name="api_trades"),
    path("api/run/<int:pk>/trade-markers/", views.api_trade_markers, name="api_trade_markers"),
    path("api/run/<int:pk>/cumulative-pnl/", views.api_cumulative_pnl, name="api_cumulative_pnl"),
    path("api/running-count/", views.api_running_count, name="api_running_count"),
    path("api/running-backtests/", views.api_running_backtests, name="api_running_backtests"),
    # Dataset routes
    path("data/", views.dataset_list, name="dataset_list"),
    path("data/new/", views.dataset_new, name="dataset_new"),
    path("data/start/", views.dataset_start, name="dataset_start"),
    path("api/data/", views.api_datasets, name="api_datasets"),
    path("api/data/<int:pk>/status/", views.api_dataset_status, name="api_dataset_status"),
    path("api/data/<int:pk>/delete/", views.dataset_delete, name="dataset_delete"),
    path("api/data/<int:pk>/redownload/", views.dataset_redownload, name="dataset_redownload"),
    path("api/data/running/", views.api_running_downloads, name="api_running_downloads"),
    # Paper trading routes
    path("paper/", views.paper_list, name="paper_list"),
    path("paper/<int:pk>/", views.paper_detail, name="paper_detail"),
    path("paper/<int:pk>/delete/", views.paper_delete, name="paper_delete"),
    path(
        "paper/<int:paper_pk>/compare/<int:backtest_pk>/",
        views.comparison_view,
        name="comparison_view",
    ),
    path("api/paper/<int:pk>/trades/", views.api_paper_trades, name="api_paper_trades"),
    path("api/paper/<int:pk>/events/", views.api_paper_events, name="api_paper_events"),
    path(
        "api/paper/<int:pk>/risk-snapshots/",
        views.api_paper_risk_snapshots,
        name="api_paper_risk_snapshots",
    ),
    path(
        "api/compare/<int:paper_pk>/<int:backtest_pk>/",
        views.api_comparison_data,
        name="api_comparison_data",
    ),
    path(
        "api/paper/<int:pk>/equity/",
        views.api_paper_equity_curve,
        name="api_paper_equity_curve",
    ),
    path(
        "api/paper/<int:pk>/cumulative-pnl/",
        views.api_paper_cumulative_pnl,
        name="api_paper_cumulative_pnl",
    ),
    path(
        "api/paper/<int:pk>/trade-markers/",
        views.api_paper_trade_markers,
        name="api_paper_trade_markers",
    ),
    # Risk dashboard
    path("risk/", views.risk_dashboard, name="risk_dashboard"),
]
