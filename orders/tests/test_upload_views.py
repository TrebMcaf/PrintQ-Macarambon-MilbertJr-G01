import unittest.mock
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from catalog.models import ColorMode, PaperSize, PrintRate, Shop
from orders.models import Order
from orders.tests.utils import LocalStorageTestMixin, make_pdf
from orders.views import DRAFT_KEY, _safe_file_name


class UploadViewTests(LocalStorageTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        Shop.objects.create(name="Shop", location="Here")
        self.rate = PrintRate.objects.create(paper_size=PaperSize.A4, color_mode=ColorMode.MONO,
                                             price_per_page=Decimal("2.00"))
        self.student = User.objects.create_user("up-s1@x.test", "pw-123456", full_name="S One")
        self.other = User.objects.create_user("up-s2@x.test", "pw-123456", full_name="S Two")
        self.operator = User.objects.create_user("up-op@x.test", "pw-123456", full_name="Op", role=Role.OPERATOR)
        self.client.force_login(self.student)

    def _post_data(self, files, notes=""):
        data = {
            "docs-TOTAL_FORMS": "5",
            "docs-INITIAL_FORMS": "0",
            "docs-MIN_NUM_FORMS": "0",
            "docs-MAX_NUM_FORMS": "5",
            "notes": notes,
        }
        for i in range(5):
            data[f"docs-{i}-rate"] = self.rate.pk
            data[f"docs-{i}-copies"] = "1"
            data[f"docs-{i}-binding_type"] = "NONE"
        for i, f in files.items():
            data[f"docs-{i}-file"] = f
        return data

    def _pdf(self, name="a.pdf", pages=3):
        return SimpleUploadedFile(name, make_pdf(pages), content_type="application/pdf")

    def _files_on_disk(self):
        return list(self.media_root.rglob("*.pdf"))

    def _upload_one(self):
        pdf_bytes = make_pdf(3)
        upload = SimpleUploadedFile("a.pdf", pdf_bytes, content_type="application/pdf")
        resp = self.client.post(reverse("orders:new_order"), self._post_data({0: upload}))
        self.assertRedirects(resp, reverse("orders:confirm_order"))
        return pdf_bytes

    def test_new_order_page_shows_rates(self):
        resp = self.client.get(reverse("orders:new_order"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Review order")
        self.assertContains(resp, "₱2.00")

    def test_post_valid_pdf_creates_draft(self):
        self._upload_one()
        self.assertEqual(len(self._files_on_disk()), 1)
        self.assertIn(DRAFT_KEY, self.client.session)
        self.assertEqual(Order.objects.count(), 0)

    def test_confirm_page_shows_draft(self):
        self._upload_one()
        resp = self.client.get(reverse("orders:confirm_order"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "a.pdf")
        self.assertContains(resp, "6.00")
        self.assertContains(resp, "None")

    def test_upload_oserror_cleans_up(self):
        with unittest.mock.patch("orders.views.storage.upload_file", side_effect=OSError("disk full")):
            resp = self.client.post(reverse("orders:new_order"), self._post_data({0: self._pdf()}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Upload failed")
        self.assertEqual(self._files_on_disk(), [])
        self.assertNotIn(DRAFT_KEY, self.client.session)

    def test_confirm_places_order(self):
        self._upload_one()
        resp = self.client.post(reverse("orders:confirm_order"), {"action": "confirm"})
        order = Order.objects.get()
        self.assertEqual(order.total_amount, Decimal("6.00"))
        path = self._files_on_disk()[0]
        self.assertEqual(order.items.get().file_url, str(path.relative_to(self.media_root)).replace("\\", "/"))
        self.assertNotIn(DRAFT_KEY, self.client.session)
        self.assertRedirects(resp, reverse("orders:order_detail", args=[order.pk]))

    def test_discard_deletes_files(self):
        self._upload_one()
        resp = self.client.post(reverse("orders:confirm_order"), {"action": "discard"})
        self.assertEqual(self._files_on_disk(), [])
        self.assertEqual(Order.objects.count(), 0)
        self.assertRedirects(resp, reverse("orders:new_order"))

    def test_non_pdf_rejected(self):
        txt = SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")
        resp = self.client.post(reverse("orders:new_order"), self._post_data({0: txt}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Only PDF files are accepted.")
        self.assertEqual(self._files_on_disk(), [])

    def test_new_order_redirects_while_draft_exists(self):
        self._upload_one()
        resp = self.client.post(reverse("orders:new_order"), self._post_data({0: self._pdf("b.pdf")}))
        self.assertRedirects(resp, reverse("orders:confirm_order"))
        self.assertEqual(len(self._files_on_disk()), 1)

    def test_inactive_rate_redirects_and_cleans_up(self):
        self._upload_one()
        self.rate.is_active = False
        self.rate.save()
        resp = self.client.get(reverse("orders:confirm_order"))
        self.assertRedirects(resp, reverse("orders:new_order"))
        self.assertEqual(self._files_on_disk(), [])
        self.assertEqual(Order.objects.count(), 0)

    def test_operator_can_download_item(self):
        pdf_bytes = self._upload_one()
        self.client.post(reverse("orders:confirm_order"), {"action": "confirm"})
        item = Order.objects.get().items.get()
        self.client.force_login(self.operator)
        resp = self.client.get(reverse("orders:download_item", args=[item.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertEqual(b"".join(resp.streaming_content), pdf_bytes)

    def test_student_cannot_download_item(self):
        self._upload_one()
        self.client.post(reverse("orders:confirm_order"), {"action": "confirm"})
        item = Order.objects.get().items.get()
        self.assertEqual(self.client.get(reverse("orders:download_item", args=[item.pk])).status_code, 403)

    def test_download_link_visible_only_to_operator(self):
        self._upload_one()
        self.client.post(reverse("orders:confirm_order"), {"action": "confirm"})
        order = Order.objects.get()
        url = reverse("orders:order_detail", args=[order.pk])
        self.client.force_login(self.operator)
        self.assertContains(self.client.get(url), "Download")
        self.client.force_login(self.student)
        self.assertNotContains(self.client.get(url), ">Download<")

    def test_unknown_action_is_bad_request(self):
        self._upload_one()
        resp = self.client.post(reverse("orders:confirm_order"), {"action": "nope"})
        self.assertEqual(resp.status_code, 400)

    def test_safe_file_name(self):
        self.assertEqual(_safe_file_name("../../a b.pdf"), "a_b.pdf")
        self.assertEqual(_safe_file_name(".."), "document.pdf")
        long_name = _safe_file_name("x" * 300 + ".pdf")
        self.assertEqual(len(long_name), 255)
        self.assertTrue(long_name.endswith(".pdf"))
