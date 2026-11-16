from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from catalog.models import ColorMode, PaperSize, PrintRate, Shop
from orders import services
from orders.live import parse_since, scope_for, version_for
from orders.models import Order, Status
from orders.services import ItemSpec

# Every order is pinned to this fixed past time in setUp, so version changes never depend on the clock's resolution.
PAST = datetime(2026, 1, 1, 12, 0, tzinfo=dt_timezone.utc)


class LiveTests(TestCase):
    def setUp(self):
        Shop.objects.create(name="Shop", location="Here")
        self.rate = PrintRate.objects.create(paper_size=PaperSize.A4, color_mode=ColorMode.MONO,
                                             price_per_page=Decimal("2.00"))
        self.student = User.objects.create_user("s1@x.test", "pw-123456", full_name="S One")
        self.other = User.objects.create_user("s2@x.test", "pw-123456", full_name="S Two")
        self.operator = User.objects.create_user("op@x.test", "pw-123456", full_name="Op", role=Role.OPERATOR)
        self.admin = User.objects.create_user("ad@x.test", "pw-123456", full_name="Ad", role=Role.ADMIN)
        self.order = self._place(self.student)
        self.their_order = self._place(self.other)
        Order.objects.update(created_at=PAST, updated_at=PAST)
        self.live_url = reverse("orders:live")

    def _place(self, user):
        return services.place_order(user, [ItemSpec(
            rate_id=self.rate.pk, file_name="a.pdf", file_url="p/a.pdf",
            file_size_mb=Decimal("1.00"), page_count=3, copies=1)])

    def _version(self, user):
        return version_for(scope_for(user))

    # 1-2. Endpoint basics
    def test_current_version_returns_204_with_no_store(self):
        self.client.force_login(self.student)
        current = self._version(self.student)
        # Queries on the 204 path: 1 session load, 2 user load, 3 the single version aggregate.
        with self.assertNumQueries(3):
            resp = self.client.get(self.live_url, {"v": current})
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(resp.content, b"")
        self.assertEqual(resp["Cache-Control"], "no-store")

    def test_stale_or_empty_version_returns_json(self):
        self.client.force_login(self.student)
        current = self._version(self.student)
        for params in ({}, {"v": "0.0.0"}):
            with self.subTest(params=params):
                resp = self.client.get(self.live_url, params)
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp["Cache-Control"], "no-store")
                data = resp.json()
                self.assertEqual(data["version"], current)
                self.assertIn("message", data)

    # 3. Transitions change the student's version and the message names the order
    def test_operator_transition_changes_student_version_and_message(self):
        self.client.force_login(self.student)
        old = self._version(self.student)
        services.transition_status(self.order.pk, Status.PRINTING, self.operator)
        self.assertNotEqual(self._version(self.student), old)
        resp = self.client.get(self.live_url, {"v": old})
        self.assertEqual(resp.status_code, 200)
        message = resp.json()["message"]
        self.assertIn(self.order.tracking_code, message)
        self.assertIn("Printing", message)

    def test_transition_bumps_updated_at(self):
        before = Order.objects.get(pk=self.order.pk).updated_at
        services.transition_status(self.order.pk, Status.PRINTING, self.operator)
        after = Order.objects.get(pk=self.order.pk).updated_at
        self.assertGreater(after, before)

    # 4. Isolation
    def test_other_students_change_does_not_change_version(self):
        self.client.force_login(self.student)
        old = self._version(self.student)
        services.transition_status(self.their_order.pk, Status.PRINTING, self.operator)
        self.assertEqual(self._version(self.student), old)
        resp = self.client.get(self.live_url, {"v": old})
        self.assertEqual(resp.status_code, 204)

    # 5. Operator notifications
    def test_operator_one_new_order(self):
        self.client.force_login(self.operator)
        old = self._version(self.operator)
        self._place(self.student)
        resp = self.client.get(self.live_url, {"v": old})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertNotEqual(data["version"], old)
        self.assertEqual(data["message"], "1 new order.")

    def test_operator_two_new_orders(self):
        self.client.force_login(self.operator)
        old = self._version(self.operator)
        self._place(self.student)
        self._place(self.other)
        resp = self.client.get(self.live_url, {"v": old})
        self.assertEqual(resp.json()["message"], "2 new orders.")

    # 6. Access control
    def test_admin_forbidden(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.live_url).status_code, 403)

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(self.live_url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("accounts:login"), resp.url)

    # 7. Malformed versions never crash
    def test_malformed_version_returns_generic_message(self):
        self.client.force_login(self.student)
        for bad in ("abc", "1.2.notanumber", "1.2.-5"):
            with self.subTest(v=bad):
                resp = self.client.get(self.live_url, {"v": bad})
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json()["message"], "Your orders were updated.")

    def test_malformed_version_for_operator_returns_generic_message(self):
        self.client.force_login(self.operator)
        resp = self.client.get(self.live_url, {"v": "abc"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["message"], "Queue updated.")

    # 8. Page configuration
    def test_student_pages_have_auto_live_config(self):
        self.client.force_login(self.student)
        urls = (reverse("student_dashboard"), reverse("orders:my_orders"),
                reverse("orders:order_detail", args=[self.order.pk]))
        for url in urls:
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.context["live"]["mode"], "auto")
                self.assertEqual(resp.context["live"]["url"], self.live_url)
                self.assertContains(resp, 'id="live-config"')
                self.assertContains(resp, 'id="live-region"')

    def test_operator_pages_have_notify_live_config(self):
        self.client.force_login(self.operator)
        for url in (reverse("operator_dashboard"), reverse("orders:queue")):
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.context["live"]["mode"], "notify")
                self.assertContains(resp, 'id="live-config"')
                self.assertContains(resp, 'id="live-region"')
                self.assertContains(resp, '"mode": "notify"')

    def test_pages_without_live_updates_have_no_live_config(self):
        self.client.force_login(self.operator)
        resp = self.client.get(reverse("orders:order_detail", args=[self.order.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["live"])
        self.assertNotContains(resp, 'id="live-config"')

        self.client.force_login(self.admin)
        resp = self.client.get(reverse("admin_dashboard"))
        self.assertNotContains(resp, 'id="live-config"')

        self.client.force_login(self.student)
        resp = self.client.get(reverse("orders:new_order"))
        self.assertNotContains(resp, 'id="live-config"')

        resp = self.client.get(reverse("orders:track"))
        self.assertNotContains(resp, 'id="live-config"')

    # 9. Fingerprint details
    def test_version_for_empty_queryset(self):
        version = version_for(Order.objects.none())
        self.assertTrue(version.startswith("0."))
        self.assertEqual(version, "0.0.0")

    def test_version_round_trips_max_updated_at(self):
        version = self._version(self.student)
        self.assertEqual(parse_since(version), PAST)
