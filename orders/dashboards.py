from decimal import Decimal

from django.db.models import Count, Sum
from django.shortcuts import render
from django.utils import timezone

from accounts.decorators import operator_required, student_required
from catalog.models import Shop

from .models import Order, OrderStatusLog, Status

QUEUE_SECTIONS = [
    (Status.PENDING, "Waiting"),
    (Status.PRINTING, "Printing"),
    (Status.READY, "Ready for pickup"),
]


def queue_groups():
    """Active orders grouped by status for the operator board, oldest first in each group."""
    base = Order.objects.select_related("user").prefetch_related("items")
    return [
        {"status": status, "label": label,
         "orders": list(base.filter(status=status).order_by("created_at"))}
        for status, label in QUEUE_SECTIONS
    ]


@student_required
def student_dashboard(request):
    mine = Order.objects.filter(user=request.user)
    counts = dict(mine.values_list("status").annotate(n=Count("order_id")))
    ctx = {
        "shop": Shop.current(),
        "active_count": counts.get(Status.PENDING, 0) + counts.get(Status.PRINTING, 0),
        "ready_count": counts.get(Status.READY, 0),
        "completed_count": counts.get(Status.COMPLETED, 0),
        "ready_orders": mine.filter(status=Status.READY).select_related("shop").order_by("created_at"),
        "recent_orders": mine.select_related("shop").order_by("-created_at")[:5],
    }
    return render(request, "dashboards/student.html", ctx)


@operator_required
def operator_dashboard(request):
    today = timezone.localdate()
    counts = dict(Order.objects.values_list("status").annotate(n=Count("order_id")))
    completed_ids = OrderStatusLog.objects.filter(new_status=Status.COMPLETED, changed_at__date=today).values("order_id")
    completed_today = Order.objects.filter(order_id__in=completed_ids, status=Status.COMPLETED)
    ctx = {
        "shop": Shop.current(),
        "pending_count": counts.get(Status.PENDING, 0),
        "printing_count": counts.get(Status.PRINTING, 0),
        "ready_count": counts.get(Status.READY, 0),
        "placed_today": Order.objects.filter(created_at__date=today).count(),
        "completed_today": completed_today.count(),
        "collected_today": completed_today.aggregate(s=Sum("total_amount"))["s"] or Decimal("0.00"),
        "oldest_pending": Order.objects.filter(status=Status.PENDING).select_related("user").order_by("created_at")[:5],
        "groups": queue_groups(),
    }
    return render(request, "dashboards/operator.html", ctx)
