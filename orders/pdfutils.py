import io
from decimal import Decimal, ROUND_UP

from pypdf import PdfReader


class PdfError(Exception):
    pass  # str(exc) is one of: "encrypted", "unreadable", "empty"


def looks_like_pdf(fileobj) -> bool:
    fileobj.seek(0)
    head = fileobj.read(5)
    fileobj.seek(0)
    return head == b"%PDF-"


def count_pdf_pages(fileobj) -> int:
    fileobj.seek(0)
    try:
        data = fileobj.read()
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                try:
                    ok = reader.decrypt("")
                except Exception:
                    ok = 0
                if not ok:
                    raise PdfError("encrypted")
            n = len(reader.pages)
        except PdfError:
            raise
        except Exception as exc:
            raise PdfError("unreadable") from exc
        if n == 0:
            raise PdfError("empty")
        return n
    finally:
        fileobj.seek(0)


def size_in_mb(size_bytes: int) -> Decimal:
    mb = (Decimal(size_bytes) / Decimal(1048576)).quantize(Decimal("0.01"), rounding=ROUND_UP)
    return max(mb, Decimal("0.01"))
