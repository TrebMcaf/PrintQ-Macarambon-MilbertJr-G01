"""Reading a tracking code out of scanned or typed text."""
import re

MAX_SCAN_LENGTH = 500

# PQ-YYYY-NNNN, not glued to other letters or digits. Matches inside a tracking link too.
CODE_RE = re.compile(r"(?<![A-Z0-9])PQ-\d{4}-\d{4}(?!\d)", re.IGNORECASE)


def parse_code(text) -> str | None:
    """Return the tracking code (upper-case) found in `text`, or None.

    `text` can be a bare code, a code typed in lower case, or the tracking link a customer's QR code holds.
    Only the first MAX_SCAN_LENGTH characters are looked at, so a huge scan or query string costs nothing.
    """
    match = CODE_RE.search((text or "")[:MAX_SCAN_LENGTH])
    return match.group(0).upper() if match else None
