import os
import secrets
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Role, User
from catalog.models import ColorMode, PaperSize, PrintRate, Shop

DEFAULT_RATES = {
    (PaperSize.SHORT, ColorMode.MONO): "2.00",
    (PaperSize.SHORT, ColorMode.COLOR): "10.00",
    (PaperSize.A4, ColorMode.MONO): "2.00",
    (PaperSize.A4, ColorMode.COLOR): "10.00",
    (PaperSize.LONG, ColorMode.MONO): "3.00",
    (PaperSize.LONG, ColorMode.COLOR): "12.00",
}


class Command(BaseCommand):
    help = "Seed the shop, the print rate matrix and an operator and an admin account. Safe to re-run."

    @transaction.atomic
    def handle(self, *args, **opts):
        shop, created = Shop.objects.get_or_create(
            shop_id=1, defaults={"name": "Campus Print Shop", "location": "Main Building, Ground Floor"}
        )
        self.stdout.write(f"Shop: {shop} ({'created' if created else 'exists'})")

        for (size, mode), price in DEFAULT_RATES.items():
            PrintRate.objects.get_or_create(paper_size=size, color_mode=mode,
                                            defaults={"price_per_page": Decimal(price)})
        self.stdout.write(f"Print rates: {PrintRate.objects.count()}")

        email = os.environ.get("SEED_OPERATOR_EMAIL", "operator@printq.test")
        if not User.objects.filter(email=email).exists():
            password = os.environ.get("SEED_OPERATOR_PASSWORD") or secrets.token_urlsafe(12)
            User.objects.create_user(email=email, password=password, full_name="Shop Operator",
                                     role=Role.OPERATOR, is_staff=True)
            shown = "from SEED_OPERATOR_PASSWORD" if os.environ.get("SEED_OPERATOR_PASSWORD") else password
            self.stdout.write(self.style.SUCCESS(f"Operator created: {email} / password: {shown}"))
        else:
            self.stdout.write(f"Operator {email} exists")

        admin_email = os.environ.get("SEED_ADMIN_EMAIL", "admin@printq.test")
        if not User.objects.filter(email=admin_email).exists():
            admin_pw = os.environ.get("SEED_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
            User.objects.create_user(email=admin_email, password=admin_pw, full_name="Shop Admin",
                                     role=Role.ADMIN, is_staff=True, is_superuser=True)
            shown = "from SEED_ADMIN_PASSWORD" if os.environ.get("SEED_ADMIN_PASSWORD") else admin_pw
            self.stdout.write(self.style.SUCCESS(f"Admin created: {admin_email} / password: {shown}"))
        else:
            self.stdout.write(f"Admin {admin_email} exists")
