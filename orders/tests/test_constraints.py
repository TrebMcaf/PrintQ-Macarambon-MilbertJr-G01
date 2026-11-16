from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import RestrictedError
from django.test import TestCase

from accounts.models import User
from catalog.models import ColorMode, PaperSize, PrintRate, Shop
from orders import services
from orders.models import OrderItem
from orders.services import ItemSpec


class ConstraintTests(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(name="Shop", location="Here")
        self.rate = PrintRate.objects.create(paper_size=PaperSize.A4, color_mode=ColorMode.MONO,
                                             price_per_page=Decimal("2.00"))
        self.student = User.objects.create_user("s1@x.test", "pw-123456", full_name="S One")
        self.order = services.place_order(self.student, [ItemSpec(
            rate_id=self.rate.pk, file_name="a.pdf", file_url="p/a.pdf",
            file_size_mb=Decimal("1.00"), page_count=3, copies=1)])

    def _item(self, **over):
        data = dict(order=self.order, rate=self.rate, file_name="b.pdf", file_url="p/b.pdf",
                    file_size_mb=Decimal("1.00"), page_count=1, copies=1,
                    unit_price=Decimal("2.00"), subtotal=Decimal("2.00"))
        data.update(over)
        return OrderItem(**data)

    def assertRejected(self, obj):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                obj.save()

    def test_valid_item_saves(self):
        self._item().save()

    def test_copies_zero(self):  # BR-07
        self.assertRejected(self._item(copies=0))

    def test_page_count_zero(self):  # BR-08
        self.assertRejected(self._item(page_count=0))

    def test_file_size_over_limit(self):  # BR-06
        self.assertRejected(self._item(file_size_mb=Decimal("26.00")))

    def test_negative_total(self):  # BR-05
        self.order.total_amount = Decimal("-1.00")
        self.assertRejected(self.order)

    def test_bad_role(self):  # BR-02
        self.assertRejected(User(email="r@x.test", full_name="R", role="BOSS", password="x"))

    def test_duplicate_email(self):  # BR-01
        self.assertRejected(User(email="s1@x.test", full_name="Dup", password="x"))

    def test_bad_status(self):  # BR-04
        self.order.status = "BOGUS"
        self.assertRejected(self.order)

    def test_delete_referenced_rate_restricted(self):  # BR-11
        with self.assertRaises(RestrictedError):
            self.rate.delete()
