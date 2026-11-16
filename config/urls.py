from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import include, path

from orders import dashboards


@login_required
def home(request):
    if request.user.is_admin:
        return redirect("admin_dashboard")
    if request.user.is_operator:
        return redirect("operator_dashboard")
    return redirect("student_dashboard")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("manage/", include("accounts.admin_urls")),
    path("orders/", include("orders.urls")),
    path("dashboard/student/", dashboards.student_dashboard, name="student_dashboard"),
    path("dashboard/operator/", dashboards.operator_dashboard, name="operator_dashboard"),
    path("", home, name="home"),
]
