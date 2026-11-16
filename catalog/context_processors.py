from django.utils.functional import SimpleLazyObject

from .models import Shop


def shop(request):
    """Expose the configured shop to every template (header, footer, login intro).

    Lazy, so pages that never mention the shop don't run the query.
    """
    return {"shop": SimpleLazyObject(Shop.current)}
