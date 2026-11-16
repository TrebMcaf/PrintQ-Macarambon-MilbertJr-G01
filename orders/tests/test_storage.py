import io
import re
import urllib.error
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from pypdf import PdfReader

from orders.storage import (
    LocalBackend,
    StorageError,
    SupabaseBackend,
    build_object_path,
    delete_files,
    get_backend,
)
from orders.tests.utils import LocalStorageTestMixin, make_pdf


class LocalStorageTests(LocalStorageTestMixin, SimpleTestCase):
    def test_upload_then_read_back_identical(self):
        data = make_pdf(1)
        path = "uploads/1/abc.pdf"
        get_backend().upload(SimpleUploadedFile("a.pdf", data), path)

        self.assertTrue((self.media_root / path).is_file())
        with get_backend().open(path) as fh:
            self.assertEqual(fh.read(), data)

    def test_delete_removes_file_and_missing_does_not_raise(self):
        path = "uploads/1/del.pdf"
        get_backend().upload(SimpleUploadedFile("d.pdf", make_pdf(1)), path)
        get_backend().delete([path])
        self.assertFalse((self.media_root / path).exists())

        get_backend().delete([path])  # missing path: no error

    def test_path_traversal_rejected(self):
        with self.assertRaises(StorageError):
            get_backend().upload(SimpleUploadedFile("e.pdf", b"x"), "../evil.pdf")
        with self.assertRaises(StorageError):
            get_backend().open("../../x")

    def test_get_backend_is_local_by_default(self):
        self.assertIsInstance(get_backend(), LocalBackend)

    def test_get_backend_is_supabase_when_configured(self):
        with override_settings(SUPABASE_URL="https://x.supabase.co", SUPABASE_SERVICE_KEY="k"):
            self.assertIsInstance(get_backend(), SupabaseBackend)

    def test_build_object_path_format(self):
        self.assertRegex(build_object_path(7), r"^uploads/7/[0-9a-f]{32}\.pdf$")

    def test_delete_files_swallows_storage_error(self):
        delete_files(["../evil"])  # must not raise


class SupabaseSignedUrlTests(SimpleTestCase):
    def _backend(self):
        return SupabaseBackend("https://x.supabase.co/", "k", "b", 300)

    def test_signed_url_builds_absolute_url(self):
        with patch("orders.storage.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = (
                b'{"signedURL": "/object/sign/b/uploads/1/a.pdf?token=t"}'
            )
            result = self._backend().signed_url("uploads/1/a.pdf")
            self.assertEqual(
                result,
                "https://x.supabase.co/storage/v1/object/sign/b/uploads/1/a.pdf?token=t",
            )

            result = self._backend().signed_url("uploads/1/a.pdf", download_name="a b.pdf")
            self.assertTrue(result.endswith("&download=a%20b.pdf"))

    def test_http_error_raises_storage_error(self):
        err = urllib.error.HTTPError("https://x.supabase.co", 400, "bad", {}, None)
        with patch("orders.storage.urllib.request.urlopen", side_effect=err):
            with self.assertRaises(StorageError):
                self._backend().signed_url("uploads/1/a.pdf")


class MakePdfTests(SimpleTestCase):
    def test_make_pdf_page_count(self):
        reader = PdfReader(io.BytesIO(make_pdf(3)))
        self.assertEqual(len(reader.pages), 3)
