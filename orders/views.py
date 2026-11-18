import os
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, SuspiciousFileOperation, ValidationError
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import get_valid_filename
from django.views.decorators.http import require_GET, require_POST

from accounts.decorators import operator_required, student_required
from catalog.models import PrintRate, Shop

from . import services, storage
from .dashboards import queue_groups
from .forms import DocumentFormSet, NotesForm
from .live import change_message, live_context, parse_since, scope_for, version_for
from .models import BindingType, Order, OrderItem, Status
from .qr import QR_STATUSES, qr_svg, track_url
from .search import clean_query, clean_status, search_orders
from .services import ItemSpec

DRAFT_KEY = "printq_draft"


def _error_text(exc):
    return " ".join(exc.messages) if isinstance(exc, ValidationError) else (str(exc) or "Not allowed.")


def _safe_file_name(raw: str) -> str:
    try:
        name = get_valid_filename(os.path.basename(raw))
    except SuspiciousFileOperation:
        name = ""
    if not name:
        return "document.pdf"
    stem, ext = os.path.splitext(name)
    ext = ext or ".pdf"
    return stem[: 255 - len(ext)] + ext


def _draft_specs(draft) -> list[ItemSpec]:
    return [ItemSpec(rate_id=i["rate_id"], file_name=i["file_name"], file_url=i["file_url"],
                     file_size_mb=Decimal(i["file_size_mb"]), page_count=i["page_count"],
                     copies=i["copies"], binding_type=i["binding_type"]) for i in draft["items"]]


def _draft_paths(draft) -> list[str]:
    return [i["file_url"] for i in draft["items"]]


@student_required
def my_orders(request):
    q = clean_query(request.GET.get("q"))
    status = clean_status(request.GET.get("status"))
    orders = search_orders(Order.objects.filter(user=request.user), q, status).order_by("-created_at")
    return render(request, "orders/my_orders.html", {
        "orders": orders, "q": q, "status": status, "statuses": Status.choices,
        "filtered": bool(q or status), "live": live_context(request, "auto"),
    })


@login_required
@require_GET
def live(request):
    """Cheap change check: 204 when the page's version is current, else JSON with the new version and a message."""
    scope = scope_for(request.user)
    if scope is None:
        raise PermissionDenied
    current = version_for(scope)
    seen = request.GET.get("v", "")
    if seen == current:
        response = HttpResponse(status=204)
    else:
        response = JsonResponse({
            "version": current,
            "message": change_message(request.user, scope, parse_since(seen)),
        })
    response["Cache-Control"] = "no-store"
    return response


@student_required
def new_order(request):
    if DRAFT_KEY in request.session:
        return redirect("orders:confirm_order")
    shop = Shop.current()
    if shop is not None and not shop.is_active:
        return render(request, "orders/new_order.html", {"paused": True, "rates": PrintRate.objects.filter(is_active=True)})
    if request.method == "POST":
        formset = DocumentFormSet(request.POST, request.FILES, prefix="docs")
        notes_form = NotesForm(request.POST)
        if formset.is_valid() and notes_form.is_valid():
            items, uploaded = [], []
            try:
                for form in formset.filled_forms():
                    f = form.cleaned_data["file"]
                    path = storage.build_object_path(request.user.pk)
                    uploaded.append(path)
                    storage.upload_file(f, path)
                    items.append({
                        "rate_id": form.cleaned_data["rate"].pk,
                        "file_name": _safe_file_name(f.name),
                        "file_url": path,
                        "file_size_mb": str(form.size_mb),
                        "page_count": form.page_count,
                        "copies": form.cleaned_data["copies"],
                        "binding_type": form.cleaned_data["binding_type"],
                    })
            except (storage.StorageError, OSError):
                storage.delete_files(uploaded)
                messages.error(request, "Upload failed. Please try again.")
            else:
                request.session[DRAFT_KEY] = {"notes": notes_form.cleaned_data["notes"], "items": items}
                return redirect("orders:confirm_order")
    else:
        formset = DocumentFormSet(prefix="docs")
        notes_form = NotesForm()
    rates = PrintRate.objects.filter(is_active=True)
    return render(request, "orders/new_order.html", {"formset": formset, "notes_form": notes_form, "rates": rates})


@student_required
def confirm_order(request):
    draft = request.session.get(DRAFT_KEY)
    if not draft:
        return redirect("orders:new_order")
    specs = _draft_specs(draft)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "discard":
            request.session.pop(DRAFT_KEY, None)
            storage.delete_files(_draft_paths(draft))
            messages.info(request, "Draft discarded.")
            return redirect("orders:new_order")
        if action == "confirm":
            request.session.pop(DRAFT_KEY, None)
            try:
                order = services.place_order(request.user, specs, draft["notes"])
            except (ValidationError, PermissionDenied) as exc:
                storage.delete_files(_draft_paths(draft))
                messages.error(request, _error_text(exc))
                return redirect("orders:new_order")
            except Exception:
                storage.delete_files(_draft_paths(draft))
                raise
            messages.success(request, f"Order {order.tracking_code} placed.")
            return redirect("orders:order_detail", order_id=order.pk)
        return HttpResponseBadRequest("Unknown action.")
    try:
        lines, total = services.quote_items(specs)
    except ValidationError as exc:
        request.session.pop(DRAFT_KEY, None)
        storage.delete_files(_draft_paths(draft))
        messages.error(request, _error_text(exc))
        return redirect("orders:new_order")
    for line in lines:
        line.binding_label = BindingType(line.spec.binding_type).label
    return render(request, "orders/confirm_order.html", {"lines": lines, "total": total, "notes": draft["notes"]})


@operator_required
def download_item(request, item_id):
    item = get_object_or_404(OrderItem, pk=item_id)
    backend = storage.get_backend()
    try:
        url = backend.signed_url(item.file_url, download_name=item.file_name)
        if url:
            return redirect(url)
        return FileResponse(backend.open(item.file_url), as_attachment=True, filename=item.file_name,
                            content_type="application/pdf")
    except storage.StorageError:
        raise Http404


@login_required
def order_detail(request, order_id):
    order = get_object_or_404(Order.objects.select_related("user"), pk=order_id)
    if request.user.is_student and order.user_id != request.user.pk:
        raise Http404
    is_owner_student = request.user.is_student and order.user_id == request.user.pk
    qr = None
    if is_owner_student and order.status in QR_STATUSES:
        qr = qr_svg(track_url(request, order), title=f"QR code for order {order.tracking_code}")
    return render(request, "orders/order_detail.html", {
        "order": order,
        "qr_svg": qr,
        "items": order.items.select_related("rate"),
        "logs": order.status_logs.select_related("changed_by"),
        "can_cancel": order.can_cancel and is_owner_student,
        "can_edit": order.status == Status.PENDING and is_owner_student,
        "live": live_context(request, "auto") if is_owner_student else None,
    })


@student_required
def edit_notes(request, order_id):
    order = get_object_or_404(Order, pk=order_id, user=request.user)
    if order.status != Status.PENDING:
        messages.error(request, "Notes can only be changed while the order is pending.")
        return redirect("orders:order_detail", order_id=order.pk)
    if request.method == "POST":
        form = NotesForm(request.POST)
        if form.is_valid():
            try:
                services.update_notes(order.pk, request.user, form.cleaned_data["notes"])
            except (ValidationError, PermissionDenied) as exc:
                messages.error(request, _error_text(exc))
            else:
                messages.success(request, f"Notes updated for {order.tracking_code}.")
                return redirect("orders:order_detail", order_id=order.pk)
    else:
        form = NotesForm(initial={"notes": order.notes or ""})
    return render(request, "orders/edit_notes.html", {"order": order, "form": form})


@require_POST
@student_required
def cancel(request, order_id):
    order = get_object_or_404(Order, pk=order_id, user=request.user)
    try:
        services.cancel_order(order.pk, request.user)
        messages.success(request, f"Order {order.tracking_code} cancelled.")
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _error_text(exc))
    return redirect("orders:order_detail", order_id=order.pk)


@operator_required
def queue(request):
    q = clean_query(request.GET.get("q"))
    return render(request, "orders/queue.html", {
        "groups": queue_groups(q), "q": q, "live": live_context(request, "notify"),
    })


@require_POST
@operator_required
def advance(request, order_id):
    new_status = request.POST.get("status", "")
    try:
        if new_status not in Status.values:
            raise ValidationError("Unknown status.")
        order = services.transition_status(order_id, new_status, request.user)
        messages.success(request, f"{order.tracking_code} is now {order.get_status_display()}.")
    except Order.DoesNotExist:
        raise Http404
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _error_text(exc))
    return redirect("home")


@require_POST
@operator_required
def shop_status(request):
    shop = Shop.current()
    if shop is None:
        messages.error(request, "No shop is set up yet.")
        return redirect("home")
    action = request.POST.get("action")
    if action == "pause":
        shop.is_active = False
        messages.success(request, "Online orders paused. Students can't place new orders until you resume.")
    elif action == "resume":
        shop.is_active = True
        messages.success(request, "Online orders resumed.")
    else:
        return HttpResponseBadRequest("Unknown action.")
    shop.save(update_fields=["is_active"])
    nxt = request.POST.get("next", "")
    safe = bool(nxt) and url_has_allowed_host_and_scheme(
        nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    return redirect(nxt if safe else "home")


def track(request):
    code = request.GET.get("code", "").strip().upper()
    order = None
    if code:
        order = Order.objects.filter(tracking_code=code).first()
    return render(request, "orders/track.html", {"code": code, "order": order, "searched": bool(code)})
