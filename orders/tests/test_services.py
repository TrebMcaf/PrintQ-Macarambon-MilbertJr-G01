from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from accounts.models import Role, User
from catalog.models import ColorMode, PaperSize, PrintRate, Shop
from orders import services
from orders.models import OrderStatusLog, Status
from orders.services import ItemSpec


def make_spec(rate, pages=10, copies=2):
    return ItemSpec(rate_id=rate.pk, file_name="a.pdf", file_url="orders/x/a.pdf",
                    file_size_mb=Decimal("1.50"), page_count=pages, copies=copies)


class ServiceTestBase(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(name="Shop", location="Here")
        self.rate = PrintRate.objects.create(paper_size=PaperSize.A4, color_mode=ColorMode.MONO,
                                             price_per_page=Decimal("2.00"))
        self.color = PrintRate.objects.create(paper_size=PaperSize.A4, color_mode=ColorMode.COLOR,
                                              price_per_page=Decimal("10.00"))
        self.student = User.objects.create_user("s1@x.test", "pw-123456", full_name="S One")
        self.student2 = User.objects.create_user("s2@x.test", "pw-123456", full_name="S Two")
        self.operator = User.objects.create_user("op@x.test", "pw-123456", full_name="Op", role=Role.OPERATOR)


class PlaceOrderTests(ServiceTestBase):
    def test_snapshot_and_totals(self):
        order = services.place_order(self.student, [make_spec(self.rate), make_spec(self.color, 5, 1)])
        items = list(order.items.order_by("item_id"))
        self.assertEqual(items[0].unit_price, Decimal("2.00"))
        self.assertEqual(items[0].subtotal, Decimal("40.00"))
        self.assertEqual(items[1].subtotal, Decimal("50.00"))
        self.assertEqual(order.total_amount, Decimal("90.00"))

    def test_price_change_does_not_affect_old_order(self):  # BR-09
        order = services.place_order(self.student, [make_spec(self.rate)])
        self.rate.price_per_page = Decimal("5.00")
        self.rate.save()
        order.refresh_from_db()
        item = order.items.get()
        self.assertEqual(item.unit_price, Decimal("2.00"))
        self.assertEqual(item.subtotal, Decimal("40.00"))
        self.assertEqual(order.total_amount, Decimal("40.00"))

    def test_initial_pending_log(self):
        order = services.place_order(self.student, [make_spec(self.rate)])
        self.assertEqual(order.status, Status.PENDING)
        log = order.status_logs.get()
        self.assertIsNone(log.previous_status)
        self.assertEqual(log.new_status, Status.PENDING)
        self.assertEqual(log.changed_by, self.student)

    def test_operator_cannot_place(self):
        with self.assertRaises(PermissionDenied):
            services.place_order(self.operator, [make_spec(self.rate)])


class TransitionTests(ServiceTestBase):
    def setUp(self):
        super().setUp()
        self.order = services.place_order(self.student, [make_spec(self.rate)])

    def test_operator_advance_writes_log(self):  # BR-13
        services.transition_status(self.order.pk, Status.PRINTING, self.operator)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Status.PRINTING)
        log = OrderStatusLog.objects.filter(order=self.order).latest("log_id")
        self.assertEqual(log.previous_status, Status.PENDING)
        self.assertEqual(log.new_status, Status.PRINTING)
        self.assertEqual(log.changed_by, self.operator)

    def test_invalid_jump(self):
        with self.assertRaises(ValidationError):
            services.transition_status(self.order.pk, Status.COMPLETED, self.operator)

    def test_student_cancels_own_pending(self):
        services.cancel_order(self.order.pk, self.student)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Status.CANCELLED)

    def test_student_cannot_cancel_after_printing(self):  # BR-12
        services.transition_status(self.order.pk, Status.PRINTING, self.operator)
        with self.assertRaises(ValidationError):
            services.cancel_order(self.order.pk, self.student)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Status.PRINTING)

    def test_student_cannot_cancel_others_order(self):
        with self.assertRaises(PermissionDenied):
            services.cancel_order(self.order.pk, self.student2)

    def test_student_cannot_advance(self):
        with self.assertRaises(PermissionDenied):
            services.transition_status(self.order.pk, Status.PRINTING, self.student)
