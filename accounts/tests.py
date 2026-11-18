from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role

User = get_user_model()


class ProfileTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user("s1@x.test", "Pw-123456!", full_name="S One")
        self.other = User.objects.create_user("s2@x.test", "Pw-123456!", full_name="S Two")
        self.operator = User.objects.create_user("op@x.test", "Pw-123456!", full_name="Op",
                                                 role=Role.OPERATOR)

    def test_anonymous_redirected_to_login(self):
        for name in ("accounts:profile", "accounts:profile_edit", "accounts:password_change"):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 302, name)
            self.assertTrue(resp.url.startswith(reverse("accounts:login")), name)

    def test_student_sees_own_profile(self):
        self.client.force_login(self.student)
        resp = self.client.get(reverse("accounts:profile"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "S One")
        self.assertContains(resp, "s1@x.test")
        self.assertContains(resp, "Student")
        self.assertNotContains(resp, "s2@x.test")

    def test_operator_sees_own_profile(self):
        self.client.force_login(self.operator)
        resp = self.client.get(reverse("accounts:profile"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Operator")

    def test_edit_profile_success(self):
        self.client.force_login(self.student)
        resp = self.client.post(reverse("accounts:profile_edit"), {
            "full_name": "New Name", "email": "new@x.test", "phone_number": "0917 123 4567"}, follow=True)
        self.assertRedirects(resp, reverse("accounts:profile"))
        self.assertContains(resp, "Profile updated.")
        self.student.refresh_from_db()
        self.assertEqual(self.student.full_name, "New Name")
        self.assertEqual(self.student.email, "new@x.test")
        self.assertEqual(self.student.phone_number, "0917 123 4567")

    def test_edit_rejects_other_users_email_any_case(self):
        self.client.force_login(self.student)
        resp = self.client.post(reverse("accounts:profile_edit"), {
            "full_name": "S One", "email": "S2@X.TEST", "phone_number": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already exists")
        self.student.refresh_from_db()
        self.assertEqual(self.student.email, "s1@x.test")

    def test_edit_keeping_own_email_succeeds(self):
        self.client.force_login(self.student)
        resp = self.client.post(reverse("accounts:profile_edit"), {
            "full_name": "S One", "email": "s1@x.test", "phone_number": ""})
        self.assertRedirects(resp, reverse("accounts:profile"))
        self.assertNotContains(self.client.get(reverse("accounts:profile_edit")), "already exists")

    def test_edit_cannot_change_role(self):
        self.client.force_login(self.student)
        self.client.post(reverse("accounts:profile_edit"), {
            "full_name": "S One", "email": "s1@x.test", "phone_number": "", "role": "OPERATOR"})
        self.student.refresh_from_db()
        self.assertEqual(self.student.role, Role.STUDENT)

    def test_edit_invalid_phone(self):
        self.client.force_login(self.student)
        resp = self.client.post(reverse("accounts:profile_edit"), {
            "full_name": "S One", "email": "s1@x.test", "phone_number": "abc"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Enter a valid contact number.")

    def test_edit_empty_name_rejected(self):
        self.client.force_login(self.student)
        resp = self.client.post(reverse("accounts:profile_edit"), {
            "full_name": "", "email": "s1@x.test", "phone_number": ""})
        self.assertEqual(resp.status_code, 200)
        self.student.refresh_from_db()
        self.assertEqual(self.student.full_name, "S One")

    def test_password_change_keeps_session(self):
        self.client.force_login(self.student)
        resp = self.client.post(reverse("accounts:password_change"), {
            "old_password": "Pw-123456!", "new_password1": "N3w-Passw0rd!",
            "new_password2": "N3w-Passw0rd!"})
        self.assertRedirects(resp, reverse("accounts:profile"))
        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password("N3w-Passw0rd!"))
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 200)

    def test_password_change_wrong_old_password(self):
        self.client.force_login(self.student)
        resp = self.client.post(reverse("accounts:password_change"), {
            "old_password": "wrong-old", "new_password1": "N3w-Passw0rd!",
            "new_password2": "N3w-Passw0rd!"})
        self.assertEqual(resp.status_code, 200)
        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password("Pw-123456!"))

    def test_nav_links_to_profile(self):
        self.client.force_login(self.student)
        resp = self.client.get(reverse("accounts:profile"))
        self.assertContains(resp, 'href="%s"' % reverse("accounts:profile"))
