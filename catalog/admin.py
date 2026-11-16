from django.contrib import admin

from .models import PrintRate, Shop


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ["name", "location", "is_active"]


@admin.register(PrintRate)
class PrintRateAdmin(admin.ModelAdmin):
    list_display = ["paper_size", "color_mode", "price_per_page", "is_active"]
    list_editable = ["price_per_page", "is_active"]

    def has_delete_permission(self, request, obj=None):  # BR-11: disable, don't delete
        return False
