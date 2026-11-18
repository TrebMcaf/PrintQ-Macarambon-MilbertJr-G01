"""Search and filter helpers shared by the student order list and the operator queue."""
from django.db.models import Q

from .models import Status

MAX_QUERY_LENGTH = 60


def clean_query(raw) -> str:
    """Trim the search text and cap its length so a huge query string is never sent to the database."""
    return (raw or "").strip()[:MAX_QUERY_LENGTH]


def clean_status(raw) -> str:
    """Return a valid Status value, or '' (no filter) for anything else."""
    value = (raw or "").strip().upper()
    return value if value in Status.values else ""


def search_orders(qs, q="", status="", *, by_student=False):
    """Narrow an Order queryset by a text search and/or a status.

    The text matches the tracking code and the uploaded file names. Operators pass by_student=True so
    they can also search by the student's name or email. Unknown statuses are ignored.
    """
    q = clean_query(q)
    status = clean_status(status)
    if q:
        cond = Q(tracking_code__icontains=q) | Q(items__file_name__icontains=q)
        if by_student:
            cond |= Q(user__full_name__icontains=q) | Q(user__email__icontains=q)
        qs = qs.filter(cond).distinct()
    if status:
        qs = qs.filter(status=status)
    return qs
