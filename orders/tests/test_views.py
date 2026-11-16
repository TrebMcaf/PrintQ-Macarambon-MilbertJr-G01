from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from catalog.models import ColorMode, PaperSize, PrintRate, Shop
from orders import services
from orders.models import Status
from orders.services import ItemSpec


class ViewTests(TestCase):
    def setUp(self):
        Shop.objects.create(name="Shop", location="Here")
        self.rate = PrintRate.objects.create(paper_size=PaperSize.A4, color_mode=ColorMode.MONO,
                                             price_per_page=Decimal("2.00"))
        self.student = User.objects.create_user("s1@x.test", "pw-123456", full_name="S One")
        self.other = User.objects.create_user("s2@x.test", "pw-123456", full_name="S Two")
        self.operator = User.objects.create_user("op@x.test", "pw-123456", full_name="Op", role=Role.OPERATOR)
        self.order = services.place_order(self.student, [ItemSpec(
            rate_id=self.rate.pk, file_name="a.pdf", file_url="p/a.pdf",
            file_size_mb=Decimal("1.00"), page_count=3, copies=1)])

    def test_student_forbidden_on_queue(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse("orders:queue")).status_code, 403)

    def test_operator_queue_ok(self):
        self.client.force_login(self.operator)
        resp = self.client.get(reverse("orders:queue"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.order.tracking_code)

    def test_anonymous_track(self):
        self.assertEqual(self.client.get(reverse("orders:track")).status_code, 200)
        resp = self.client.get(reverse("orders:track"), {"code": self.order.tracking_code})
        self.assertContains(resp, "Pending")
        self.assertContains(self.client.get(reverse("orders:track"), {"code": "PQ-0000-0000"}), "not found")

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(reverse("orders:my_orders"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("accounts:login"), resp.url)

    def test_home_redirects_by_role(self):
        self.client.force_login(self.operator)
        self.assertRedirects(self.client.get(reverse("home")), reverse("operator_dashboard"))
        self.client.force_login(self.student)
        self.assertRedirects(self.client.get(reverse("home")), reverse("student_dashboard"))

    def test_student_cannot_view_others_order(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("orders:order_detail", args=[self.order.pk])).status_code, 404)

    def test_student_cancels_via_view(self):
        self.client.force_login(self.student)
        self.client.post(reverse("orders:cancel", args=[self.order.pk]))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Status.CANCELLED)

    def test_operator_advance_via_view(self):
        self.client.force_login(self.operator)
        self.client.post(reverse("orders:advance", args=[self.order.pk]), {"status": "PRINTING"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Status.PRINTING)
        resp = self.client.post(reverse("orders:advance", args=[self.order.pk]), {"status": "COMPLETED"}, follow=True)
        self.assertContains(resp, "Cannot move order")

    def test_signup_creates_student_and_logs_in(self):
        resp = self.client.post(reverse("accounts:signup"), {
            "email": "new@x.test", "full_name": "New", "password1": "Str0ng-pass-99",
            "password2": "Str0ng-pass-99", "role": "OPERATOR"})
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(email="new@x.test")
        self.assertEqual(user.role, Role.STUDENT)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
