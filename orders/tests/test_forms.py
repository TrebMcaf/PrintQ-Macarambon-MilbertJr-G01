import io
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from pypdf import PdfWriter

from catalog.models import ColorMode, PaperSize, PrintRate
from orders.forms import DocumentForm, DocumentFormSet
from orders.pdfutils import count_pdf_pages, size_in_mb


def _make_pdf(pages=1, user_password=None, owner_password=None) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=612, height=792)
    if user_password is not None:
        w.encrypt(user_password=user_password, owner_password=owner_password)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


class DocumentFormTests(TestCase):
    def setUp(self):
        self.active = PrintRate.objects.create(
            paper_size=PaperSize.A4, color_mode=ColorMode.MONO, price_per_page=Decimal("2.00"), is_active=True
        )
        self.inactive = PrintRate.objects.create(
            paper_size=PaperSize.A4, color_mode=ColorMode.COLOR, price_per_page=Decimal("10.00"), is_active=False
        )

    def _upload(self, name, content):
        return SimpleUploadedFile(name, content, content_type="application/pdf")

    def _doc_form(self, file, rate=None, copies=1, binding="NONE"):
        return DocumentForm(
            data={"rate": (rate or self.active).pk, "copies": copies, "binding_type": binding},
            files={"file": file},
        )

    def _formset_data(self, n_forms, files_by_index):
        data = {
            "docs-TOTAL_FORMS": str(n_forms),
            "docs-INITIAL_FORMS": "0",
            "docs-MIN_NUM_FORMS": "0",
            "docs-MAX_NUM_FORMS": "5",
        }
        for i in range(n_forms):
            data[f"docs-{i}-rate"] = str(self.active.pk)
            data[f"docs-{i}-copies"] = "1"
            data[f"docs-{i}-binding_type"] = "NONE"
        files = {}
        for i, f in files_by_index.items():
            files[f"docs-{i}-file"] = f
        return data, files

    def test_valid_three_page_pdf(self):
        form = self._doc_form(self._upload("three.pdf", _make_pdf(3)))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.page_count, 3)
        self.assertGreaterEqual(form.size_mb, Decimal("0.01"))

    def test_wrong_extension_rejected(self):
        form = self._doc_form(self._upload("notes.txt", _make_pdf(1)))
        self.assertFalse(form.is_valid())
        self.assertIn("Only PDF files are accepted.", form.errors["file"])

    def test_fake_pdf_rejected(self):
        form = self._doc_form(self._upload("fake.pdf", b"hello world"))
        self.assertFalse(form.is_valid())
        self.assertIn("Only PDF files are accepted.", form.errors["file"])

    def test_user_password_pdf_rejected(self):
        form = self._doc_form(self._upload("locked.pdf", _make_pdf(1, user_password="x")))
        self.assertFalse(form.is_valid())
        self.assertTrue(any("password-protected" in e for e in form.errors["file"]))

    def test_owner_password_only_pdf_accepted(self):
        form = self._doc_form(self._upload("owner.pdf", _make_pdf(2, user_password="", owner_password="o")))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.page_count, 2)

    def test_file_too_large_rejected(self):
        with override_settings(MAX_UPLOAD_MB=0.0001):
            form = self._doc_form(self._upload("big.pdf", _make_pdf(1)))
            self.assertFalse(form.is_valid())
            self.assertTrue(any("larger than" in e for e in form.errors["file"]))

    def test_garbage_after_header_rejected(self):
        form = self._doc_form(self._upload("garbage.pdf", b"%PDF-1.4\nnot really a pdf"))
        self.assertFalse(form.is_valid())
        self.assertTrue(any("could not be read" in e for e in form.errors["file"]))

    def test_inactive_rate_rejected(self):
        form = self._doc_form(self._upload("doc.pdf", _make_pdf(1)), rate=self.inactive)
        self.assertFalse(form.is_valid())
        self.assertIn("rate", form.errors)

    def test_formset_no_files_rejected(self):
        data, files = self._formset_data(5, {})
        formset = DocumentFormSet(data, files, prefix="docs")
        self.assertFalse(formset.is_valid())
        self.assertIn("Add at least one document.", formset.non_form_errors())

    def test_formset_one_file_accepted(self):
        data, files = self._formset_data(5, {0: self._upload("one.pdf", _make_pdf(1))})
        formset = DocumentFormSet(data, files, prefix="docs")
        self.assertTrue(formset.is_valid(), formset.errors)
        self.assertEqual(len(formset.filled_forms()), 1)
        self.assertEqual(formset.filled_forms()[0].page_count, 1)

    def test_formset_over_max_rejected(self):
        files = {i: self._upload(f"d{i}.pdf", _make_pdf(1)) for i in range(6)}
        data, files = self._formset_data(6, files)
        formset = DocumentFormSet(data, files, prefix="docs")
        self.assertFalse(formset.is_valid())


class DefaultRateTests(TestCase):
    def test_defaults_to_a4_black_and_white_and_lists_bw_first(self):
        for size in (PaperSize.SHORT, PaperSize.A4, PaperSize.LONG):
            PrintRate.objects.create(paper_size=size, color_mode=ColorMode.COLOR, price_per_page=Decimal("10.00"))
            PrintRate.objects.create(paper_size=size, color_mode=ColorMode.MONO, price_per_page=Decimal("2.00"))
        form = DocumentForm()
        initial = form.fields["rate"].initial
        self.assertEqual((initial.paper_size, initial.color_mode), (PaperSize.A4, ColorMode.MONO))
        html = str(form["rate"])
        self.assertIn(f'value="{initial.pk}" selected', html)
        modes = [r.color_mode for r in form.fields["rate"].queryset if r.paper_size == PaperSize.A4]
        self.assertEqual(modes, [ColorMode.MONO, ColorMode.COLOR])

    def test_falls_back_to_any_black_and_white(self):
        PrintRate.objects.create(paper_size=PaperSize.A4, color_mode=ColorMode.COLOR, price_per_page=Decimal("10.00"))
        bw = PrintRate.objects.create(paper_size=PaperSize.LONG, color_mode=ColorMode.MONO, price_per_page=Decimal("3.00"))
        self.assertEqual(DocumentForm().fields["rate"].initial, bw)


class PdfUtilsTests(TestCase):
    def test_size_in_mb(self):
        self.assertEqual(size_in_mb(1), Decimal("0.01"))
        self.assertEqual(size_in_mb(1048576), Decimal("1.00"))
        self.assertEqual(size_in_mb(1048577), Decimal("1.01"))

    def test_count_pdf_pages_resets_position(self):
        buf = io.BytesIO(_make_pdf(2))
        self.assertEqual(count_pdf_pages(buf), 2)
        self.assertEqual(buf.tell(), 0)
