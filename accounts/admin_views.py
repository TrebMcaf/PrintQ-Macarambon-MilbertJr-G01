"""Admin-role views: oversight of the shop, students and operators."""
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from catalog.models import PrintRate, Shop
from orders.models import Order, Status

from .decorators import admin_required
from .forms import OperatorCreateForm, RateFormSet, ShopForm
from .models import Role, User

ZERO = Decimal("0.00")


@admin_required
def admin_dashboard(request):
    active = User.objects.filter(is_active=True)
    revenue = Order.objects.filter(status=Status.COMPLETED).aggregate(s=Sum("total_amount"))["s"]
    ctx = {
        "shop": Shop.current(),
        "student_count": active.filter(role=Role.STUDENT).count(),
        "operator_count": active.filter(role=Role.OPERATOR).count(),
        "orders_total": Order.objects.count(),
        "active_orders": Order.objects.filter(
            status__in=[Status.PENDING, Status.PRINTING, Status.READY]).count(),
        "orders_today": Order.objects.filter(created_at__date=timezone.localdate()).count(),
        "revenue_total": revenue or ZERO,
        "recent_students": User.objects.filter(role=Role.STUDENT).annotate(order_count=Count("orders"))
        .order_by("-created_at", "-user_id")[:5],
    }
    return render(request, "manage/dashboard.html", ctx)


@admin_required
def student_list(request):
    q = request.GET.get("q", "").strip()
    students = User.objects.filter(role=Role.STUDENT)
    if q:
        students = students.filter(Q(full_name__icontains=q) | Q(email__icontains=q))
    students = students.annotate(
        order_count=Count("orders"),
        order_total=Coalesce(Sum("orders__total_amount"), Value(ZERO), output_field=DecimalField()),
    ).order_by("full_name", "user_id")
    page = Paginator(students, 25).get_page(request.GET.get("page"))
    return render(request, "manage/students.html", {"page": page, "q": q, "total": page.paginator.count})


@admin_required
def student_detail(request, user_id):
    student = get_object_or_404(User, pk=user_id, role=Role.STUDENT)
    orders = Order.objects.filter(user=student).order_by("-created_at")
    return render(request, "manage/student_detail.html", {"student": student, "orders": orders})


@require_POST
@admin_required
def toggle_user_active(request, user_id):
    target = get_object_or_404(User, pk=user_id)
    nxt = request.POST.get("next", "")
    safe = bool(nxt) and url_has_allowed_host_and_scheme(
        nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    dest = nxt if safe else "admin_students"
    if target.pk == request.user.pk:
        messages.error(request, "You cannot change your own account status.")
    elif target.role not in (Role.STUDENT, Role.OPERATOR):
        messages.error(request, "Admin accounts cannot be deactivated here.")
    else:
        target.is_active = not target.is_active
        target.save(update_fields=["is_active"])
        messages.success(request, f"{target.full_name} {'reactivated' if target.is_active else 'deactivated'}.")
    return redirect(dest)


@admin_required
def operator_list(request):
    form = OperatorCreateForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            op = form.save()
            messages.success(request, f"Operator {op.full_name} created.")
            return redirect("admin_operators")
        messages.error(request, "Please fix the errors below.")
    operators = User.objects.filter(role=Role.OPERATOR).order_by("full_name", "user_id")
    return render(request, "manage/operators.html", {"operators": operators, "form": form})


@admin_required
def shop_settings(request):
    shop = Shop.current()
    if shop is None:
        messages.error(request, "No shop is configured yet. Run python manage.py seed to create it.")
        return render(request, "manage/shop.html", {"shop": None})
    rates_qs = PrintRate.objects.order_by("paper_size", "-color_mode")
    shop_form = ShopForm(instance=shop)
    rate_formset = RateFormSet(queryset=rates_qs, prefix="rates")
    if request.method == "POST":
        section = request.POST.get("section")
        if section == "shop":
            shop_form = ShopForm(request.POST, instance=shop)
            if shop_form.is_valid():
                shop_form.save()
                messages.success(request, "Shop details saved.")
                return redirect("admin_shop")
            messages.error(request, "Please fix the errors below.")
        elif section == "rates":
            rate_formset = RateFormSet(request.POST, queryset=rates_qs, prefix="rates")
            if rate_formset.is_valid():
                rate_formset.save()
                messages.success(request, "Print rates saved.")
                return redirect("admin_shop")
            messages.error(request, "Please fix the errors below.")
    return render(request, "manage/shop.html", {"shop": shop, "shop_form": shop_form,
                                                "rate_formset": rate_formset})
