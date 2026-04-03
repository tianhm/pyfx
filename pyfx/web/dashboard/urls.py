from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.backtest_list, name="backtest_list"),
    path("run/<int:pk>/", views.backtest_detail, name="backtest_detail"),
    path("run/<int:pk>/delete/", views.backtest_delete, name="backtest_delete"),
    path("api/run/<int:pk>/equity/", views.api_equity_curve, name="api_equity_curve"),
    path("api/run/<int:pk>/trades/", views.api_trades, name="api_trades"),
]
