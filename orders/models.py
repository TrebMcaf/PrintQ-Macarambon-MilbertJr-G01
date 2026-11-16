from django.conf import settings
from django.db import models
from django.db.models import Q


class Status(models.TextChoices):
    PENDING = "PENDING", "Pending"
    PRINTING = "PRINTING", "Printing"
    READY = "READY", "Ready for pickup"
    COMPLETED = "COMPLETED", "Completed"
    CANCELLED = "CANCELLED", "Cancelled"


# The order state machine. Every status change must go through
# orders.services.transition_status(), which enforces this map.
ALLOWED_TRANSITIONS = {
    Status.PENDING: {Status.PRINTING, Status.CANCELLED},
    Status.PRINTING: {Status.READY},
    Status.READY: {Status.COMPLETED},
    Status.COMPLETED: set(),
    Status.CANCELLED: set(),
}


class BindingType(models.TextChoices):
    NONE = "NONE", "None"
    STAPLE = "STAPLE", "Staple"
    RING_BIND = "RING_BIND", "Ring bind"


class Order(models.Model):
    """SDD table `orders` (transaction header)."""

    order_id = models.AutoField(primary_key=True)
    shop = models.ForeignKey("catalog.Shop", on_delete=models.RESTRICT, db_column="shop_id",
                             related_name="orders")  # BR-14
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, db_column="user_id",
                             related_name="orders")
    tracking_code = models.CharField(max_length=12, unique=True)  # BR-03
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "orders"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="orders_status_created_idx"),
            models.Index(fields=["created_at"], name="orders_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=Status.values), name="orders_status_chk"),  # BR-04
            models.CheckConstraint(condition=Q(total_amount__gte=0), name="orders_total_chk"),  # BR-05
        ]

    def __str__(self):
        return self.tracking_code

    @property
    def can_cancel(self):  # BR-12
        return self.status == Status.PENDING

    @property
    def next_statuses(self):
        return sorted(ALLOWED_TRANSITIONS[Status(self.status)] - {Status.CANCELLED})


class OrderItem(models.Model):
    """SDD table `order_items` — one uploaded document per row."""

    item_id = models.AutoField(primary_key=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, db_column="order_id", related_name="items")
    rate = models.ForeignKey("catalog.PrintRate", on_delete=models.RESTRICT, db_column="rate_id",
                             related_name="order_items")  # BR-11
    file_name = models.CharField(max_length=255)
    file_url = models.CharField(max_length=500, help_text="Object path in the private storage bucket")
    file_size_mb = models.DecimalField(max_digits=5, decimal_places=2)
    page_count = models.IntegerField()
    copies = models.IntegerField(default=1)
    binding_type = models.CharField(max_length=20, choices=BindingType.choices, default=BindingType.NONE)
    unit_price = models.DecimalField(max_digits=6, decimal_places=2)  # BR-09 snapshot
    subtotal = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        db_table = "order_items"
        constraints = [
            models.CheckConstraint(condition=Q(file_size_mb__gt=0) & Q(file_size_mb__lte=25),
                                   name="order_items_file_size_chk"),  # BR-06
            models.CheckConstraint(condition=Q(page_count__gte=1), name="order_items_page_count_chk"),  # BR-08
            models.CheckConstraint(condition=Q(copies__gte=1), name="order_items_copies_chk"),  # BR-07
            models.CheckConstraint(condition=Q(binding_type__in=BindingType.values),
                                   name="order_items_binding_chk"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="order_items_unit_price_chk"),
            models.CheckConstraint(condition=Q(subtotal__gte=0), name="order_items_subtotal_chk"),
        ]

    def __str__(self):
        return f"{self.file_name} ×{self.copies}"


class OrderStatusLog(models.Model):
    """SDD table `order_status_logs` — immutable audit trail (BR-13)."""

    log_id = models.AutoField(primary_key=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, db_column="order_id", related_name="status_logs")
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, db_column="changed_by",
                                   related_name="status_changes")
    previous_status = models.CharField(max_length=15, choices=Status.choices, null=True, blank=True)
    new_status = models.CharField(max_length=15, choices=Status.choices)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "order_status_logs"
        ordering = ["changed_at"]
        constraints = [
            models.CheckConstraint(condition=Q(previous_status__isnull=True) | Q(previous_status__in=Status.values),
                                   name="status_logs_prev_chk"),
            models.CheckConstraint(condition=Q(new_status__in=Status.values), name="status_logs_new_chk"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Status logs are immutable.")
        super().save(*args, **kwargs)
