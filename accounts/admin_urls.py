from django.urls import path

from . import admin_views as v

urlpatterns = [
    path("", v.admin_dashboard, name="admin_dashboard"),
    path("students/", v.student_list, name="admin_students"),
    path("students/<int:user_id>/", v.student_detail, name="admin_student_detail"),
    path("users/<int:user_id>/toggle-active/", v.toggle_user_active, name="admin_toggle_active"),
    path("operators/", v.operator_list, name="admin_operators"),
    path("shop/", v.shop_settings, name="admin_shop"),
]
