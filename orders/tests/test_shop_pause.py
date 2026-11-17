from decimal import Decimal

from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from catalog.models import ColorMode, PaperSize, PrintRate, Shop
from orders.models import Order
from orders.tests.utils import LocalStorageTestMixin, make_pdf


class ShopPauseBase(LocalStorageTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.shop = Shop.objects.create(name="Shop", location="Here", is_active=True)
        self.rate = PrintRate.objects.create(paper_size=PaperSize.A4, color_mode=ColorMode.MONO,
                                             price_per_page=Decimal("2.00"))
        self.operator = User.objects.create_user("pause-op@x.test", "pw-123456", full_name="Op", role=Role.OPERATOR)
        self.student = User.objects.create_user("pause-s@x.test", "pw-123456", full_name="Stu", role=Role.STUDENT)
        self.admin = User.objects.create_user("pause-a@x.test", "pw-123456", full_name="Adm", role=Role.ADMIN)
        self.url = reverse("orders:shop_status")

    def _set_active(self, value):
        self.shop.is_active = value
        self.shop.save(update_fields=["is_active"])

    def _messages(self, resp):
        return [str(m) for m in get_messages(resp.wsgi_request)]


class ShopStatusViewTests(ShopPauseBase):
    def test_operator_pause_deactivates_shop_and_messages(self):
        self.client.force_login(self.operator)
        resp = self.client.post(self.url, {"action": "pause", "next": reverse("orders:queue")})
        self.shop.refresh_from_db()
        self.assertFalse(self.shop.is_active)
        self.assertEqual(self._messages(resp),
                         ["Online orders paused. Students can't place new orders until you resume."])
        self.assertRedirects(resp, reverse("orders:queue"))

    def test_operator_resume_reactivates_shop(self):
        self._set_active(False)
        self.client.force_login(self.operator)
        resp = self.client.post(self.url, {"action": "resume"})
        self.shop.refresh_from_db()
        self.assertTrue(self.shop.is_active)
        self.assertEqual(self._messages(resp), ["Online orders resumed."])
        self.assertRedirects(resp, reverse("home"), fetch_redirect_response=False)

    def test_get_is_not_allowed(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_student_post_forbidden(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(self.url, {"action": "pause"}).status_code, 403)
        self.shop.refresh_from_db()
        self.assertTrue(self.shop.is_active)

    def test_admin_post_forbidden(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url, {"action": "pause"}).status_code, 403)
        self.shop.refresh_from_db()
        self.assertTrue(self.shop.is_active)

    def test_anonymous_redirected_to_login(self):
        resp = self.client.post(self.url, {"action": "pause"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("next=", resp["Location"])
        self.shop.refresh_from_db()
        self.assertTrue(self.shop.is_active)

    def test_bad_action_is_400(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.post(self.url, {"action": "explode"}).status_code, 400)
        self.assertEqual(self.client.post(self.url, {}).status_code, 400)
        self.shop.refresh_from_db()
        self.assertTrue(self.shop.is_active)

    def test_no_shop_redirects_with_error(self):
        Shop.objects.all().delete()
        self.client.force_login(self.operator)
        resp = self.client.post(self.url, {"action": "pause"})
        self.assertEqual(self._messages(resp), ["No shop is set up yet."])
        self.assertRedirects(resp, reverse("home"), fetch_redirect_response=False)

    def test_unsafe_next_is_ignored(self):
        self.client.force_login(self.operator)
        resp = self.client.post(self.url, {"action": "pause", "next": "https://evil.example/steal"})
        self.assertRedirects(resp, reverse("home"), fetch_redirect_response=False)


class NewOrderWhilePausedTests(ShopPauseBase):
    def _post_data(self):
        data = {"docs-TOTAL_FORMS": "5", "docs-INITIAL_FORMS": "0", "docs-MIN_NUM_FORMS": "0",
                "docs-MAX_NUM_FORMS": "5", "notes": ""}
        for i in range(5):
            data[f"docs-{i}-rate"] = self.rate.pk
            data[f"docs-{i}-copies"] = "1"
            data[f"docs-{i}-binding_type"] = "NONE"
        data["docs-0-file"] = SimpleUploadedFile("a.pdf", make_pdf(2), content_type="application/pdf")
        return data

    def test_get_shows_paused_notice_without_form(self):
        self._set_active(False)
        self.client.force_login(self.student)
        resp = self.client.get(reverse("orders:new_order"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Online orders are paused right now. Please check back later.")
        self.assertNotContains(resp, "docs-TOTAL_FORMS")
        self.assertNotContains(resp, "Review order")

    def test_post_creates_no_order_and_no_file(self):
        self._set_active(False)
        self.client.force_login(self.student)
        resp = self.client.post(reverse("orders:new_order"), self._post_data())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Online orders are paused right now.")
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(list(self.media_root.rglob("*.pdf")), [])
        self.assertNotIn("printq_draft", self.client.session)

    def test_active_shop_shows_form(self):
        self.client.force_login(self.student)
        resp = self.client.get(reverse("orders:new_order"))
        self.assertContains(resp, "Review order")
        self.assertNotContains(resp, "Online orders are paused right now.")


class OperatorPauseControlTests(ShopPauseBase):
    def test_dashboard_shows_pause_when_active(self):
        self.client.force_login(self.operator)
        resp = self.client.get(reverse("operator_dashboard"))
        self.assertContains(resp, "Pause online orders")
        self.assertNotContains(resp, "Resume online orders")

    def test_dashboard_shows_resume_when_paused(self):
        self._set_active(False)
        self.client.force_login(self.operator)
        resp = self.client.get(reverse("operator_dashboard"))
        self.assertContains(resp, "Resume online orders")
        self.assertContains(resp, "Online orders are paused.")
        self.assertNotContains(resp, "Pause online orders")

    def test_queue_shows_pause_control(self):
        self.client.force_login(self.operator)
        resp = self.client.get(reverse("orders:queue"))
        self.assertContains(resp, "Pause online orders")
        self.assertContains(resp, 'id="live-region"')
