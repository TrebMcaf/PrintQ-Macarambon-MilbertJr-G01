from django.db import models
from django.db.models import Q


class Shop(models.Model):
    """SDD table `shops` (single-branch deployment)."""

    shop_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=150)
    location = models.CharField(max_length=255)
    contact_phone = models.CharField(max_length=20, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "shops"

    def __str__(self):
        return self.name

    @classmethod
    def current(cls):
        """The configured shop for this deployment (BR-14)."""
        return cls.objects.order_by("shop_id").first()


class PaperSize(models.TextChoices):
    SHORT = "SHORT", "Short (8.5x11)"
    A4 = "A4", "A4"
    LONG = "LONG", "Long (8.5x13)"


class ColorMode(models.TextChoices):
    MONO = "MONO", "Black & white"
    COLOR = "COLOR", "Color"


class PrintRate(models.Model):
    """SDD table `print_rates`. Never deleted once used — disable with is_active (BR-11)."""

    rate_id = models.AutoField(primary_key=True)
    paper_size = models.CharField(max_length=20, choices=PaperSize.choices)
    color_mode = models.CharField(max_length=10, choices=ColorMode.choices)
    price_per_page = models.DecimalField(max_digits=6, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "print_rates"
        ordering = ["paper_size", "color_mode"]
        constraints = [
            models.UniqueConstraint(fields=["paper_size", "color_mode"], name="print_rates_size_color_uq"),
            models.CheckConstraint(condition=Q(paper_size__in=PaperSize.values), name="print_rates_paper_size_chk"),
            models.CheckConstraint(condition=Q(color_mode__in=ColorMode.values), name="print_rates_color_mode_chk"),
            models.CheckConstraint(condition=Q(price_per_page__gte=0), name="print_rates_price_chk"),  # BR-10
        ]

    def __str__(self):
        return f"{self.get_paper_size_display()} / {self.get_color_mode_display()} – ₱{self.price_per_page}"
