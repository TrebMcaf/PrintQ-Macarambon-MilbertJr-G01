from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import Role, User
from catalog.models import ColorMode, PaperSize, PrintRate, Shop
from orders import services
from orders.scan import MAX_SCAN_LENGTH, parse_code
from orders.services import ItemSpec


class ParseCodeTests(SimpleTestCase):
    def test_bare_code(self):
        self.assertEqual(parse_code("PQ-2026-0042"), "PQ-2026-0042")

    def test_lowercase_and_whitespace_are_fixed(self):
        self.assertEqual(parse_code("  pq-2026-0042 \n"), "PQ-2026-0042")

    def test_code_is_found_inside_a_tracking_link(self):
        self.assertEqual(parse_code("https://shop.example/orders/track/?code=PQ-2026-0042"), "PQ-2026-0042")

    def test_first_code_wins(self):
        self.assertEqual(parse_code("PQ-2026-0001 then PQ-2026-0002"), "PQ-2026-0001")

    def test_rejects_things_that_are_not_codes(self):
        for text in ("", None, "hello", "PQ-2026-12", "PQ-26-0001", "PQ-2026-00421", "XPQ-2026-0042", "PQ2026-0042"):
            with self.subTest(text=text):
                self.assertIsNone(parse_code(text))

    def test_only_the_start_of_huge_text_is_read(self):
        self.assertIsNone(parse_code("x " * MAX_SCAN_LENGTH + "PQ-2026-0042"))


class ScanViewTests(TestCase):
    def setUp(self):
        Shop.objects.create(name="Shop", location="Here")
        rate = PrintRate.objects.create(paper_size=PaperSize.A4, color_mode=ColorMode.MONO,
                                        price_per_page=Decimal("2.00"))
        self.student = User.objects.create_user("s1@x.test", "pw-123456", full_name="S One")
        self.operator = User.objects.create_user("op@x.test", "pw-123456", full_name="Op", role=Role.OPERATOR)
        self.order = services.place_order(self.student, [ItemSpec(
            rate_id=rate.pk, file_name="a.pdf", file_url="p/a.pdf",
            file_size_mb=Decimal("1.00"), page_count=3, copies=1)])
        self.scan_url = reverse("orders:scan")
        self.lookup_url = reverse("orders:scan_lookup")
        self.detail_url = reverse("orders:order_detail", args=[self.order.pk])

    # --- the scan page
    def test_operator_sees_the_scan_page_with_all_three_ways_in(self):
        self.client.force_login(self.operator)
        resp = self.client.get(self.scan_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Start camera")
        self.assertContains(resp, "Scan from a photo")
        self.assertContains(resp, 'id="scan-code"')
        self.assertContains(resp, "vendor/jsqr/jsQR.js")
        self.assertContains(resp, "js/scan.js")

    def test_student_forbidden_and_anonymous_sent_to_login(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(self.scan_url).status_code, 403)
        self.client.logout()
        resp = self.client.get(self.scan_url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("accounts:login"), resp.url)

    # --- the lookup
    def test_valid_code_opens_the_order(self):
        self.client.force_login(self.operator)
        resp = self.client.get(self.lookup_url, {"code": self.order.tracking_code})
        self.assertRedirects(resp, self.detail_url)

    def test_tracking_link_and_lowercase_also_work(self):
        self.client.force_login(self.operator)
        link = f"https://shop.example/orders/track/?code={self.order.tracking_code}"
        self.assertRedirects(self.client.get(self.lookup_url, {"code": link}), self.detail_url)
        self.assertRedirects(self.client.get(self.lookup_url, {"code": self.order.tracking_code.lower()}), self.detail_url)

    def test_unknown_code_goes_back_to_scan_with_a_message(self):
        self.client.force_login(self.operator)
        resp = self.client.get(self.lookup_url, {"code": "PQ-2026-9999"}, follow=True)
        self.assertRedirects(resp, self.scan_url)
        self.assertContains(resp, "No order found for PQ-2026-9999")

    def test_junk_or_missing_code_goes_back_to_scan_with_a_message(self):
        self.client.force_login(self.operator)
        for params in ({"code": "hello"}, {"code": ""}, {}):
            with self.subTest(params=params):
                resp = self.client.get(self.lookup_url, params, follow=True)
                self.assertRedirects(resp, self.scan_url)
                self.assertContains(resp, "not a PrintQ tracking code")

    def test_student_cannot_use_the_lookup_to_find_orders(self):
        self.client.force_login(self.student)
        resp = self.client.get(self.lookup_url, {"code": self.order.tracking_code})
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_lookup_sent_to_login(self):
        resp = self.client.get(self.lookup_url, {"code": self.order.tracking_code})
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("accounts:login"), resp.url)

    def test_lookup_is_get_only(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.post(self.lookup_url, {"code": self.order.tracking_code}).status_code, 405)

    # --- where operators find it
    def test_scan_link_is_in_the_operator_nav_and_queue_pages(self):
        self.client.force_login(self.operator)
        for url in (reverse("operator_dashboard"), reverse("orders:queue")):
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertContains(resp, f'href="{self.scan_url}"')
                self.assertContains(resp, "Scan QR")

    def test_students_never_see_a_scan_link(self):
        self.client.force_login(self.student)
        self.assertNotContains(self.client.get(reverse("orders:my_orders")), self.scan_url)

    def test_track_page_offers_open_order_to_operators_only(self):
        params = {"code": self.order.tracking_code}
        track = reverse("orders:track")
        self.assertNotContains(self.client.get(track, params), "Open order")
        self.client.force_login(self.student)
        self.assertNotContains(self.client.get(track, params), "Open order")
        self.client.force_login(self.operator)
        resp = self.client.get(track, params)
        self.assertContains(resp, "Open order")
        self.assertContains(resp, f'href="{self.detail_url}"')
