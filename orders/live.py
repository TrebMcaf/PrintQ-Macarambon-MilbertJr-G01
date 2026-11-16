"""Live-update helpers: versions, change messages and page context (PLAN.md 3.1, amendments L6-L14)."""
from datetime import datetime, timedelta, timezone as dt_timezone

from django.db.models import Count, Max, Sum
from django.urls import reverse

from .models import Order

EPOCH = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)
MAX_US = 10**17
MAX_LISTED = 3


def scope_for(user):
    """Orders whose changes matter to this user, or None if the user gets no live updates."""
    if user.is_student:
        return Order.objects.filter(user=user)
    if user.is_operator:
        return Order.objects.all()
    return None


def version_for(qs) -> str:
    """Cheap fingerprint '<count>.<sum_pk>.<max_updated_at_us>' from one aggregate query ('0.0.0' if empty)."""
    agg = qs.aggregate(n=Count("pk"), s=Sum("pk"), m=Max("updated_at"))
    m = agg["m"]
    us = 0
    if m is not None:
        m = m.astimezone(dt_timezone.utc)
        us = (m - EPOCH) // timedelta(microseconds=1)
    return f"{agg['n']}.{agg['s'] or 0}.{us}"


def parse_since(version: str):
    """Return the aware datetime encoded in a version string, or None if malformed."""
    tail = version.rsplit(".", 1)[-1]
    try:
        us = int(tail)
    except (ValueError, OverflowError):
        return None
    if us <= 0 or us > MAX_US:
        return None
    return EPOCH + timedelta(microseconds=us)


def change_message(user, qs, since) -> str:
    """Human message describing what changed after `since` (aware datetime or None)."""
    if user.is_operator:
        if since is None:
            return "Queue updated."
        n_new = qs.filter(created_at__gt=since).count()
        if n_new:
            return f"{n_new} new order{'s' if n_new != 1 else ''}."
        return "Queue updated."

    if since is None:
        return "Your orders were updated."
    changed = qs.filter(updated_at__gt=since)
    total = changed.count()
    if not total:
        return "Your orders were updated."
    parts = [f"{o.tracking_code} is now {o.get_status_display()}."
             for o in changed.order_by("-updated_at")[:MAX_LISTED]]
    if total > MAX_LISTED:
        parts.append(f"and {total - MAX_LISTED} more updated.")
    return " ".join(parts)


def live_context(request, mode: str) -> dict | None:
    """Template context for the live poller, or None when this user gets no live updates."""
    scope = scope_for(request.user)
    if scope is None:
        return None
    return {"url": reverse("orders:live"), "version": version_for(scope), "mode": mode}
