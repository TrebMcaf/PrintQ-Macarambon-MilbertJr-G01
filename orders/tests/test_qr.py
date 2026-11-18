from decimal import Decimal

from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import Role, User
from catalog.models import ColorMode, PaperSize, PrintRate, Shop
from orders import services
from orders.models import Status
from orders.qr import QR_STATUSES, qr_svg, track_url
from orders.services import ItemSpec


class QrSvgTests(SimpleTestCase):
    def test_returns_inline_svg_with_title(self):
        svg = qr_svg("https://example.test/orders/track/?code=PQ-2026-0001", title="QR code for order PQ-2026-0001")
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("<title>QR code for order PQ-2026-0001</title>", svg)

    def test_is_scalable_and_has_no_script(self):
        svg = qr_svg("PQ-2026-0001")
        self.assertIn("viewBox=", svg)
        self.assertNotIn("width=", svg)
        self.assertNotIn("<script", svg.lower())

    def test_same_data_gives_same_svg_and_other_data_differs(self):
        self.assertEqual(qr_svg("PQ-2026-0001"), qr_svg("PQ-2026-0001"))
        self.assertNotEqual(qr_svg("PQ-2026-0001"), qr_svg("PQ-2026-0002"))


class QrViewTests(TestCase):
    def setUp(self):
        Shop.objects.create(name="Shop", location="Here")
        rate = PrintRate.objects.create(paper_size=PaperSize.A4, color_mode=ColorMode.MONO,
                                        price_per_page=Decimal("2.00"))
        self.student = User.objects.create_user("s1@x.test", "pw-123456", full_name="S One")
        self.other = User.objects.create_user("s2@x.test", "pw-123456", full_name="S Two")
        self.operator = User.objects.create_user("op@x.test", "pw-123456", full_name="Op", role=Role.OPERATOR)
        self.order = services.place_order(self.student, [ItemSpec(
            rate_id=rate.pk, file_name="a.pdf", file_url="p/a.pdf",
            file_size_mb=Decimal("1.00"), page_count=3, copies=1)])
        self.url = reverse("orders:order_detail", args=[self.order.pk])

    def test_track_url_is_absolute_and_carries_the_code(self):
        request = RequestFactory().get("/")
        self.assertEqual(track_url(request, self.order),
                         f"http://testserver{reverse('orders:track')}?code={self.order.tracking_code}")

    def test_owner_sees_qr_card_and_enlarge_modal_while_active(self):
        self.client.force_login(self.student)
        for status in (Status.PENDING, Status.PRINTING, Status.READY):
            self.order.status = status
            self.order.save(update_fields=["status"])
            resp = self.client.get(self.url)
            self.assertContains(resp, "Show this at the counter")
            self.assertContains(resp, 'id="qr-modal"')
            self.assertContains(resp, "<svg")

    def test_no_qr_once_completed_or_cancelled(self):
        self.client.force_login(self.student)
        for status in (Status.COMPLETED, Status.CANCELLED):
            self.order.status = status
            self.order.save(update_fields=["status"])
            resp = self.client.get(self.url)
            self.assertNotContains(resp, "Show this at the counter")
            self.assertNotContains(resp, 'id="qr-modal"')

    def test_other_student_cannot_see_the_order_or_qr(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_operator_does_not_get_the_customer_qr(self):
        self.client.force_login(self.operator)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Show this at the counter")

    def test_qr_statuses_are_the_active_ones(self):
        self.assertEqual(set(QR_STATUSES), {Status.PENDING, Status.PRINTING, Status.READY})
