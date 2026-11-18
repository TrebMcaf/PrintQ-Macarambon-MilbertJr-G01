"""QR codes for orders.

The code encodes the public tracking link, so any phone camera opens the tracking page, and the operator
scanner can read the tracking code out of it. It carries nothing that the Track page does not already show.
"""
import segno
from django.urls import reverse
from django.utils.safestring import mark_safe

from .models import Status

# A QR code is only useful while the order is still being worked on.
QR_STATUSES = (Status.PENDING, Status.PRINTING, Status.READY)


def track_url(request, order) -> str:
    """Absolute link to the public tracking page for this order (works behind the HTTPS proxy too)."""
    return f"{request.build_absolute_uri(reverse('orders:track'))}?code={order.tracking_code}"


def qr_svg(data: str, title: str = "") -> str:
    """Inline SVG for `data`: scalable with CSS, dark on white, 4-module quiet zone, medium error correction."""
    qr = segno.make(data, error="m")
    svg = qr.svg_inline(scale=1, border=4, dark="#111111", light="#ffffff", omitsize=True,
                        title=title or None, lineclass=None)
    # The markup comes from segno for data we built ourselves (a link with the tracking code), never from user input.
    return mark_safe(svg)
