"""Admin-role views: oversight of the shop and administration of all user accounts."""
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import SetPasswordForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from catalog.models import PrintRate, Shop
from orders.models import Order, Status

from .decorators import admin_required
from .forms import AdminUserCreateForm, AdminUserForm, RateFormSet, ShopForm
from .models import Role, User
from .services import apply_account_change, can_reset_password, is_locked

ZERO = Decimal("0.00")


@admin_required
def admin_dashboard(request):
    active = User.objects.filter(is_active=True)
    revenue = Order.objects.filter(status=Status.COMPLETED).aggregate(s=Sum("total_amount"))["s"]
    ctx = {
        "shop": Shop.current(),
        "student_count": active.filter(role=Role.STUDENT).count(),
        "operator_count": active.filter(role=Role.OPERATOR).count(),
        "admin_count": active.filter(role=Role.ADMIN).count(),
        "orders_total": Order.objects.count(),
        "active_orders": Order.objects.filter(
            status__in=[Status.PENDING, Status.PRINTING, Status.READY]).count(),
        "orders_today": Order.objects.filter(created_at__date=timezone.localdate()).count(),
        "revenue_total": revenue or ZERO,
        "recent_students": User.objects.filter(role=Role.STUDENT).annotate(order_count=Count("orders"))
        .order_by("-created_at", "-user_id")[:5],
        "recent_orders": Order.objects.select_related("user").order_by("-created_at", "-pk")[:8],
    }
    return render(request, "manage/dashboard.html", ctx)


@admin_required
def user_list(request):
    q = request.GET.get("q", "").strip()
    r = request.GET.get("role", "")
    role = r if r in Role.values else ""
    s = request.GET.get("status", "")
    status = s if s in ("active", "inactive") else ""

    base = User.objects.all()
    if q:
        base = base.filter(Q(full_name__icontains=q) | Q(email__icontains=q))
    if status == "active":
        base = base.filter(is_active=True)
    elif status == "inactive":
        base = base.filter(is_active=False)
    role_counts = base.aggregate(
        ALL=Count("pk"),
        STUDENT=Count("pk", filter=Q(role=Role.STUDENT)),
        OPERATOR=Count("pk", filter=Q(role=Role.OPERATOR)),
        ADMIN=Count("pk", filter=Q(role=Role.ADMIN)),
    )

    users = base.filter(role=role) if role else base
    users = users.annotate(
        order_count=Count("orders"),
        order_total=Coalesce(Sum("orders__total_amount"), Value(ZERO), output_field=DecimalField()),
    ).order_by("full_name", "user_id")
    page = Paginator(users, 25).get_page(request.GET.get("page"))
    ctx = {
        "page": page, "q": q, "role": role, "status": status, "role_counts": role_counts,
        "total": page.paginator.count, "roles": Role.choices,
    }
    return render(request, "manage/users.html", ctx)


@admin_required
def user_detail(request, user_id):
    target = get_object_or_404(User, pk=user_id)
    is_self = target.pk == request.user.pk
    locked = is_locked(target)
    ctx = {
        "target": target,
        "orders": Order.objects.filter(user=target).order_by("-created_at"),
        "is_self": is_self,
        "locked": locked,
        "can_toggle": not is_self and not locked,
        "can_reset": can_reset_password(request.user, target),
    }
    return render(request, "manage/user_detail.html", ctx)


@admin_required
def user_create(request):
    form = AdminUserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        new_user = form.save()
        messages.success(request, f"Created {new_user.full_name} ({new_user.get_role_display()}).")
        return redirect("admin_user_detail", user_id=new_user.pk)
    return render(request, "manage/user_form.html", {"form": form, "mode": "create"})


@admin_required
def user_edit(request, user_id):
    form = AdminUserForm(request.POST or None, instance=get_object_or_404(User, pk=user_id), actor=request.user)
    target = get_object_or_404(User, pk=user_id)
    is_self = target.pk == request.user.pk
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            apply_account_change(request.user, target.pk, role=cd["role"], is_active=cd["is_active"],
                                 full_name=cd["full_name"], email=cd["email"],
                                 phone_number=cd["phone_number"])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            target.refresh_from_db()
            messages.success(request, f"Saved changes to {target.full_name}.")
            if is_self and cd["role"] != Role.ADMIN:
                messages.info(request, "You are no longer an admin.")
                return redirect("home")
            return redirect("admin_user_detail", user_id=target.pk)
    return render(request, "manage/user_form.html",
                  {"form": form, "target": target, "is_self": is_self, "mode": "edit"})


@admin_required
def user_password(request, user_id):
    target = get_object_or_404(User, pk=user_id)
    if not can_reset_password(request.user, target):
        raise PermissionDenied
    form = SetPasswordForm(target, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        if target.pk == request.user.pk:
            update_session_auth_hash(request, target)
        messages.success(request, f"Password updated for {target.full_name}. Share it with them in person.")
        return redirect("admin_user_detail", user_id=target.pk)
    return render(request, "manage/user_password.html",
                  {"form": form, "target": target, "can_reset": True})


@require_POST
@admin_required
def toggle_user_active(request, user_id):
    target = get_object_or_404(User, pk=user_id)
    nxt = request.POST.get("next", "")
    safe = bool(nxt) and url_has_allowed_host_and_scheme(
        nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    dest = nxt if safe else "admin_users"
    new_active = not target.is_active
    try:
        apply_account_change(request.user, target.pk, is_active=new_active)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        messages.success(request, f"{target.full_name} {'reactivated' if new_active else 'deactivated'}.")
    return redirect(dest)


def _users_redirect(role, q=""):
    params = {"role": role}
    if q:
        params["q"] = q
    return redirect(reverse("admin_users") + "?" + urlencode(params))


@admin_required
def student_list(request):
    """Legacy URL: the students masterlist is now the Users page filtered by role."""
    return _users_redirect(Role.STUDENT, request.GET.get("q", "").strip())


@admin_required
def student_detail(request, user_id):
    """Legacy URL: student details now live on the user detail page."""
    return redirect("admin_user_detail", user_id=user_id)


@admin_required
def operator_list(request):
    """Legacy URL: the operators list is now the Users page filtered by role."""
    return _users_redirect(Role.OPERATOR, request.GET.get("q", "").strip())


@admin_required
def shop_settings(request):
    shop = Shop.current()
    if shop is None:
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
