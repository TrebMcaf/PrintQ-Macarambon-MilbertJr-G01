import io
import os
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from accounts.services import apply_account_change, check_account_change
from catalog.models import ColorMode, PaperSize, PrintRate, Shop
from orders import services
from orders.models import Status
from orders.services import ItemSpec

PW = "Pw-123456!"
NEW_PW = "Fresh-Pass-4321"


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
        return [reverse("admin_dashboard"), reverse("admin_users"), reverse("admin_user_create"),
                reverse("admin_user_detail", args=[self.alice.pk]),
                reverse("admin_user_edit", args=[self.alice.pk]),
                reverse("admin_user_password", args=[self.alice.pk]),
                reverse("admin_shop")]

    def edit_data(self, user, **over):
        data = {"full_name": user.full_name, "email": user.email, "phone_number": user.phone_number or "",
                "role": user.role, "is_active": "on" if user.is_active else ""}
        data.update(over)
        return data

    def edit(self, user, follow=False, **over):
        return self.client.post(reverse("admin_user_edit", args=[user.pk]), self.edit_data(user, **over),
                                follow=follow)


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
        resp = self.client.get(reverse("admin_users"))
        self.assertContains(resp, 'href="%s" aria-current="page"' % reverse("admin_users"))
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
        self.assertEqual(ctx["admin_count"], 1)
        self.assertEqual(ctx["orders_total"], 2)
        self.assertEqual(ctx["active_orders"], 1)
        self.assertEqual(ctx["orders_today"], 2)
        self.assertEqual(ctx["revenue_total"], Decimal("20.00"))
        self.assertEqual(len(ctx["recent_students"]), 3)  # includes inactive
        self.assertEqual(len(ctx["recent_orders"]), 2)
        self.assertEqual(ctx["recent_orders"][0].user.pk, self.bob.pk)  # newest first, user preloaded

    def test_admin_count_ignores_inactive_admins(self):
        User.objects.create_user("a2@x.test", PW, full_name="A2", role=Role.ADMIN, is_active=False)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("admin_dashboard")).context["admin_count"], 1)

    def test_revenue_default_zero(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("admin_dashboard")).context["revenue_total"], Decimal("0.00"))


class UserListTests(AdminRoleBase):
    def test_tab_counts(self):
        self.client.force_login(self.admin)
        counts = self.client.get(reverse("admin_users")).context["role_counts"]
        self.assertEqual(counts, {"ALL": 4, "STUDENT": 2, "OPERATOR": 1, "ADMIN": 1})

    def test_role_filter_keeps_counts(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("admin_users"), {"role": "STUDENT"})
        pks = {u.pk for u in resp.context["page"]}
        self.assertEqual(pks, {self.alice.pk, self.bob.pk})
        self.assertEqual(resp.context["role_counts"]["ALL"], 4)
        self.assertEqual(self.client.get(reverse("admin_users"), {"role": "BOGUS"}).context["role"], "")

    def test_status_filter(self):
        self.bob.is_active = False
        self.bob.save()
        self.client.force_login(self.admin)
        inactive = self.client.get(reverse("admin_users"), {"status": "inactive"})
        self.assertEqual([u.pk for u in inactive.context["page"]], [self.bob.pk])
        self.assertEqual(inactive.context["role_counts"]["ALL"], 1)
        active = self.client.get(reverse("admin_users"), {"status": "active"})
        self.assertNotIn(self.bob.pk, [u.pk for u in active.context["page"]])

    def test_search_by_name_and_email(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("admin_users"), {"q": "alice"})
        self.assertContains(resp, "Alice Cruz")
        self.assertNotContains(resp, "Bob Reyes")
        resp = self.client.get(reverse("admin_users"), {"q": "SCHOOL.test"})
        self.assertContains(resp, "Bob Reyes")
        self.assertNotContains(resp, "Alice Cruz")
        resp = self.client.get(reverse("admin_users"), {"q": "zzz"})
        self.assertContains(resp, "No users match these filters.")

    def test_pagination_preserves_filters(self):
        for i in range(26):
            User.objects.create_user(f"pg{i:02d}@x.test", PW, full_name=f"Pg Student {i:02d}")
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("admin_users"), {"role": "STUDENT", "q": "Pg", "page": 2})
        self.assertEqual(resp.context["page"].number, 2)
        self.assertEqual(len(resp.context["page"].object_list), 1)
        self.assertContains(resp, "page=1&amp;role=STUDENT&amp;q=Pg")

    def test_order_aggregates(self):
        self.place(self.alice)
        self.place(self.alice, pages=10)
        self.client.force_login(self.admin)
        rows = {u.pk: u for u in self.client.get(reverse("admin_users")).context["page"]}
        self.assertEqual(rows[self.alice.pk].order_count, 2)
        self.assertEqual(rows[self.alice.pk].order_total, Decimal("30.00"))
        self.assertEqual(rows[self.bob.pk].order_count, 0)
        self.assertEqual(rows[self.bob.pk].order_total, Decimal("0"))

    def test_legacy_students_redirect(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("admin_students"), {"q": "x"})
        self.assertRedirects(resp, "/manage/users/?role=STUDENT&q=x", fetch_redirect_response=False)
        resp = self.client.get(reverse("admin_students"))
        self.assertRedirects(resp, "/manage/users/?role=STUDENT", fetch_redirect_response=False)

    def test_legacy_operators_redirect(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("admin_operators"))
        self.assertRedirects(resp, "/manage/users/?role=OPERATOR", fetch_redirect_response=False)

    def test_legacy_student_detail_redirect(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("admin_student_detail", args=[self.alice.pk]))
        self.assertRedirects(resp, reverse("admin_user_detail", args=[self.alice.pk]),
                             fetch_redirect_response=False)


class UserDetailTests(AdminRoleBase):
    def test_detail_lists_orders_for_any_role(self):
        order = self.place(self.alice)
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("admin_user_detail", args=[self.alice.pk]))
        self.assertContains(resp, order.tracking_code)
        self.assertEqual(self.client.get(reverse("admin_user_detail", args=[self.operator.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin_user_detail", args=[self.admin.pk])).status_code, 200)

    def test_detail_404_for_missing_user(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("admin_user_detail", args=[999999])).status_code, 404)


class UserCreateTests(AdminRoleBase):
    data = {"email": "newuser@x.test", "full_name": "New User", "phone_number": "",
            "password1": "Str0ng-pass-99", "password2": "Str0ng-pass-99"}

    def create(self, role, **over):
        return self.client.post(reverse("admin_user_create"), {**self.data, "role": role, **over})

    def test_create_any_role(self):
        self.client.force_login(self.admin)
        for role in (Role.STUDENT, Role.OPERATOR, Role.ADMIN):
            email = f"new-{role.lower()}@x.test"
            resp = self.create(role, email=email)
            user = User.objects.get(email=email)
            self.assertRedirects(resp, reverse("admin_user_detail", args=[user.pk]))
            self.assertEqual(user.role, role)
            self.assertTrue(user.is_active)
            self.assertFalse(user.is_superuser)

    def test_password_hashed(self):
        self.client.force_login(self.admin)
        self.create(Role.OPERATOR)
        user = User.objects.get(email="newuser@x.test")
        self.assertTrue(user.check_password("Str0ng-pass-99"))
        self.assertNotEqual(user.password, "Str0ng-pass-99")

    def test_duplicate_email_rejected(self):
        self.client.force_login(self.admin)
        resp = self.create(Role.STUDENT, email="ALICE@x.test")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already exists")
        self.assertEqual(User.objects.filter(email__iexact="alice@x.test").count(), 1)

    def test_password_mismatch(self):
        self.client.force_login(self.admin)
        resp = self.create(Role.STUDENT, password2="different-1")
        self.assertContains(resp, "Passwords do not match.")
        self.assertFalse(User.objects.filter(email="newuser@x.test").exists())

    def test_get_renders(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("admin_user_create")).status_code, 200)


class UserEditTests(AdminRoleBase):
    def test_student_to_operator_then_operator_dashboard(self):
        self.client.force_login(self.admin)
        resp = self.edit(self.alice, role=Role.OPERATOR)
        self.assertRedirects(resp, reverse("admin_user_detail", args=[self.alice.pk]))
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.role, Role.OPERATOR)
        self.client.force_login(self.alice)
        self.assertRedirects(self.client.get(reverse("home")), reverse("operator_dashboard"))

    def test_operator_to_admin(self):
        self.client.force_login(self.admin)
        self.edit(self.operator, role=Role.ADMIN)
        self.operator.refresh_from_db()
        self.assertEqual(self.operator.role, Role.ADMIN)

    def test_edit_name_email_phone(self):
        self.client.force_login(self.admin)
        resp = self.edit(self.alice, full_name="Alice Reyes", email="alice.new@x.test", phone_number="0917 123-4567")
        self.assertRedirects(resp, reverse("admin_user_detail", args=[self.alice.pk]))
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.full_name, "Alice Reyes")
        self.assertEqual(self.alice.email, "alice.new@x.test")
        self.assertEqual(self.alice.phone_number, "0917 123-4567")

    def test_duplicate_email_rejected(self):
        self.client.force_login(self.admin)
        resp = self.edit(self.alice, email="BOB@school.test")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already exists")
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.email, "alice@x.test")

    def test_bad_phone_rejected(self):
        self.client.force_login(self.admin)
        resp = self.edit(self.alice, phone_number="abc")
        self.assertContains(resp, "Enter a valid contact number.")

    def test_superuser_role_post_is_ignored(self):
        su = User.objects.create_superuser("su@x.test", PW, full_name="Su")
        self.client.force_login(self.admin)
        self.edit(su, role=Role.STUDENT, is_active="")
        su.refresh_from_db()
        self.assertEqual(su.role, Role.ADMIN)
        self.assertTrue(su.is_active)

    def test_superuser_name_edit_saves_even_when_inactive(self):
        su = User.objects.create_superuser("su@x.test", PW, full_name="Su")
        User.objects.filter(pk=su.pk).update(is_active=False)
        su.refresh_from_db()
        self.client.force_login(self.admin)
        self.edit(su, full_name="Renamed Su")
        su.refresh_from_db()
        self.assertEqual(su.full_name, "Renamed Su")
        self.assertFalse(su.is_active)
        self.assertEqual(su.role, Role.ADMIN)

    def test_self_demote_with_another_active_admin(self):
        User.objects.create_user("a2@x.test", PW, full_name="A2", role=Role.ADMIN)
        self.client.force_login(self.admin)
        resp = self.edit(self.admin, follow=True, role=Role.STUDENT)
        self.assertEqual(resp.redirect_chain[0][0], reverse("home"))
        self.assertContains(resp, "You are no longer an admin.")
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, Role.STUDENT)

    def test_self_demote_as_only_active_admin_blocked(self):
        self.client.force_login(self.admin)
        resp = self.edit(self.admin, role=Role.STUDENT)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "PrintQ needs at least one active admin.")
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, Role.ADMIN)
        self.assertTrue(self.admin.is_active)

    def test_self_deactivate_blocked(self):
        User.objects.create_user("a2@x.test", PW, full_name="A2", role=Role.ADMIN)
        self.client.force_login(self.admin)
        resp = self.edit(self.admin, is_active="")
        self.assertContains(resp, "deactivate your own account")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_last_admin_guard_applies_to_service_layer(self):
        # The UI can only reach rule 3 through a self-change (rule 2 fires first for deactivation),
        # so the rule is exercised directly against the service with a non-admin actor.
        with self.assertRaisesMessage(ValidationError, "PrintQ needs at least one active admin."):
            check_account_change(self.operator, self.admin, Role.STUDENT, True)
        with self.assertRaisesMessage(ValidationError, "PrintQ needs at least one active admin."):
            apply_account_change(self.operator, self.admin.pk, role=Role.ADMIN, is_active=False)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_get_renders(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("admin_user_edit", args=[self.alice.pk])).status_code, 200)


class UserPasswordTests(AdminRoleBase):
    def test_admin_sets_student_password(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("admin_user_password", args=[self.alice.pk]),
                                {"new_password1": NEW_PW, "new_password2": NEW_PW})
        self.assertRedirects(resp, reverse("admin_user_detail", args=[self.alice.pk]))
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.check_password(NEW_PW))
        self.assertFalse(self.alice.check_password(PW))

    def test_self_reset_keeps_session(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("admin_user_password", args=[self.admin.pk]),
                         {"new_password1": NEW_PW, "new_password2": NEW_PW})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password(NEW_PW))
        self.assertEqual(self.client.get(reverse("admin_dashboard")).status_code, 200)

    def test_superuser_password_blocked_for_other_admin(self):
        su = User.objects.create_superuser("su@x.test", PW, full_name="Su")
        self.client.force_login(self.admin)
        url = reverse("admin_user_password", args=[su.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"new_password1": NEW_PW, "new_password2": NEW_PW}).status_code, 403)
        su.refresh_from_db()
        self.assertTrue(su.check_password(PW))

    def test_superuser_can_reset_own_password(self):
        su = User.objects.create_superuser("su@x.test", PW, full_name="Su")
        self.client.force_login(su)
        resp = self.client.post(reverse("admin_user_password", args=[su.pk]),
                                {"new_password1": NEW_PW, "new_password2": NEW_PW})
        self.assertEqual(resp.status_code, 302)
        su.refresh_from_db()
        self.assertTrue(su.check_password(NEW_PW))

    def test_get_renders(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("admin_user_password", args=[self.alice.pk])).status_code, 200)


class ToggleTests(AdminRoleBase):
    def toggle(self, user, follow=False, **extra):
        return self.client.post(reverse("admin_toggle_active", args=[user.pk]), extra, follow=follow)

    def test_deactivate_blocks_login_and_reactivate_student(self):
        self.client.force_login(self.admin)
        resp = self.toggle(self.alice, next=reverse("admin_users"))
        self.assertRedirects(resp, reverse("admin_users"))
        self.alice.refresh_from_db()
        self.assertFalse(self.alice.is_active)
        self.assertFalse(self.client.__class__().login(username="alice@x.test", password=PW))
        self.toggle(self.alice)
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.is_active)
        self.assertTrue(self.client.__class__().login(username="alice@x.test", password=PW))

    def test_deactivate_and_reactivate_operator(self):
        self.client.force_login(self.admin)
        self.toggle(self.operator)
        self.operator.refresh_from_db()
        self.assertFalse(self.operator.is_active)
        self.toggle(self.operator)
        self.operator.refresh_from_db()
        self.assertTrue(self.operator.is_active)

    def test_messages(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.post(reverse("admin_toggle_active", args=[self.alice.pk]), follow=True),
                            "Alice Cruz deactivated.")
        self.assertContains(self.client.post(reverse("admin_toggle_active", args=[self.alice.pk]), follow=True),
                            "Alice Cruz reactivated.")

    def test_deactivate_other_admin_when_two_active(self):
        other_admin = User.objects.create_user("a2@x.test", PW, full_name="A2", role=Role.ADMIN)
        self.client.force_login(self.admin)
        self.toggle(other_admin)
        other_admin.refresh_from_db()
        self.assertFalse(other_admin.is_active)

    def test_self_blocked(self):
        self.client.force_login(self.admin)
        resp = self.toggle(self.admin, follow=True)
        self.assertContains(resp, "deactivate your own account")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_superuser_blocked(self):
        su = User.objects.create_superuser("su@x.test", PW, full_name="Su")
        self.client.force_login(self.admin)
        resp = self.toggle(su, follow=True)
        self.assertContains(resp, "Superuser accounts keep the Admin role")
        su.refresh_from_db()
        self.assertTrue(su.is_active)

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
        self.assertRedirects(resp, reverse("admin_users"))


class ServiceTests(AdminRoleBase):
    def test_status_change_on_superuser_blocked_at_service_layer(self):
        su = User.objects.create_superuser("su@x.test", PW, full_name="Su")
        with self.assertRaisesMessage(ValidationError, "Superuser accounts keep the Admin role"):
            apply_account_change(self.admin, su.pk, role=Role.ADMIN, is_active=False)
        with self.assertRaisesMessage(ValidationError, "Superuser accounts keep the Admin role"):
            apply_account_change(self.admin, su.pk, role=Role.STUDENT, is_active=True)

    def test_omitted_role_keeps_current_role(self):
        apply_account_change(self.admin, self.alice.pk, is_active=False)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.role, Role.STUDENT)
        self.assertFalse(self.alice.is_active)


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
