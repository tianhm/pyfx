from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("backtests/", views.backtest_list, name="backtest_list"),
    path("backtests/start/", views.backtest_start, name="backtest_start"),
    path("run/<int:pk>/", views.backtest_detail, name="backtest_detail"),
    path("run/<int:pk>/delete/", views.backtest_delete, name="backtest_delete"),
    path("api/strategies/", views.api_strategies, name="api_strategies"),
    path("api/run/<int:pk>/status/", views.api_backtest_status, name="api_backtest_status"),
    path("api/run/<int:pk>/equity/", views.api_equity_curve, name="api_equity_curve"),
    path("api/run/<int:pk>/trades/", views.api_trades, name="api_trades"),
    path("api/running-count/", views.api_running_count, name="api_running_count"),
    path("api/running-backtests/", views.api_running_backtests, name="api_running_backtests"),
]
