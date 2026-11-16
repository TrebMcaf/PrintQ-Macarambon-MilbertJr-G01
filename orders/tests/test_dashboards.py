from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from catalog.models import ColorMode, PaperSize, PrintRate, Shop
from orders import services
from orders.models import Status
from orders.services import ItemSpec


class DashboardTests(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(name="Campus Print", location="Main Bldg")
        self.rate = PrintRate.objects.create(paper_size=PaperSize.A4, color_mode=ColorMode.MONO,
                                             price_per_page=Decimal("2.00"))
        self.student = User.objects.create_user("s1@x.test", "Pw-123456!", full_name="S One")
        self.other = User.objects.create_user("s2@x.test", "Pw-123456!", full_name="S Two")
        self.operator = User.objects.create_user("op@x.test", "Pw-123456!", full_name="Op",
                                                 role=Role.OPERATOR)

        def place(user):
            return services.place_order(user, [ItemSpec(
                rate_id=self.rate.pk, file_name="a.pdf", file_url="p/a.pdf",
                file_size_mb=Decimal("1.00"), page_count=5, copies=1)])

        self.pending = place(self.student)
        self.ready = place(self.student)
        self.completed = place(self.student)
        services.transition_status(self.ready.pk, Status.PRINTING, self.operator)
        services.transition_status(self.ready.pk, Status.READY, self.operator)
        services.transition_status(self.completed.pk, Status.PRINTING, self.operator)
        services.transition_status(self.completed.pk, Status.READY, self.operator)
        services.transition_status(self.completed.pk, Status.COMPLETED, self.operator)
        self.others_order = place(self.other)

    def test_anonymous_redirected_to_login(self):
        for name in ("student_dashboard", "operator_dashboard"):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 302, name)
            self.assertTrue(resp.url.startswith(reverse("accounts:login")), resp.url)

    def test_student_dashboard(self):
        self.client.force_login(self.student)
        resp = self.client.get(reverse("student_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hi, ")
        self.assertEqual(resp.context["active_count"], 1)
        self.assertEqual(resp.context["ready_count"], 1)
        self.assertEqual(resp.context["completed_count"], 1)
        self.assertContains(resp, self.ready.tracking_code)
        self.assertContains(resp, "Ready for pickup")
        self.assertNotContains(resp, self.others_order.tracking_code)

    def test_cross_role_dashboard_forbidden(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse("operator_dashboard")).status_code, 403)
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(reverse("student_dashboard")).status_code, 403)

    def test_operator_dashboard(self):
        self.client.force_login(self.operator)
        resp = self.client.get(reverse("operator_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["pending_count"], 2)
        self.assertEqual(resp.context["ready_count"], 1)
        self.assertEqual(resp.context["completed_today"], 1)
        self.assertEqual(resp.context["collected_today"], Decimal("10.00"))
        self.assertEqual(resp.context["placed_today"], 4)
        self.assertContains(resp, "Open queue")

    def test_home_redirects_by_role(self):
        self.client.force_login(self.operator)
        self.assertRedirects(self.client.get(reverse("home")), reverse("operator_dashboard"))
        self.client.force_login(self.student)
        self.assertRedirects(self.client.get(reverse("home")), reverse("student_dashboard"))

    def test_nav_has_dashboard_link(self):
        self.client.force_login(self.student)
        resp = self.client.get(reverse("student_dashboard"))
        self.assertContains(resp, 'href="/"')
        self.assertContains(resp, ">Dashboard</a>")

    def test_signup_shows_message(self):
        resp = self.client.post(reverse("accounts:signup"), {
            "email": "new@x.test", "full_name": "New", "password1": "Str0ng-pass-99",
            "password2": "Str0ng-pass-99"}, follow=True)
        self.assertContains(resp, "Account created")

    def test_login_shows_message_and_lands_on_student_dashboard(self):
        resp = self.client.post(reverse("accounts:login"),
                                {"username": "s1@x.test", "password": "Pw-123456!"}, follow=True)
        self.assertEqual(resp.redirect_chain[-1][0], reverse("student_dashboard"))
        self.assertContains(resp, "Welcome back")

    def test_logout_shows_message_and_requires_login_after(self):
        self.client.force_login(self.student)
        resp = self.client.post(reverse("accounts:logout"), follow=True)
        self.assertContains(resp, "You have been logged out.")
        resp = self.client.get(reverse("student_dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.url.startswith(reverse("accounts:login")), resp.url)

    def test_wrong_password_shows_error(self):
        resp = self.client.post(reverse("accounts:login"),
                                {"username": "s1@x.test", "password": "wrong-pass"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Please enter a correct")
