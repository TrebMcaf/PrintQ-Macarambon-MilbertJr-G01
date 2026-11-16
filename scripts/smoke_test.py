"""Smoke test: checks that a running PrintQ server actually works end to end.

Usage:
    python scripts/smoke_test.py                      # http://127.0.0.1:8000, accounts from .env
    python scripts/smoke_test.py --base-url URL --signup

It logs in with the seeded admin and operator accounts (SEED_ADMIN_* / SEED_OPERATOR_* in .env or
the environment). With --signup it also registers a throwaway student, so only use that flag on a
test database (CI does). Uses only the Python standard library. Exits 1 on the first failure.
"""
import argparse
import http.cookiejar
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

FAILURES = []


class Client:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path, data=None):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(self.base + path, data=body, headers={"Referer": self.base + "/"})
        try:
            with self.opener.open(req, timeout=15) as resp:
                return resp.status, urllib.parse.urlparse(resp.geturl()).path, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, path, exc.read().decode("utf-8", "replace")

    def csrf(self, path):
        _, _, html = self.request(path)
        m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
        if not m:
            raise RuntimeError(f"no CSRF token on {path}")
        return m.group(1)

    def post_form(self, path, fields, token_from=None):
        return self.request(path, {"csrfmiddlewaretoken": self.csrf(token_from or path), **fields})


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def expect(client, path, status=200, lands_on=None, contains=None, label=None):
    code, final, html = client.request(path)
    name = label or f"GET {path}"
    ok = code == status and (lands_on is None or final.startswith(lands_on)) and (contains is None or contains in html)
    check(name, ok, f"got {code} at {final}")


def wait_for_server(base, seconds=30):
    for _ in range(seconds * 2):
        try:
            urllib.request.urlopen(base + "/accounts/login/", timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def login(base, email, password, expected_path):
    c = Client(base)
    code, final, _ = c.post_form("/accounts/login/", {"username": email, "password": password})
    check(f"log in as {email} lands on {expected_path}", code == 200 and final == expected_path, f"got {code} at {final}")
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--signup", action="store_true", help="also register a throwaway student (test databases only)")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    print(f"Smoke testing {base}")
    if not wait_for_server(base):
        print("  FAIL  server did not respond within 30 seconds")
        return 1

    print("Public pages")
    anon = Client(base)
    expect(anon, "/accounts/login/")
    expect(anon, "/accounts/signup/")
    expect(anon, "/orders/track/")
    expect(anon, "/static/css/app.css", label="GET /static/css/app.css (static files served)")
    expect(anon, "/", lands_on="/accounts/login/", label="GET / sends visitors to login")
    expect(anon, "/manage/", lands_on="/accounts/login/", label="GET /manage/ requires login")
    expect(anon, "/orders/track/?code=PQ-0000-0000", contains="not found", label="tracking an unknown code")

    admin_email, admin_pw = os.environ.get("SEED_ADMIN_EMAIL", "admin@printq.test"), os.environ.get("SEED_ADMIN_PASSWORD")
    if admin_pw:
        print("Admin")
        a = login(base, admin_email, admin_pw, "/manage/")
        for p in ("/manage/students/", "/manage/operators/", "/manage/shop/", "/accounts/profile/"):
            expect(a, p)
        expect(a, "/orders/queue/", status=403, label="admin blocked from operator queue (403)")
    else:
        print("Admin: skipped (SEED_ADMIN_PASSWORD not set)")

    op_email, op_pw = os.environ.get("SEED_OPERATOR_EMAIL", "operator@printq.test"), os.environ.get("SEED_OPERATOR_PASSWORD")
    if op_pw:
        print("Operator")
        o = login(base, op_email, op_pw, "/dashboard/operator/")
        expect(o, "/orders/queue/")
        expect(o, "/manage/", status=403, label="operator blocked from admin pages (403)")
        expect(o, "/dashboard/student/", status=403, label="operator blocked from student dashboard (403)")
    else:
        print("Operator: skipped (SEED_OPERATOR_PASSWORD not set)")

    if args.signup:
        print("Student")
        s = Client(base)
        email = f"smoke-{secrets.token_hex(4)}@printq.test"
        pw = "Smoke-" + secrets.token_urlsafe(10)
        code, final, html = s.post_form("/accounts/signup/", {
            "email": email, "full_name": "Smoke Student", "password1": pw, "password2": pw})
        check("sign up a new student lands on the student dashboard",
              code == 200 and final == "/dashboard/student/" and "Account created" in html, f"got {code} at {final}")
        expect(s, "/orders/new/", contains="Review order")
        expect(s, "/orders/")
        expect(s, "/accounts/profile/", contains=email)
        expect(s, "/manage/", status=403, label="student blocked from admin pages (403)")
        expect(s, "/orders/queue/", status=403, label="student blocked from operator queue (403)")
        # Logout is POST-only; take the token from a page that has the nav's logout form.
        code, final, html = s.post_form("/accounts/logout/", {}, token_from="/accounts/profile/")
        check("log out ends the session", final.startswith("/accounts/login/") and "logged out" in html, f"got {code} at {final}")
        expect(s, "/dashboard/student/", lands_on="/accounts/login/", label="dashboard requires login after logout")

    print()
    if FAILURES:
        print(f"SMOKE TEST FAILED: {len(FAILURES)} check(s) failed")
        return 1
    print("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
