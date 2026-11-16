from django.contrib import admin

from .models import Order, OrderItem, OrderStatusLog


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ["unit_price", "subtotal"]


class StatusLogInline(admin.TabularInline):
    model = OrderStatusLog
    extra = 0
    can_delete = False
    readonly_fields = ["previous_status", "new_status", "changed_by", "changed_at"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["tracking_code", "user", "status", "total_amount", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["tracking_code", "user__full_name", "user__email"]
    # Status is read-only here so admin edits can't skip the audit log (BR-13).
    readonly_fields = ["tracking_code", "status", "total_amount", "created_at", "updated_at"]
    inlines = [OrderItemInline, StatusLogInline]
