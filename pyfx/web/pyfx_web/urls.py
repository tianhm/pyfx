from django.urls import include, path

urlpatterns = [
    path("", include("pyfx.web.dashboard.urls")),
]
