import re

from django import forms
from django.contrib.auth import password_validation
from django.forms import modelformset_factory

from catalog.models import PrintRate, Shop

from .models import Role, User


class SignupForm(forms.ModelForm):
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ["email", "full_name", "phone_number"]  # role is never user-settable

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"])
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        elif p1:
            candidate = User(email=cleaned.get("email", ""), full_name=cleaned.get("full_name", ""))
            try:
                password_validation.validate_password(p1, candidate)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = Role.STUDENT
        user.phone_number = self.cleaned_data.get("phone_number") or None
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["full_name", "email", "phone_number"]
        labels = {"full_name": "Full name", "email": "Email", "phone_number": "Contact number"}

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"])
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_phone_number(self):
        phone = (self.cleaned_data.get("phone_number") or "").strip()
        if phone and not re.fullmatch(r"[0-9+\-() ]{7,20}", phone):
            raise forms.ValidationError("Enter a valid contact number.")
        return phone or None


class OperatorCreateForm(SignupForm):
    """Admin-only: creates an operator account. The role is forced in save()."""

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = Role.OPERATOR
        user.is_staff = False
        if commit:
            user.save()
        return user


class ShopForm(forms.ModelForm):
    class Meta:
        model = Shop
        fields = ["name", "location", "contact_phone", "is_active"]
        labels = {"is_active": "Accepting online orders", "contact_phone": "Contact phone"}


RateFormSet = modelformset_factory(PrintRate, fields=["price_per_page", "is_active"], extra=0, can_delete=False)
