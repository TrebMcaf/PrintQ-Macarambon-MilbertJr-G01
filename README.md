# PrintQ

[![CI](https://github.com/TrebMcaf/PrintQ-Macarambon-MilbertJr-G01/actions/workflows/ci.yml/badge.svg)](https://github.com/TrebMcaf/PrintQ-Macarambon-MilbertJr-G01/actions/workflows/ci.yml)

PrintQ is a pre-order and queue system for a campus print shop. Students upload their PDFs, choose
the paper size, color, copies and binding, and get a tracking code. The shop sees every order in a
queue and updates its status until it is ready for pickup. Payment is still done at the counter.

Built with Django 5.2 and PostgreSQL (Supabase) for CSIT327 Information Management 2.

## Features

- Student sign up, login and logout, with passwords hashed by Django
- Three roles, each with its own dashboard: Student, Operator and Admin
- Profile page where users can update their name, email, contact number and password
- PDF upload (up to 5 files, 25 MB each) with page counting and a price review before placing the order
- Order tracking by code, and cancelling while the order is still pending
- Operator queue: Pending, Printing, Ready, Completed, with every status change logged
- Admin pages for managing students, operators, shop details and print rates

## Roles

| Role | What they can do |
|---|---|
| Student | Place orders, track and cancel their own orders, edit their profile |
| Operator | Work through the queue, download files, update order status |
| Admin | Oversee students and operators, add operator accounts, edit shop details and print rates |

Students register through the sign up page. Operator accounts are created by an admin. The first
operator and admin accounts are created by the `seed` command using the emails and passwords in `.env`.

## Setup

1. Create and activate a virtual environment:
   ```
   python -m venv venv
   venv\Scripts\activate
   ```
2. Install the requirements:
   ```
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in the values (see the Supabase section below).
4. Create the tables and the starting data:
   ```
   python manage.py migrate
   python manage.py seed
   ```
5. Run the server and open http://localhost:8000:
   ```
   python manage.py runserver
   ```

## Connecting to Supabase

1. Create a project on supabase.com and keep the database password you set.
2. In the project, click Connect and copy the Session pooler connection string.
3. Paste it into `.env` as `DATABASE_URL=...` and replace the password placeholder with your real
   password. If the password has symbols like `@` or `#`, write them as `%40` and `%23`.
4. Check the connection, then create the tables:
   ```
   python manage.py db_status
   python manage.py migrate
   python manage.py seed
   ```

`db_status` shows which database the app is connected to and whether all six PrintQ tables exist.
If `DATABASE_URL` is empty, the app uses a local SQLite file, but only while `DJANGO_DEBUG=1`.

Never commit `.env`. It is already listed in `.gitignore`.

## File storage

If `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` are set in `.env`, uploaded PDFs are stored in a private
Supabase Storage bucket. Otherwise they are saved in the local `private_uploads/` folder. In both cases
only operators can download the files, and only through the app.

## Running the tests

Before every commit, run these three and make sure they pass:

```
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

With the server running, you can also run the smoke test, which logs in as the admin and operator
from `.env` and checks that every role reaches its own pages:

```
python scripts/smoke_test.py
```

## Continuous integration

Every push and pull request runs `.github/workflows/ci.yml` on GitHub Actions. It runs the full test
suite twice, once on SQLite and once on PostgreSQL 16 (the same database engine as Supabase). It then
creates the tables, seeds the data, starts the server and runs the smoke test against it, including
signing up a student. The badge at the top of this file shows the latest result. If it is red, fix
the problem before pushing anything else.
Deployment to the server is separate and local, it does not use GitHub (see `deploy/DEPLOY.md`).

## Project structure

```
config/      settings and main URLs
accounts/    users, roles, login, profile, admin pages
catalog/     shop and print rates, seed and db_status commands
orders/      orders, uploads, pricing, queue, dashboards
templates/   HTML templates
static/      CSS
```

## Business rules

The rules from the SDD are enforced in two places:

- In the database, with CHECK, UNIQUE and foreign key constraints: unique emails and tracking codes,
  allowed roles and statuses, file size, copies, page count and prices.
- In the code (`orders/services.py`): locking the price when an order is placed, allowing students to
  cancel only pending orders, and saving a log entry for every status change.

## Progress

- [x] Project setup and database schema
- [x] Account feature: registration, login and logout, role dashboards, user profile
- [x] Admin role
- [x] PDF upload, price review and order placement
- [x] Order queue and tracking
- [ ] Search and filters
- [ ] Daily sales report
- [ ] Clean-up of old uploaded files
