# PrintQ

Pre-order and queue system for a campus print shop (Django 5.2).

## Setup
1. `pip install -r requirements.txt` (a virtualenv is recommended).
2. Copy `.env.example` to `.env` and fill in values (see **Connecting to Supabase**).
3. `python manage.py migrate`
4. `python manage.py seed` (shop, rate matrix, operator account; safe to re-run)
5. `python manage.py runserver`

## Connecting to Supabase
The final setup uses Supabase PostgreSQL. SQLite is only a fallback while `DJANGO_DEBUG=1`, and
the app refuses to start without `DATABASE_URL` when debug is off.

1. Create a project at supabase.com and note the **database password** you set.
2. In the project, click **Connect** → copy the **Session pooler** URI
   (`postgresql://postgres.<ref>:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres`).
   Use the session pooler unless your network has IPv6; the direct `db.<ref>.supabase.co` host is IPv6-only.
3. Put it in `.env` as `DATABASE_URL=...` with your real password. URL-encode special characters
   in the password (e.g. `@` → `%40`, `#` → `%23`). Never commit `.env`.
4. Run:
   ```
   python manage.py db_status     # shows host + "Connected: PostgreSQL ..."
   python manage.py migrate       # creates users, shops, print_rates, orders, order_items, order_status_logs
   python manage.py seed
   python manage.py db_status     # should now say "PrintQ tables present: 6/6"
   ```
5. The tables appear in Supabase → **Table Editor**, with the SDD's CHECK / UNIQUE / FK constraints.

Notes: the transaction pooler (port 6543) also works — settings switch off persistent connections,
server-side cursors and prepared statements for it automatically. SSL is always required.

## Tests
`python manage.py test` — uses SQLite when `DATABASE_URL` is unset. To run the suite against Supabase,
Django needs permission to create a temporary `test_postgres` database; use the session pooler URL.

## Project structure
- `config/` settings and root URLs (`home` redirects by role).
- `accounts/` custom email-login `User`, role decorators, signup/login/logout.
- `catalog/` `Shop`, `PrintRate`, `seed` command.
- `orders/` models, `services.py` (all business logic), `storage.py` (local/Supabase file storage), `pdfutils.py` (PDF checks), views, tests.
- `templates/`, `static/css/app.css` plain HTML/CSS, no JS frameworks.

## Which layer enforces which SDD rule
- **Database (CHECK / UNIQUE / FK RESTRICT):** BR-01 unique email, BR-02 role values, BR-03 unique tracking code, BR-04 status values, BR-05 total >= 0, BR-06 file size 0-25 MB, BR-07 copies >= 1, BR-08 pages >= 1, BR-10 price >= 0, BR-11 rates referenced by items cannot be deleted, BR-14 shop FK.
- **Services (`orders/services.py`):** BR-09 price snapshot, BR-12 students cancel only their own PENDING orders, BR-13 status log on every change, state-machine transitions, operator-only advancing.
- **Views/decorators:** role-based access (students vs operators), owner-only order visibility. Views never set `order.status` directly.

## Storage
- Uploaded PDFs go to Supabase Storage when `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` are set. The bucket is private (`SUPABASE_BUCKET`), and operators get signed download links valid for `SIGNED_URL_TTL_SECONDS`.
- Otherwise files are saved under `private_uploads/`. That folder is never served directly; only operators can download files, through the app.
- Note: abandoned drafts can leave orphaned files. A purge command is planned for Phase 7.

## Roadmap
0. Setup
1. Schema
2. Auth
3. Upload + pricing
4. Queue
5. Tracking / cancel
6. Search + daily report
7. Rate editor + purge
8. QA
