from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from catalog.models import ColorMode, PaperSize, PrintRate, Shop
from orders import services
from orders.models import Status
from orders.services import ItemSpec


class NotesEditTests(TestCase):
    def setUp(self):
        Shop.objects.create(name="Shop", location="Here")
        rate = PrintRate.objects.create(paper_size=PaperSize.A4, color_mode=ColorMode.MONO,
                                        price_per_page=Decimal("2.00"))
        self.student = User.objects.create_user("s1@x.test", "pw-123456", full_name="S One")
        self.other = User.objects.create_user("s2@x.test", "pw-123456", full_name="S Two")
        self.operator = User.objects.create_user("op@x.test", "pw-123456", full_name="Op", role=Role.OPERATOR)
        self.order = services.place_order(self.student, [ItemSpec(
            rate_id=rate.pk, file_name="a.pdf", file_url="p/a.pdf",
            file_size_mb=Decimal("1.00"), page_count=3, copies=1)], "first notes")
        self.url = reverse("orders:edit_notes", args=[self.order.pk])

    # --- service rules
    def test_service_updates_notes_and_trims(self):
        order = services.update_notes(self.order.pk, self.student, "  new notes  ")
        self.assertEqual(order.notes, "new notes")

    def test_service_clears_notes_when_blank(self):
        order = services.update_notes(self.order.pk, self.student, "   ")
        self.assertIsNone(order.notes)

    def test_service_keeps_price_and_status(self):
        total = self.order.total_amount
        services.update_notes(self.order.pk, self.student, "changed")
        self.order.refresh_from_db()
        self.assertEqual(self.order.total_amount, total)
        self.assertEqual(self.order.status, Status.PENDING)

    def test_service_rejects_notes_over_limit(self):
        with self.assertRaises(ValidationError):
            services.update_notes(self.order.pk, self.student, "x" * 501)

    def test_service_rejects_non_pending(self):
        services.transition_status(self.order.pk, Status.PRINTING, self.operator)
        with self.assertRaises(ValidationError):
            services.update_notes(self.order.pk, self.student, "too late")

    def test_service_rejects_other_student_and_operator(self):
        with self.assertRaises(PermissionDenied):
            services.update_notes(self.order.pk, self.other, "hack")
        with self.assertRaises(PermissionDenied):
            services.update_notes(self.order.pk, self.operator, "hack")

    # --- views
    def test_get_shows_form_with_current_notes(self):
        self.client.force_login(self.student)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "first notes")

    def test_post_saves_and_redirects_with_message(self):
        self.client.force_login(self.student)
        resp = self.client.post(self.url, {"notes": "please staple"}, follow=True)
        self.assertRedirects(resp, reverse("orders:order_detail", args=[self.order.pk]))
        self.assertContains(resp, "Notes updated")
        self.order.refresh_from_db()
        self.assertEqual(self.order.notes, "please staple")

    def test_post_too_long_shows_error_and_does_not_save(self):
        self.client.force_login(self.student)
        resp = self.client.post(self.url, {"notes": "x" * 501})
        self.assertEqual(resp.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.notes, "first notes")

    def test_other_student_gets_404(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {"notes": "hack"}).status_code, 404)

    def test_operator_forbidden(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("accounts:login"), resp.url)

    def test_non_pending_redirects_with_error(self):
        services.transition_status(self.order.pk, Status.PRINTING, self.operator)
        self.client.force_login(self.student)
        resp = self.client.post(self.url, {"notes": "late"}, follow=True)
        self.assertRedirects(resp, reverse("orders:order_detail", args=[self.order.pk]))
        self.assertContains(resp, "only be changed while the order is pending")
        self.order.refresh_from_db()
        self.assertEqual(self.order.notes, "first notes")

    def test_detail_shows_edit_link_only_while_pending(self):
        self.client.force_login(self.student)
        detail = reverse("orders:order_detail", args=[self.order.pk])
        self.assertContains(self.client.get(detail), self.url)
        services.transition_status(self.order.pk, Status.PRINTING, self.operator)
        self.assertNotContains(self.client.get(detail), self.url)
