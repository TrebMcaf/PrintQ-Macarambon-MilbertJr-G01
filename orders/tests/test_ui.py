from types import SimpleNamespace

from django import forms
from django.contrib.messages import constants as message_constants
from django.contrib.messages.storage.base import Message
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import SimpleTestCase


class UiForm(forms.Form):
    name = forms.CharField(widget=forms.TextInput())
    level = forms.ChoiceField(choices=[("a", "A")], widget=forms.Select())
    agree = forms.BooleanField(widget=forms.CheckboxInput())
    pdf = forms.FileField(widget=forms.FileInput(attrs={"accept": "application/pdf"}))
    price = forms.DecimalField(widget=forms.NumberInput(attrs={"step": "0.01"}))
    active = forms.BooleanField(required=False, widget=forms.CheckboxInput())


def render_field(form, name, arg=""):
    tpl = "{% load ui %}{{ field|bs" + (f':"{arg}"' if arg else "") + " }}"
    return Template(tpl).render(Context({"field": form[name]}))


class BsFilterTests(SimpleTestCase):
    def test_text_input_gets_form_control(self):
        html = render_field(UiForm(), "name")
        self.assertIn('class="form-control"', html)

    def test_select_gets_form_select(self):
        html = render_field(UiForm(), "level")
        self.assertIn("form-select", html)
        self.assertNotIn("form-control", html)

    def test_checkbox_gets_form_check_input(self):
        html = render_field(UiForm(), "agree")
        self.assertIn("form-check-input", html)

    def test_errors_add_is_invalid_and_aria(self):
        form = UiForm(data={"name": ""})
        form.is_valid()
        html = render_field(form, "name")
        self.assertIn("is-invalid", html)
        self.assertIn('aria-invalid="true"', html)
        self.assertIn("name_errors", html)

    def test_no_errors_no_invalid_state(self):
        html = render_field(UiForm(), "name")
        self.assertNotIn("is-invalid", html)
        self.assertNotIn("aria-invalid", html)
        self.assertNotIn("aria-describedby", html)

    def test_accept_attribute_survives(self):
        html = render_field(UiForm(), "pdf")
        self.assertIn('accept="application/pdf"', html)
        self.assertIn("form-control", html)

    def test_step_attribute_survives(self):
        html = render_field(UiForm(), "price")
        self.assertIn('step="0.01"', html)
        self.assertIn("form-control", html)

    def test_extra_class_argument_applied(self):
        html = render_field(UiForm(), "name", "form-control-sm")
        self.assertIn("form-control", html)
        self.assertIn("form-control-sm", html)

    def test_attribute_argument_role_switch_applied(self):
        html = render_field(UiForm(), "active", "role=switch")
        self.assertIn('role="switch"', html)
        self.assertIn("form-check-input", html)

    def test_help_text_sets_describedby(self):
        class HelpForm(forms.Form):
            code = forms.CharField(help_text="Like PQ-2026-0001")

        html = render_field(HelpForm(), "code")
        self.assertIn('aria-describedby="id_code_help"', html)


class StepIndexFilterTests(SimpleTestCase):
    def render_index(self, status):
        return Template("{% load ui %}{{ s|step_index }}").render(Context({"s": status}))

    def test_values(self):
        self.assertEqual(self.render_index("PENDING"), "0")
        self.assertEqual(self.render_index("PRINTING"), "1")
        self.assertEqual(self.render_index("READY"), "2")
        self.assertEqual(self.render_index("COMPLETED"), "3")
        self.assertEqual(self.render_index("CANCELLED"), "-1")


class StepsPartialTests(SimpleTestCase):
    def test_printing_marks_first_done_and_second_current(self):
        html = render_to_string("partials/steps.html", {"status": "PRINTING"})
        self.assertIn('<li class="done">Waiting in line</li>', html)
        self.assertIn('<li class="current" aria-current="step">Being printed</li>', html)
        self.assertIn('<li>Picked up</li>', html)

    def test_completed_marks_all_done(self):
        html = render_to_string("partials/steps.html", {"status": "COMPLETED"})
        self.assertEqual(html.count('class="done"'), 4)
        self.assertNotIn("current", html)

    def test_cancelled_shows_cancelled_message(self):
        html = render_to_string("partials/steps.html", {"status": "CANCELLED"})
        self.assertIn("cancelled", html)
        self.assertNotIn("<ol", html)


class MessagesPartialTests(SimpleTestCase):
    def test_error_message_is_danger_alert(self):
        html = render_to_string(
            "partials/messages.html",
            {"messages": [Message(message_constants.ERROR, "Upload failed")]},
        )
        self.assertIn("alert-danger", html)
        self.assertIn('role="alert"', html)
        self.assertIn("Upload failed", html)

    def test_success_message_is_status(self):
        html = render_to_string(
            "partials/messages.html",
            {"messages": [Message(message_constants.SUCCESS, "Profile updated.")]},
        )
        self.assertIn("alert-success", html)
        self.assertIn('role="status"', html)


class BaseNavTests(SimpleTestCase):
    def fake_user(self, role):
        return SimpleNamespace(
            is_authenticated=True,
            full_name="Ana Reyes",
            is_admin=role == "admin",
            is_operator=role == "operator",
            is_student=role == "student",
        )

    def test_logged_out_nav(self):
        html = render_to_string("base.html", {"user": SimpleNamespace(is_authenticated=False)})
        self.assertIn("Track an order", html)
        self.assertIn("Log in", html)
        self.assertIn("Sign up", html)
        self.assertNotIn(">Dashboard<", html)

    def test_student_nav(self):
        html = render_to_string("base.html", {"user": self.fake_user("student")})
        self.assertIn(">Home</a>", html)
        self.assertIn(">New order</a>", html)
        self.assertIn(">My orders</a>", html)
        self.assertNotIn("Track an order", html.split("</nav>")[0])

    def test_operator_nav_has_queue_only(self):
        html = render_to_string("base.html", {"user": self.fake_user("operator")})
        self.assertIn(">Queue</a>", html)
        self.assertNotIn("New order", html)

    def test_admin_nav_never_shows_student_or_operator_copy(self):
        html = render_to_string("base.html", {"user": self.fake_user("admin")})
        self.assertIn(">Overview</a>", html)
        self.assertIn("Shop &amp; rates", html)
        self.assertNotIn("New order", html)
        self.assertNotIn(">Queue<", html)
        self.assertNotIn("Track an order", html.split("</nav>")[0])

    def test_logout_is_post_form_with_csrf_in_nav(self):
        html = render_to_string("base.html", {"user": self.fake_user("student")})
        self.assertIn('<form method="post" action="/accounts/logout/">', html)
        self.assertIn('<button type="submit" class="btn btn-link nav-link">Log out</button>', html)
