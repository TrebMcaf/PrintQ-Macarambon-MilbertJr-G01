from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("", views.my_orders, name="my_orders"),
    path("new/", views.new_order, name="new_order"),
    path("queue/", views.queue, name="queue"),
    path("scan/", views.scan, name="scan"),
    path("scan/find/", views.scan_lookup, name="scan_lookup"),
    path("shop/status/", views.shop_status, name="shop_status"),
    path("track/", views.track, name="track"),
    path("live/", views.live, name="live"),
    path("<int:order_id>/", views.order_detail, name="order_detail"),
    path("<int:order_id>/notes/", views.edit_notes, name="edit_notes"),
    path("<int:order_id>/cancel/", views.cancel, name="cancel"),
    path("<int:order_id>/advance/", views.advance, name="advance"),
    path("confirm/", views.confirm_order, name="confirm_order"),
    path("items/<int:item_id>/download/", views.download_item, name="download_item"),
]
