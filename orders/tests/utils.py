import io
import pathlib
import tempfile

from django.test import override_settings
from pypdf import PdfWriter


def make_pdf(pages: int = 1, encrypt: str | None = None) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=612, height=792)
    if encrypt is not None:
        w.encrypt(encrypt)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


class LocalStorageTestMixin:
    """Mix into a TestCase BEFORE TestCase in the bases. Forces the local backend into a temp dir."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.media_root = pathlib.Path(self._tmp.name)
        self._storage_override = override_settings(
            PRIVATE_MEDIA_ROOT=self.media_root, SUPABASE_URL="", SUPABASE_SERVICE_KEY=""
        )
        self._storage_override.enable()
        self.addCleanup(self._storage_override.disable)
