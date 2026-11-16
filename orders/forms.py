from decimal import Decimal  # noqa: F401

from django import forms
from django.conf import settings

from catalog.models import ColorMode, PaperSize, PrintRate

from .models import BindingType
from .pdfutils import PdfError, count_pdf_pages, looks_like_pdf, size_in_mb


class DocumentForm(forms.Form):
    # accept= makes Android/iOS pickers open straight to PDFs; the server still validates (BR-06).
    file = forms.FileField(label="PDF file", widget=forms.ClearableFileInput(attrs={"accept": ".pdf,application/pdf"}))
    rate = forms.ModelChoiceField(queryset=PrintRate.objects.filter(is_active=True), empty_label=None)
    copies = forms.IntegerField(min_value=1, max_value=settings.MAX_COPIES, initial=1)
    binding_type = forms.ChoiceField(choices=BindingType.choices, initial=BindingType.NONE)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.page_count = None
        self.size_mb = None
        # List black & white before color within each paper size, and preselect A4 black & white
        # (the cheapest common job) so a student who skips the dropdown isn't charged for color.
        rate_field = self.fields["rate"]
        rate_field.queryset = PrintRate.objects.filter(is_active=True).order_by("paper_size", "-color_mode")
        rate_field.initial = (
            rate_field.queryset.filter(paper_size=PaperSize.A4, color_mode=ColorMode.MONO).first()
            or rate_field.queryset.filter(color_mode=ColorMode.MONO).first()
        )

    def has_changed(self):
        # A row counts as filled ONLY if a file was uploaded for it.
        return bool(self.files.get(self.add_prefix("file")))

    def clean_file(self):
        f = self.cleaned_data["file"]
        name = f.name
        if not name.lower().endswith(".pdf"):
            raise forms.ValidationError("Only PDF files are accepted.")
        # Read settings here (not at import) so tests can override.
        if f.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
            raise forms.ValidationError(f"{name} is larger than {settings.MAX_UPLOAD_MB:g} MB.")
        if not looks_like_pdf(f):
            raise forms.ValidationError("Only PDF files are accepted.")
        try:
            self.page_count = count_pdf_pages(f)
        except PdfError as exc:
            if str(exc) == "encrypted":
                raise forms.ValidationError(f"{name} is password-protected.")
            raise forms.ValidationError(f"{name} could not be read as a PDF.")
        self.size_mb = size_in_mb(f.size)
        return f


class BaseDocumentFormSet(forms.BaseFormSet):
    def filled_forms(self):
        return [f for f in self.forms if f.has_changed()]

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        if not self.filled_forms():
            raise forms.ValidationError("Add at least one document.")


DocumentFormSet = forms.formset_factory(
    DocumentForm,
    formset=BaseDocumentFormSet,
    extra=settings.MAX_FILES_PER_ORDER,
    max_num=settings.MAX_FILES_PER_ORDER,
    validate_max=True,
)  # Do NOT pass absolute_max.


class NotesForm(forms.Form):
    notes = forms.CharField(required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 3}))
