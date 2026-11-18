"""Order business logic. Views call these functions; they never write order rows directly."""
import secrets
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from catalog.models import PrintRate, Shop

from .models import ALLOWED_TRANSITIONS, BindingType, Order, OrderItem, OrderStatusLog, Status

MAX_FILE_MB = Decimal("25.00")


@dataclass
class ItemSpec:
    rate_id: int
    file_name: str
    file_url: str
    file_size_mb: Decimal
    page_count: int
    copies: int = 1
    binding_type: str = BindingType.NONE


def generate_tracking_code() -> str:
    """PQ-YYYY-NNNN (12 chars, BR-03). Retries until unused."""
    year = timezone.localdate().year
    for _ in range(20):
        code = f"PQ-{year}-{secrets.randbelow(10000):04d}"
        if not Order.objects.filter(tracking_code=code).exists():
            return code
    raise RuntimeError("Could not generate a unique tracking code")


def line_subtotal(unit_price: Decimal, page_count: int, copies: int) -> Decimal:
    return (unit_price * page_count * copies).quantize(Decimal("0.01"))


@dataclass
class QuoteLine:
    spec: ItemSpec
    rate: PrintRate
    unit_price: Decimal
    subtotal: Decimal


def quote_items(items: list[ItemSpec]) -> tuple[list[QuoteLine], Decimal]:
    """Price a draft order without writing anything (F-03 review step)."""
    rates = PrintRate.objects.in_bulk([i.rate_id for i in items])
    lines = []
    total = Decimal("0.00")
    for spec in items:
        rate = rates.get(spec.rate_id)
        if rate is None or not rate.is_active:
            raise ValidationError("Selected print option is no longer available.")
        subtotal = line_subtotal(rate.price_per_page, spec.page_count, spec.copies)
        lines.append(QuoteLine(spec=spec, rate=rate, unit_price=rate.price_per_page, subtotal=subtotal))
        total += subtotal
    return lines, total


@transaction.atomic
def place_order(user, items: list[ItemSpec], notes: str = "") -> Order:
    """Create an order with price snapshots (BR-09) and an initial audit log (BR-13)."""
    if not user.is_student:
        raise PermissionDenied("Only students can place orders.")
    if not items:
        raise ValidationError("An order needs at least one document.")

    shop = Shop.current()
    if shop is None or not shop.is_active:
        raise ValidationError("The print shop is not accepting orders right now.")

    rates = PrintRate.objects.in_bulk([i.rate_id for i in items])
    order = Order.objects.create(
        shop=shop, user=user, tracking_code=generate_tracking_code(), notes=notes or None
    )

    total = Decimal("0.00")
    for spec in items:
        rate = rates.get(spec.rate_id)
        if rate is None or not rate.is_active:
            raise ValidationError("Selected print option is no longer available.")
        if not (Decimal("0") < Decimal(spec.file_size_mb) <= MAX_FILE_MB):  # BR-06
            raise ValidationError(f"{spec.file_name} exceeds the 25 MB limit.")
        if spec.page_count < 1 or spec.copies < 1:  # BR-07, BR-08
            raise ValidationError("Page count and copies must be at least 1.")

        subtotal = line_subtotal(rate.price_per_page, spec.page_count, spec.copies)
        OrderItem.objects.create(
            order=order,
            rate=rate,
            file_name=spec.file_name,
            file_url=spec.file_url,
            file_size_mb=spec.file_size_mb,
            page_count=spec.page_count,
            copies=spec.copies,
            binding_type=spec.binding_type,
            unit_price=rate.price_per_page,  # locked snapshot
            subtotal=subtotal,
        )
        total += subtotal

    order.total_amount = total
    order.save(update_fields=["total_amount", "updated_at"])
    OrderStatusLog.objects.create(order=order, changed_by=user, previous_status=None, new_status=Status.PENDING)
    return order


@transaction.atomic
def transition_status(order_id: int, new_status: str, user) -> Order:
    """The only way to change an order's status. Locks the row, checks the state machine, logs it."""
    order = Order.objects.select_for_update().get(pk=order_id)
    current = Status(order.status)
    new = Status(new_status)

    if new not in ALLOWED_TRANSITIONS[current]:
        raise ValidationError(f"Cannot move order from {current} to {new}.")

    if new == Status.CANCELLED:
        # BR-12: students may cancel only their own PENDING orders.
        if user.is_student and order.user_id != user.pk:
            raise PermissionDenied("You can only cancel your own orders.")
    elif not user.is_operator:
        raise PermissionDenied("Only operators can advance orders.")

    order.status = new
    order.save(update_fields=["status", "updated_at"])
    OrderStatusLog.objects.create(order=order, changed_by=user, previous_status=current, new_status=new)
    return order


def cancel_order(order_id: int, user) -> Order:
    return transition_status(order_id, Status.CANCELLED, user)


MAX_NOTES_LENGTH = 500


@transaction.atomic
def update_notes(order_id: int, user, notes: str) -> Order:
    """Let a student change the notes on their own order while it is still PENDING.

    Files, copies and the locked prices (BR-09) are not editable; only the free-text notes are.
    """
    order = Order.objects.select_for_update().get(pk=order_id)
    if not user.is_student or order.user_id != user.pk:
        raise PermissionDenied("You can only edit your own orders.")
    if order.status != Status.PENDING:
        raise ValidationError("Notes can only be changed while the order is pending.")
    notes = (notes or "").strip()
    if len(notes) > MAX_NOTES_LENGTH:
        raise ValidationError(f"Notes can be at most {MAX_NOTES_LENGTH} characters.")
    order.notes = notes or None
    order.save(update_fields=["notes", "updated_at"])
    return order
