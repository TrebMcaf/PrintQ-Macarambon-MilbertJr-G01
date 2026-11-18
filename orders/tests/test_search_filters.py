from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import Role, User
from catalog.models import ColorMode, PaperSize, PrintRate, Shop
from orders import services
from orders.models import Order, Status
from orders.search import MAX_QUERY_LENGTH, clean_query, clean_status, search_orders
from orders.services import ItemSpec


def spec(rate, name="a.pdf"):
    return ItemSpec(rate_id=rate.pk, file_name=name, file_url=f"p/{name}",
                    file_size_mb=Decimal("1.00"), page_count=2, copies=1)


class CleanerTests(SimpleTestCase):
    def test_clean_query_trims_and_caps_length(self):
        self.assertEqual(clean_query("  abc  "), "abc")
        self.assertEqual(clean_query(None), "")
        self.assertEqual(len(clean_query("x" * 500)), MAX_QUERY_LENGTH)

    def test_clean_status_accepts_only_real_statuses(self):
        self.assertEqual(clean_status("pending"), Status.PENDING)
        self.assertEqual(clean_status("READY"), Status.READY)
        self.assertEqual(clean_status("bogus"), "")
        self.assertEqual(clean_status(None), "")


class SearchTestBase(TestCase):
    def setUp(self):
        Shop.objects.create(name="Shop", location="Here")
        self.rate = PrintRate.objects.create(paper_size=PaperSize.A4, color_mode=ColorMode.MONO,
                                             price_per_page=Decimal("2.00"))
        self.ana = User.objects.create_user("ana@x.test", "pw-123456", full_name="Ana Cruz")
        self.ben = User.objects.create_user("ben@x.test", "pw-123456", full_name="Ben Reyes")
        self.operator = User.objects.create_user("op@x.test", "pw-123456", full_name="Op", role=Role.OPERATOR)
        # Ana: one pending order with two files, one printing order.
        self.ana_pending = services.place_order(self.ana, [spec(self.rate, "thesis.pdf"), spec(self.rate, "thesis-appendix.pdf")])
        self.ana_printing = services.place_order(self.ana, [spec(self.rate, "poster.pdf")])
        services.transition_status(self.ana_printing.pk, Status.PRINTING, self.operator)
        # Ben: one pending order.
        self.ben_pending = services.place_order(self.ben, [spec(self.rate, "report.pdf")])


class SearchOrdersTests(SearchTestBase):
    def test_no_filters_returns_everything(self):
        self.assertEqual(search_orders(Order.objects.all()).count(), 3)

    def test_tracking_code_match_is_case_insensitive_and_partial(self):
        code = self.ben_pending.tracking_code
        found = search_orders(Order.objects.all(), code.lower()[3:])
        self.assertEqual(list(found), [self.ben_pending])

    def test_file_name_match_does_not_duplicate_multi_item_orders(self):
        found = search_orders(Order.objects.all(), "thesis")
        self.assertEqual(list(found), [self.ana_pending])

    def test_student_name_only_searched_when_by_student(self):
        self.assertEqual(search_orders(Order.objects.all(), "Ben Reyes").count(), 0)
        found = search_orders(Order.objects.all(), "Ben Reyes", by_student=True)
        self.assertEqual(list(found), [self.ben_pending])

    def test_status_filter(self):
        found = search_orders(Order.objects.all(), status=Status.PRINTING)
        self.assertEqual(list(found), [self.ana_printing])

    def test_unknown_status_is_ignored(self):
        self.assertEqual(search_orders(Order.objects.all(), status="bogus").count(), 3)

    def test_text_and_status_combine(self):
        self.assertEqual(search_orders(Order.objects.all(), "poster", Status.PENDING).count(), 0)
        self.assertEqual(search_orders(Order.objects.all(), "poster", Status.PRINTING).count(), 1)


class MyOrdersViewTests(SearchTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.ana)
        self.url = reverse("orders:my_orders")

    def test_search_by_file_name_shows_only_matching_orders(self):
        resp = self.client.get(self.url, {"q": "poster"})
        self.assertContains(resp, self.ana_printing.tracking_code)
        self.assertNotContains(resp, self.ana_pending.tracking_code)

    def test_status_filter_limits_the_list(self):
        resp = self.client.get(self.url, {"status": "PENDING"})
        self.assertContains(resp, self.ana_pending.tracking_code)
        self.assertNotContains(resp, self.ana_printing.tracking_code)

    def test_never_shows_other_students_orders(self):
        resp = self.client.get(self.url, {"q": self.ben_pending.tracking_code})
        self.assertNotContains(resp, self.ben_pending.tracking_code + "</a>")
        self.assertContains(resp, "No orders match")

    def test_no_match_message_and_clear_link(self):
        resp = self.client.get(self.url, {"q": "nothing-like-this"})
        self.assertContains(resp, "No orders match")
        self.assertContains(resp, "Clear search")

    def test_invalid_status_is_ignored(self):
        resp = self.client.get(self.url, {"status": "bogus"})
        self.assertContains(resp, self.ana_pending.tracking_code)
        self.assertContains(resp, self.ana_printing.tracking_code)

    def test_form_keeps_the_search_text(self):
        resp = self.client.get(self.url, {"q": "poster", "status": "PRINTING"})
        self.assertContains(resp, 'value="poster"')
        self.assertContains(resp, '<option value="PRINTING" selected>')

    def test_unfiltered_page_without_orders_keeps_first_order_message(self):
        self.client.force_login(User.objects.create_user("new@x.test", "pw-123456", full_name="New"))
        self.assertContains(self.client.get(self.url), "No orders yet")


class OperatorSearchTests(SearchTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.operator)

    def test_queue_search_by_student_name(self):
        resp = self.client.get(reverse("orders:queue"), {"q": "Ben"})
        self.assertContains(resp, self.ben_pending.tracking_code)
        self.assertNotContains(resp, self.ana_pending.tracking_code)

    def test_operator_home_search_by_tracking_code(self):
        resp = self.client.get(reverse("operator_dashboard"), {"q": self.ana_printing.tracking_code})
        self.assertContains(resp, self.ana_printing.tracking_code)
        self.assertNotContains(resp, self.ben_pending.tracking_code)

    def test_operator_search_by_file_name(self):
        resp = self.client.get(reverse("orders:queue"), {"q": "report"})
        self.assertContains(resp, self.ben_pending.tracking_code)
        self.assertNotContains(resp, self.ana_printing.tracking_code)

    def test_no_query_shows_the_whole_board(self):
        resp = self.client.get(reverse("orders:queue"))
        for order in (self.ana_pending, self.ana_printing, self.ben_pending):
            self.assertContains(resp, order.tracking_code)

    def test_student_still_forbidden_on_queue(self):
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse("orders:queue"), {"q": "x"}).status_code, 403)
