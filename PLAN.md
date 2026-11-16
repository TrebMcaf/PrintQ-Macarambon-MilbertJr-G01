# PLAN — PrintQ Phase 3: Document upload & pricing

## 1. Goal

A student can upload 1–5 PDFs, pick paper size / color / copies / binding per file, see server-computed line costs and the total on a review page, then confirm to create the order (F-03). Operators can download each file from the order page (F-04 prerequisite). Works with **no Supabase credentials** (local private storage) and with Supabase Storage when `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` are set.

Out of scope: payment, binding fees (SDD gap — binding stays free; see Risks), auto-purge (Phase 7), HTMX polling.

## 2. Architecture

```
new_order (GET/POST)  ──valid──▶ upload each file to storage ──▶ save draft in session ──▶ redirect confirm_order
     │ DocumentFormSet (1–5 rows) + NotesForm                           (no DB rows yet)
confirm_order (GET)   ──▶ services.quote_items(draft specs) ──▶ review table + total
confirm_order (POST action=confirm) ──▶ services.place_order(...) ──▶ clear draft ──▶ order_detail
confirm_order (POST action=discard) ──▶ storage.delete_files(paths) ──▶ clear draft ──▶ new_order
download_item (GET, operator) ──▶ Supabase: 302 to signed URL │ Local: FileResponse (attachment)
```

Key decisions:
- **Upload before the DB transaction.** Network I/O never runs inside `transaction.atomic()`. If `place_order` raises, the view deletes the uploaded objects (best effort), discards the draft and redirects to new_order with the error.
- **Two-step review instead of client-side price JS.** Page count is only trustworthy server-side (pypdf), so the "live" cost is shown on the review step. The new-order page shows the rate table (price per page) so students can estimate.
- **Pricing logic lives once** in `services.quote_items`; `place_order` reuses `line_subtotal` as today. The review total can differ from the final total only if an operator changes a rate in between — the final order always uses the price at confirm time (BR-09).
- **Storage backend chosen at call time** from settings, so tests can `override_settings`.
- **Local backend files are never web-served**: they live in `PRIVATE_MEDIA_ROOT` (not under STATIC/MEDIA URLs) and are only reachable through `download_item`.
- **Object path** stored in `order_items.file_url`: `uploads/<user_id>/<uuid32>.pdf`. Never the original name (avoids path traversal / collisions).

## 3. File-by-file change list

| # | File | Change | Task |
|---|---|---|---|
| 1 | `config/settings.py` | add `PRIVATE_MEDIA_ROOT = BASE_DIR / "private_uploads"`, `MAX_FILES_PER_ORDER = 5`, `MAX_COPIES = 100` | **T0 (orchestrator, before wave 1)** |
| 2 | `.gitignore` | add `private_uploads/` | **T0** |
| 3 | `orders/storage.py` | **rewrite**: backends + module functions (§4.1). Remove `count_pdf_pages` (moves to pdfutils) | T1 |
| 4 | `orders/tests/test_storage.py`, `orders/tests/utils.py` | **new** | T1 |
| 5 | `orders/pdfutils.py` | **new**: PDF sniffing + page count (§4.2) | T2 |
| 6 | `orders/forms.py` | **new**: `DocumentForm`, `DocumentFormSet`, `NotesForm` (§4.3) | T2 |
| 7 | `orders/tests/test_forms.py` | **new** (includes pdfutils tests) | T2 |
| 8 | `orders/services.py` | **add** `QuoteLine`, `quote_items` (§4.4); no other changes | T3 |
| 9 | `orders/tests/test_quote.py` | **new** | T3 |
| 10 | `orders/views.py` | replace `new_order`; add `confirm_order`, `download_item` (§4.5) | T4 |
| 11 | `orders/urls.py` | add `confirm/` → `confirm_order`, `items/<int:item_id>/download/` → `download_item` | T4 |
| 12 | `templates/orders/new_order.html` | rewrite: rate table + formset + notes, `enctype="multipart/form-data"` | T4 |
| 13 | `templates/orders/confirm_order.html` | **new**: review table, total, Confirm / Discard buttons | T4 |
| 14 | `templates/orders/order_detail.html` | add a "Download" link per item, operators only | T4 |
| 15 | `orders/tests/test_upload_views.py` | **new** | T4 |
| 16 | `README.md` | add "Storage" section (local vs Supabase, env vars) | T4 |

T0 (settings + .gitignore) is done by the orchestrator first. T1, T2, T3 then touch disjoint files → **parallel (wave 1)**. T4 depends on their interfaces → **wave 2**.

Shared test helper (owned by T1, file `orders/tests/utils.py`): `LocalStorageTestMixin` — in `setUp` creates a `tempfile.TemporaryDirectory`, applies `override_settings(PRIVATE_MEDIA_ROOT=<tmp>, SUPABASE_URL="", SUPABASE_SERVICE_KEY="")` via `self.settings_override.enable()`, registers cleanup (`disable()` + tmp cleanup) with `self.addCleanup`; exposes `self.media_root: Path`. Also `make_pdf(pages: int = 1, encrypt: str | None = None) -> bytes` using `pypdf.PdfWriter`, `add_blank_page(width=612, height=792)`, optional `writer.encrypt(encrypt)`. Only T4 imports `orders/tests/utils.py`. T2 runs in parallel with T1, so T2 defines its own private `_make_pdf` inside `test_forms.py` and does not import `utils.py`.

## 4. Interfaces

### 4.1 `orders/storage.py` (T1)

```python
class StorageError(Exception): ...

class LocalBackend:
    def __init__(self, root: Path): ...
    def upload(self, fileobj, object_path: str) -> None      # writes root/object_path, creating dirs; streams fileobj.chunks() if present else read()
    def open(self, object_path: str) -> BinaryIO             # open for reading 'rb'; raises StorageError if missing
    def delete(self, object_paths: list[str]) -> None        # missing files ignored
    def signed_url(self, object_path: str, download_name: str | None = None) -> None  # always None (local has no URLs)

class SupabaseBackend:
    def __init__(self, url: str, service_key: str, bucket: str, ttl: int): ...
    def upload(self, fileobj, object_path: str) -> None      # POST {url}/storage/v1/object/{bucket}/{path}, headers Authorization: Bearer key, Content-Type: application/pdf, x-upsert: false
    def open(self, object_path: str) -> BinaryIO             # raise StorageError("Supabase files are served via signed URLs") — never reached by views
    def delete(self, object_paths: list[str]) -> None        # DELETE {url}/storage/v1/object/{bucket}, JSON {"prefixes": paths}; no-op on empty list
    def signed_url(self, object_path: str, download_name: str | None = None) -> str
        # POST {url}/storage/v1/object/sign/{bucket}/{path}, JSON {"expiresIn": ttl}; response JSON "signedURL"
        # (relative, starts with "/object/sign/...") → base = f"{url}/storage/v1{signedURL}".
        # If download_name: append f"&download={urllib.parse.quote(download_name)}" (signedURL already has "?token=")
        # so the browser downloads instead of rendering inline.

def get_backend() -> LocalBackend | SupabaseBackend
    # SupabaseBackend if settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY are both non-empty,
    # else LocalBackend(Path(settings.PRIVATE_MEDIA_ROOT)). Read settings on every call (no caching).

def build_object_path(user_id: int) -> str               # f"uploads/{user_id}/{uuid.uuid4().hex}.pdf"
def upload_file(fileobj, object_path: str) -> str        # get_backend().upload(...); returns object_path
def delete_files(object_paths: list[str]) -> None        # get_backend().delete(...); logs and swallows StorageError/OSError (best-effort cleanup)
```

Rules: HTTP via `urllib.request` only (no new dependencies). Any non-2xx or `URLError` → `StorageError` with status code in the message. `url` has any trailing `/` stripped. `LocalBackend` must reject object paths that resolve outside `root` (`StorageError`) — guards against `..`.

### 4.2 `orders/pdfutils.py` (T2)

```python
class PdfError(Exception): ...
def looks_like_pdf(fileobj) -> bool        # first 5 bytes == b"%PDF-"; restores position with seek(0)
def count_pdf_pages(fileobj) -> int        # data = fileobj.read(); reader = pypdf.PdfReader(io.BytesIO(data)) (≤25 MB, safe);
                                           # always fileobj.seek(0) before and after (use try/finally).
                                           # If reader.is_encrypted: try reader.decrypt("") (owner-password-only PDFs
                                           #   open with an empty user password); if that returns 0 / raises →
                                           #   raise PdfError("encrypted").
                                           # Any other exception (catch Exception broadly, incl. RecursionError/ValueError)
                                           #   → PdfError("unreadable"). 0 pages → PdfError("empty").
def size_in_mb(size_bytes: int) -> Decimal # bytes / 1048576, quantize to 0.01 with ROUND_UP; minimum Decimal("0.01")
```

### 4.3 `orders/forms.py` (T2)

```python
class DocumentForm(forms.Form):
    file = forms.FileField()
    rate = forms.ModelChoiceField(queryset=PrintRate.objects.filter(is_active=True), empty_label=None)
    copies = forms.IntegerField(min_value=1, max_value=settings.MAX_COPIES, initial=1)
    binding_type = forms.ChoiceField(choices=BindingType.choices, initial=BindingType.NONE)

    page_count: int | None = None     # set in clean_file
    size_mb: Decimal | None = None    # set in clean_file

    def has_changed(self):
        # A row counts as filled ONLY if a file was uploaded for it. (rate has empty_label=None, so the
        # browser always posts a rate; without this override every blank row would fail "file required".)
        return bool(self.files.get(self.add_prefix("file")))

    def clean_file(self):
        # f = self.cleaned_data["file"]; name = f.name. In this order, each a forms.ValidationError:
        # 1. name.lower().endswith(".pdf")                    else "Only PDF files are accepted."
        # 2. f.size <= settings.MAX_UPLOAD_MB * 1024 * 1024   else f"{name} is larger than {settings.MAX_UPLOAD_MB:g} MB."  (BR-06)
        #    (read settings inside the method so override_settings works)
        # 3. looks_like_pdf(f)                                else "Only PDF files are accepted."
        # 4. self.page_count = count_pdf_pages(f)
        #    PdfError("encrypted") → f"{name} is password-protected."; other PdfError → f"{name} could not be read as a PDF."
        # 5. self.size_mb = size_in_mb(f.size)
        # return f

class BaseDocumentFormSet(forms.BaseFormSet):
    def clean(self):
        # super().clean(); if any(self.errors): return
        # if not self.filled_forms(): raise forms.ValidationError("Add at least one document.")
    def filled_forms(self) -> list[DocumentForm]:
        # [f for f in self.forms if f.has_changed()]   — the view iterates ONLY these.
        # Unchanged extra forms skip validation (Django sets empty_permitted on extra forms) and have cleaned_data == {}.

DocumentFormSet = forms.formset_factory(
    DocumentForm, formset=BaseDocumentFormSet,
    extra=settings.MAX_FILES_PER_ORDER,       # renders exactly 5 rows; no JS needed
    max_num=settings.MAX_FILES_PER_ORDER, validate_max=True)
    # Do NOT pass absolute_max: it would clamp a 6-form POST to 5 and silently drop the 6th file.

class NotesForm(forms.Form):
    notes = forms.CharField(required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 3}))
```

Notes:
- `rate` queryset is evaluated per form instance by ModelChoiceField; do not cache at import.
- `MAX_COPIES` and `MAX_FILES_PER_ORDER` are read at import time. Tests must NOT override these two settings.
- Formset prefix is always `"docs"`: views construct `DocumentFormSet(request.POST, request.FILES, prefix="docs")` / `DocumentFormSet(prefix="docs")`.

### 4.4 `orders/services.py` additions (T3)

```python
@dataclass
class QuoteLine:
    spec: ItemSpec
    rate: PrintRate
    unit_price: Decimal
    subtotal: Decimal

def quote_items(items: list[ItemSpec]) -> tuple[list[QuoteLine], Decimal]:
    # Looks up rates with PrintRate.objects.in_bulk; raises ValidationError("Selected print option is no longer available.")
    # if a rate is missing or inactive; subtotal = line_subtotal(rate.price_per_page, spec.page_count, spec.copies).
    # Returns (lines in input order, total). Read-only: writes nothing.
```

`ItemSpec` and `place_order` are unchanged.

### 4.5 `orders/views.py` (T4)

Session key `DRAFT_KEY = "printq_draft"`, value:
```python
{"notes": str, "items": [{"rate_id": int, "file_name": str, "file_url": str,
                          "file_size_mb": str, "page_count": int, "copies": int, "binding_type": str}, ...]}
```
(`file_size_mb` stored as str for JSON; rebuilt with `Decimal(...)`.)

```python
def _safe_file_name(raw: str) -> str:
    # name = get_valid_filename(os.path.basename(raw)) (catch SuspiciousFileOperation → "");
    # if not name: return "document.pdf"; stem, ext = os.path.splitext(name); ext = ext or ".pdf"
    # return stem[: 255 - len(ext)] + ext

@student_required
def new_order(request):
    # Draft already in session (GET or POST) → redirect confirm_order (never overwrite a draft; avoids orphans).
    # GET: render formset (prefix "docs") + NotesForm + active rates (PrintRate.objects.filter(is_active=True)).
    # POST: formset = DocumentFormSet(request.POST, request.FILES, prefix="docs"); notes_form = NotesForm(request.POST).
    #   Both valid → for form in formset.filled_forms():
    #       f = form.cleaned_data["file"]; path = storage.build_object_path(request.user.pk); storage.upload_file(f, path)
    #       append {"rate_id": form.cleaned_data["rate"].pk, "file_name": _safe_file_name(f.name), "file_url": path,
    #               "file_size_mb": str(form.size_mb), "page_count": form.page_count,
    #               "copies": form.cleaned_data["copies"], "binding_type": form.cleaned_data["binding_type"]}
    #   StorageError on any upload → storage.delete_files(already uploaded paths), messages.error(
    #       "Upload failed. Please try again."), re-render (HTTP 200).
    #   Save draft in request.session[DRAFT_KEY], redirect confirm_order.
    #   Invalid → re-render with errors (HTTP 200).

@student_required
def confirm_order(request):
    # No draft → redirect new_order.
    # GET: quote_items(specs) → render confirm_order.html with lines, total, notes and the text
    #      "Prices are final when you confirm." and "Binding is free."
    #      If quote raises ValidationError → delete files, pop draft, messages.error(str), redirect new_order.
    # POST action=discard: pop draft, delete files, messages.info("Draft discarded."), redirect new_order.
    # POST action=confirm:
    #      draft = request.session.pop(DRAFT_KEY)   # pop BEFORE place_order: a double-submit finds no draft
    #      try: order = services.place_order(request.user, specs, draft["notes"])
    #      except (ValidationError, PermissionDenied) as e: delete files, messages.error, redirect new_order
    #      except Exception: delete files; raise
    #      success → messages.success(f"Order {order.tracking_code} placed."), redirect order_detail(order.pk)
    # Any other action → HttpResponseBadRequest.

@operator_required
def download_item(request, item_id):
    # item = get_object_or_404(OrderItem, pk=item_id); backend = storage.get_backend()
    # try:
    #     url = backend.signed_url(item.file_url, download_name=item.file_name)
    #     if url: return redirect(url)
    #     return FileResponse(backend.open(item.file_url), as_attachment=True, filename=item.file_name,
    #                         content_type="application/pdf")
    # except StorageError: raise Http404
```

Template details (T4):
- `new_order.html`: `<form method="post" enctype="multipart/form-data">`, `{% csrf_token %}`, `{{ formset.management_form }}`, `{{ formset.non_form_errors }}`, one fieldset per form showing its errors, the notes form, a rate table (paper, color, ₱/page), and "Binding is free." Submit button "Review order".
- `confirm_order.html`: table (file, paper/color, pages, copies, binding, unit price, subtotal), total row, notes, two buttons in one POST form: `name="action" value="confirm"` ("Place order") and `value="discard"` ("Discard").
- `order_detail.html`: inside the existing File cell, after the name: `{% if user.is_operator %} <a href="{% url 'orders:download_item' i.pk %}">Download</a>{% endif %}`. Column count unchanged.

## 5. Risks

| Risk | Mitigation |
|---|---|
| Orphaned files when a student abandons a draft | Accepted for now; Phase 7 purge command will delete `uploads/` objects not referenced by any `order_items.file_url` and older than 24h. Noted in README. |
| Rate changes between review and confirm | Final order uses price at confirm (BR-09); confirm page says "Prices are final when you confirm." |
| Large uploads held in memory | `FILE_UPLOAD_MAX_MEMORY_SIZE = 5MB` already makes Django spool to temp files; `DATA_UPLOAD_MAX_MEMORY_SIZE` does not apply to file parts. Size check happens after upload completes (Django limitation) — acceptable for 25 MB. |
| Malicious PDFs | Server never renders/executes the PDF; pypdf only parses structure. Files are not web-served directly; download is `attachment`. |
| Path traversal | Object paths are server-generated UUIDs; LocalBackend refuses paths outside root. |
| Binding is unpriced (SDD gap) | Out of scope; show "Binding: free" note on the new-order page. Flag in summary. |
| Supabase REST shape | Signed URL response is `{"signedURL": "/object/sign/..."}`; covered with a mocked `urlopen` test, not a live call. |
| Session size | ≤5 items × small dict — fine for DB-backed sessions. |
| Total request size (5 × 25 MB) | Accepted for a single shop; no cumulative cap. |
| Operators can download any student's file | Intended: single-shop deployment, all operators staff the same counter. |
| Concurrent confirm from two tabs | Draft is popped before `place_order`; a near-simultaneous second request may still race. Accepted. |
| Owner-password-only PDFs | Opened via `decrypt("")`; only PDFs needing a real user password are rejected. |

## 6. Acceptance criteria

All must pass from the project root with global `python`:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run     # no model changes in this phase
python manage.py test                                 # all existing 28 + new tests green
```

Required new tests (names may vary; behavior must be covered):

**test_storage.py** (use `tempfile.TemporaryDirectory` + `override_settings(PRIVATE_MEDIA_ROOT=..., SUPABASE_URL="", SUPABASE_SERVICE_KEY="")`)
- local upload → file exists with same bytes; `open` returns them; `delete` removes; deleting missing path doesn't raise
- path with `..` escaping root → `StorageError`
- `get_backend()` returns Supabase backend when both settings set, local otherwise
- Supabase `signed_url` builds `{url}/storage/v1/object/sign/...?token=...` from a mocked `urlopen` (`unittest.mock.patch("orders.storage.urllib.request.urlopen")`); with `download_name="a b.pdf"` the URL ends with `&download=a%20b.pdf`; HTTPError → `StorageError`
- `build_object_path(7)` matches `^uploads/7/[0-9a-f]{32}\.pdf$`

**test_forms.py** (build PDFs in-memory with `pypdf.PdfWriter().add_blank_page(...)`)
- 3-page PDF → valid, `page_count == 3`, `size_mb >= 0.01`
- `.txt` name → error; `.pdf` name with non-PDF bytes → error
- encrypted PDF (`writer.encrypt("x")`) → "password-protected" error
- oversize: `override_settings(MAX_UPLOAD_MB=0.0001)` (≈105 bytes) with a 1-page PDF (~430 bytes) → "larger than" error
- inactive rate not selectable
- formset (always POST every row's `rate`, `copies`, `binding_type` like a browser would): all 5 rows without files → "Add at least one document."; 1 file + 4 blank rows → valid, `len(filled_forms()) == 1`; TOTAL_FORMS=6 with 6 files → invalid (max exceeded)
- owner-password-only PDF (`writer.encrypt(user_password="", owner_password="o")`) → valid with correct page count; user-password PDF (`writer.encrypt("x")`) → "password-protected"
- `size_in_mb(1)` == `0.01`, `size_in_mb(1048576)` == `1.00`, `size_in_mb(1048577)` == `1.01`

**test_quote.py**
- two lines → correct subtotals/total, input order preserved, no DB writes (Order count unchanged)
- inactive rate → `ValidationError`

**test_upload_views.py** (use `LocalStorageTestMixin` from `orders/tests/utils.py`; streaming bodies via `b"".join(resp.streaming_content)`)
- POST new_order while a draft exists → redirect to confirm, no new file on disk
- student GET new_order → 200 and lists active rates
- POST 1 valid PDF → 302 to confirm; file exists on disk; draft in session; no Order yet
- GET confirm → shows file name, page count, total
- POST confirm → Order created with correct total and item `file_url` == uploaded path; draft cleared; redirect to order_detail
- POST discard → file deleted from disk; no Order
- POST invalid file → 200 with error, nothing on disk
- operator GET download → 200, `Content-Disposition` contains `attachment`, body == uploaded bytes
- student GET download → 403
- operator sees "Download" link on order_detail; student does not
- confirm when rate deactivated after upload → redirect new_order, files deleted, no Order
