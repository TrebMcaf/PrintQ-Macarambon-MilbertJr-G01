import io
import os
from decimal import Decimal
from unittest import mock

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from catalog.models import ColorMode, PaperSize, PrintRate, Shop
from orders import services
from orders.models import Status
from orders.services import ItemSpec

PW = "Pw-123456!"


class AdminRoleBase(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(name="Campus Print", location="Main Bldg", contact_phone="0917")
        self.rate = PrintRate.objects.create(paper_size=PaperSize.A4, color_mode=ColorMode.MONO,
                                             price_per_page=Decimal("2.00"))
        self.rate2 = PrintRate.objects.create(paper_size=PaperSize.A4, color_mode=ColorMode.COLOR,
                                              price_per_page=Decimal("10.00"))
        self.admin = User.objects.create_user("admin@x.test", PW, full_name="Boss Admin", role=Role.ADMIN)
        self.operator = User.objects.create_user("op@x.test", PW, full_name="Op One", role=Role.OPERATOR)
        self.alice = User.objects.create_user("alice@x.test", PW, full_name="Alice Cruz")
        self.bob = User.objects.create_user("bob@school.test", PW, full_name="Bob Reyes")

    def place(self, user, pages=5):
        return services.place_order(user, [ItemSpec(
            rate_id=self.rate.pk, file_name="a.pdf", file_url="p/a.pdf",
            file_size_mb=Decimal("1.00"), page_count=pages, copies=1)])

    def page_urls(self):
        return [reverse("admin_dashboard"), reverse("admin_students"),
                reverse("admin_student_detail", args=[self.alice.pk]),
                reverse("admin_operators"), reverse("admin_shop")]


class RoleModelTests(AdminRoleBase):
    def test_check_constraint_accepts_admin_rejects_boss(self):
        with transaction.atomic():
            User.objects.filter(pk=self.alice.pk).update(role="ADMIN")
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.role, Role.ADMIN)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.filter(pk=self.alice.pk).update(role="BOSS")

    def test_create_superuser_is_admin(self):
        su = User.objects.create_superuser("su@x.test", PW, full_name="Su")
        self.assertEqual(su.role, Role.ADMIN)
        self.assertTrue(su.is_admin)

    def test_home_redirects_admin(self):
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.get(reverse("home")), reverse("admin_dashboard"))


class AccessTests(AdminRoleBase):
    def test_admin_gets_200(self):
        self.client.force_login(self.admin)
        for url in self.page_urls():
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_student_and_operator_forbidden(self):
        for user in (self.alice, self.operator):
            self.client.force_login(user)
            for url in self.page_urls():
                self.assertEqual(self.client.get(url).status_code, 403, (user.role, url))

    def test_anonymous_redirected(self):
        for url in self.page_urls():
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, url)
            self.assertTrue(resp.url.startswith(reverse("accounts:login")), url)

    def test_nav_for_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("admin_students"))
        self.assertContains(resp, 'href="%s" aria-current="page"' % reverse("admin_students"))
        self.assertNotContains(resp, "New order")
        self.assertNotContains(resp, ">Queue<")


class DashboardTests(AdminRoleBase):
    def test_counts(self):
        self.place(self.alice)
        done = self.place(self.bob, pages=10)
        for s in (Status.PRINTING, Status.READY, Status.COMPLETED):
            services.transition_status(done.pk, s, self.operator)
        User.objects.create_user("gone@x.test", PW, full_name="Gone", is_active=False)
        self.client.force_login(self.admin)
        ctx = self.client.get(reverse("admin_dashboard")).context
        self.assertEqual(ctx["student_count"], 2)
        self.assertEqual(ctx["operator_count"], 1)
        self.assertEqual(ctx["orders_total"], 2)
        self.assertEqual(ctx["active_orders"], 1)
        self.assertEqual(ctx["orders_today"], 2)
        self.assertEqual(ctx["revenue_total"], Decimal("20.00"))
        self.assertEqual(len(ctx["recent_students"]), 3)  # includes inactive
        self.assertEqual(len(ctx["recent_orders"]), 2)
        self.assertEqual(ctx["recent_orders"][0].user.pk, self.bob.pk)  # newest first, user preloaded

    def test_revenue_default_zero(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("admin_dashboard")).context["revenue_total"], Decimal("0.00"))


class StudentTests(AdminRoleBase):
    def test_search_by_name_and_email(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("admin_students"), {"q": "alice"})
        self.assertContains(resp, "Alice Cruz")
        self.assertNotContains(resp, "Bob Reyes")
        resp = self.client.get(reverse("admin_students"), {"q": "SCHOOL.test"})
        self.assertContains(resp, "Bob Reyes")
        self.assertNotContains(resp, "Alice Cruz")
        resp = self.client.get(reverse("admin_students"), {"q": "zzz"})
        self.assertContains(resp, "No students match")

    def test_order_aggregates(self):
        self.place(self.alice)
        self.place(self.alice, pages=10)
        self.client.force_login(self.admin)
        rows = {s.pk: s for s in self.client.get(reverse("admin_students")).context["page"]}
        self.assertEqual(rows[self.alice.pk].order_count, 2)
        self.assertEqual(rows[self.alice.pk].order_total, Decimal("30.00"))
        self.assertEqual(rows[self.bob.pk].order_count, 0)
        self.assertEqual(rows[self.bob.pk].order_total, Decimal("0"))

    def test_detail_lists_orders_and_404_for_operator(self):
        order = self.place(self.alice)
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("admin_student_detail", args=[self.alice.pk]))
        self.assertContains(resp, order.tracking_code)
        self.assertEqual(self.client.get(reverse("admin_student_detail", args=[self.operator.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("admin_student_detail", args=[self.admin.pk])).status_code, 404)


class ToggleTests(AdminRoleBase):
    def toggle(self, user, **extra):
        return self.client.post(reverse("admin_toggle_active", args=[user.pk]), extra)

    def test_deactivate_blocks_login_and_reactivate(self):
        self.client.force_login(self.admin)
        resp = self.toggle(self.alice, next=reverse("admin_students"))
        self.assertRedirects(resp, reverse("admin_students"))
        self.alice.refresh_from_db()
        self.assertFalse(self.alice.is_active)
        self.assertFalse(self.client.__class__().login(username="alice@x.test", password=PW))
        resp = self.toggle(self.alice)
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.is_active)
        self.assertTrue(self.client.__class__().login(username="alice@x.test", password=PW))

    def test_messages(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.post(reverse("admin_toggle_active", args=[self.alice.pk]), follow=True),
                            "Alice Cruz deactivated.")
        self.assertContains(self.client.post(reverse("admin_toggle_active", args=[self.alice.pk]), follow=True),
                            "Alice Cruz reactivated.")

    def test_refuses_admin_target_and_self(self):
        other_admin = User.objects.create_user("a2@x.test", PW, full_name="A2", role=Role.ADMIN)
        self.client.force_login(self.admin)
        self.toggle(other_admin)
        self.toggle(self.admin)
        other_admin.refresh_from_db()
        self.admin.refresh_from_db()
        self.assertTrue(other_admin.is_active)
        self.assertTrue(self.admin.is_active)

    def test_get_not_allowed(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("admin_toggle_active", args=[self.alice.pk])).status_code, 405)

    def test_non_admin_forbidden(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.toggle(self.alice).status_code, 403)
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.is_active)

    def test_unsafe_next_ignored(self):
        self.client.force_login(self.admin)
        resp = self.toggle(self.alice, next="https://evil.example/")
        self.assertRedirects(resp, reverse("admin_students"))

    def test_operator_can_be_deactivated(self):
        self.client.force_login(self.admin)
        self.toggle(self.operator)
        self.operator.refresh_from_db()
        self.assertFalse(self.operator.is_active)


class OperatorCreateTests(AdminRoleBase):
    data = {"email": "newop@x.test", "full_name": "New Op", "phone_number": "",
            "password1": "Str0ng-pass-99", "password2": "Str0ng-pass-99"}

    def test_create(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("admin_operators"), {**self.data, "role": "ADMIN", "is_staff": "on"},
                                follow=True)
        self.assertContains(resp, "Operator New Op created.")
        op = User.objects.get(email="newop@x.test")
        self.assertEqual(op.role, Role.OPERATOR)
        self.assertFalse(op.is_staff)
        self.assertFalse(op.is_superuser)
        self.assertTrue(op.check_password("Str0ng-pass-99"))
        self.assertNotEqual(op.password, "Str0ng-pass-99")

    def test_duplicate_email_rejected(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("admin_operators"), {**self.data, "email": "OP@x.test"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already exists")
        self.assertEqual(User.objects.filter(role=Role.OPERATOR).count(), 1)

    def test_password_mismatch(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("admin_operators"), {**self.data, "password2": "different-1"})
        self.assertContains(resp, "Passwords do not match.")
        self.assertFalse(User.objects.filter(email="newop@x.test").exists())


class ShopTests(AdminRoleBase):
    def test_shop_form(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("admin_shop"), {
            "section": "shop", "name": "Renamed", "location": "Annex", "contact_phone": "123"})
        self.assertRedirects(resp, reverse("admin_shop"))
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.name, "Renamed")
        self.assertFalse(self.shop.is_active)

    def rates_post(self, **over):
        rates = list(PrintRate.objects.order_by("paper_size", "-color_mode"))
        data = {"section": "rates", "rates-TOTAL_FORMS": len(rates), "rates-INITIAL_FORMS": len(rates),
                "rates-MIN_NUM_FORMS": 0, "rates-MAX_NUM_FORMS": 1000}
        for i, r in enumerate(rates):
            data[f"rates-{i}-rate_id"] = r.pk
            data[f"rates-{i}-price_per_page"] = str(r.price_per_page)
            if r.is_active:
                data[f"rates-{i}-is_active"] = "on"
        data.update(over)
        return data

    def test_rates_update(self):
        self.client.force_login(self.admin)
        first = PrintRate.objects.order_by("paper_size", "-color_mode").first()
        resp = self.client.post(reverse("admin_shop"), self.rates_post(**{"rates-0-price_per_page": "3.50"}))
        self.assertRedirects(resp, reverse("admin_shop"))
        first.refresh_from_db()
        self.assertEqual(first.price_per_page, Decimal("3.50"))

    def test_rate_cannot_be_deleted(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("admin_shop"))
        self.assertNotIn("DELETE", resp.context["rate_formset"].forms[0].fields)
        self.client.post(reverse("admin_shop"), self.rates_post(**{"rates-0-DELETE": "on"}))
        self.assertEqual(PrintRate.objects.count(), 2)

    def test_negative_price_rejected(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("admin_shop"), self.rates_post(**{"rates-0-price_per_page": "-1"}))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(PrintRate.objects.get(pk=self.rate2.pk).price_per_page, Decimal("10.00"))

    def test_no_shop_does_not_crash(self):
        from orders.models import Order
        Order.objects.all().delete()
        PrintRate.objects.all().delete()
        Shop.objects.all().delete()
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("admin_shop"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "manage.py seed")
        self.assertEqual(self.client.get(reverse("admin_dashboard")).status_code, 200)


class OrderViewAccessTests(AdminRoleBase):
    def test_admin_views_order_but_cannot_download(self):
        order = self.place(self.alice)
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("orders:order_detail", args=[order.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, order.tracking_code)
        self.assertNotContains(resp, "Download")
        item = order.items.first()
        self.assertEqual(self.client.get(reverse("orders:download_item", args=[item.pk])).status_code, 403)

    def test_operator_dashboard_has_no_django_admin_link(self):
        self.client.force_login(User.objects.create_user("opstaff@x.test", PW, full_name="Staff Op",
                                                         role=Role.OPERATOR, is_staff=True))
        resp = self.client.get(reverse("operator_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "/admin/catalog/printrate/")


class SeedAdminTests(TestCase):
    def run_seed(self):
        with mock.patch.dict(os.environ, {"SEED_ADMIN_PASSWORD": "Seed-Admin-Pw-1"}):
            call_command("seed", stdout=io.StringIO())

    def test_seed_creates_admin_idempotently(self):
        self.run_seed()
        self.run_seed()
        qs = User.objects.filter(email="admin@printq.test")
        self.assertEqual(qs.count(), 1)
        admin = qs.get()
        self.assertEqual(admin.role, Role.ADMIN)
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.check_password("Seed-Admin-Pw-1"))
