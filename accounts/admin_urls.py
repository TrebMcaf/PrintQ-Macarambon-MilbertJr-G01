from django.urls import path

from . import admin_views as v

urlpatterns = [
    path("", v.admin_dashboard, name="admin_dashboard"),
    path("users/", v.user_list, name="admin_users"),
    path("users/new/", v.user_create, name="admin_user_create"),
    path("users/<int:user_id>/", v.user_detail, name="admin_user_detail"),
    path("users/<int:user_id>/edit/", v.user_edit, name="admin_user_edit"),
    path("users/<int:user_id>/password/", v.user_password, name="admin_user_password"),
    path("users/<int:user_id>/toggle-active/", v.toggle_user_active, name="admin_toggle_active"),
    path("students/", v.student_list, name="admin_students"),
    path("students/<int:user_id>/", v.student_detail, name="admin_student_detail"),
    path("operators/", v.operator_list, name="admin_operators"),
    path("shop/", v.shop_settings, name="admin_shop"),
]
