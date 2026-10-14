"""
Automated tests for Phase 0 (security) and Phase 1 (payments).

Run them any time with:      python -m unittest discover -s tests -v

They use an in-memory fake instead of Supabase and Telegram (see tests/fakes.py),
so they are safe: they never touch your real database, never send a message and
need no passwords. Every time you change payment or access code, run them first.
"""
import io
import logging
import os
import re
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

os.environ.update(
    SUPABASE_URL="https://example.supabase.co",
    SUPABASE_ANON_KEY="anon-key",
    SUPABASE_SERVICE_ROLE_KEY="service-key",
    SECRET_KEY="test-secret-key-test-secret-key-0000",
    FLASK_ENV="production",
    UPI_ID="sangam@fam",
    UPI_PAYEE_NAME="Sangam Study",
    TG_TOKEN="123456:TEST-TOKEN",
    TG_ADMIN_CHAT_ID="555",
)
for _name in ("TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY", "TG_WEBHOOK_SECRET", "UPI_STATIC_QR_FILE"):
    os.environ.pop(_name, None)

import fakes  # noqa: E402

DB = fakes.FakeDB()
fakes.install(DB)

from app import create_app  # noqa: E402
from app.extensions import cache  # noqa: E402
from app.shared import access, payments_core as pay, security  # noqa: E402

APP = create_app()
APP.config["PROPAGATE_EXCEPTIONS"] = False
logging.disable(logging.CRITICAL)      # keep test output clean (errors are asserted on, not read)

PLAN_ID = "11111111-1111-4111-8111-111111111111"
ADMIN_TG_ID = 555


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def seed():
    DB.reset()
    security.rl_reset_all()
    with APP.app_context():
        cache.clear()
    DB.insert("plans", {
        "id": PLAN_ID, "code": "season_pass_2027", "name": "Season Pass",
        "description": "Everything premium.", "price_inr": 299,
        "valid_until": fakes.ts(60 * 24 * 365), "valid_days": None, "stream_id": None,
        "features": ["All premium mock tests", "All premium practice sets"],
        "is_active": True, "display_order": 1,
    })
    ids = {}
    for username, role in (("alice", 1), ("bob", 1), ("owner", 2)):
        uid = DB.add_auth_user(username, "secret123")
        DB.insert("profiles", {"id": uid, "username": username, "full_name": username.title() + " Test", "role_id": role})
        ids[username] = uid
    return ids


def make_client(user_id=None):
    client = APP.test_client()
    if user_id:
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
    return client


def post(client, path, **kwargs):
    """A normal same-site browser POST (it carries an Origin header)."""
    headers = kwargs.pop("headers", {})
    headers.setdefault("Origin", "http://localhost")
    return client.post(path, headers=headers, **kwargs)


def png_bytes(size=(2000, 1500), mode="RGB"):
    from PIL import Image
    buf = io.BytesIO()
    img = Image.new(mode, size, (30, 120, 200) if mode == "RGB" else (30, 120, 200, 128))
    img.save(buf, format="PNG")
    return buf.getvalue()


def give_subscription(user_id, minutes_valid=60 * 24 * 30, status="active"):
    return DB.insert("subscriptions", {
        "user_id": user_id, "plan_id": PLAN_ID, "stream_id": None, "order_id": None,
        "source": "admin", "status": status, "starts_at": fakes.ts(-10), "expires_at": fakes.ts(minutes_valid),
    })


class FakeTelegram:
    """Records every Telegram call instead of sending it."""

    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, method, *, json_body=None, data=None, files=None, timeout=10):
        self.calls.append({"method": method, "json": json_body, "data": data, "files": files})
        if self.fail:
            return None
        if method == "sendPhoto":
            return {"message_id": 77, "photo": [{"file_id": "small"}, {"file_id": "FILEID"}]}
        if method in ("sendMessage",):
            return {"message_id": 78}
        return True

    def of(self, method):
        return [c for c in self.calls if c["method"] == method]


class Base(unittest.TestCase):
    def setUp(self):
        self.ids = seed()
        self.tg = FakeTelegram()
        patches = [
            mock.patch.object(pay, "tg_call", self.tg),
            mock.patch.object(pay, "httpx", object()),     # makes tg_enabled() true without the real library
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    # a ready-made order, created the same way the website does it
    def new_order(self, who="alice", submit_utr=None):
        client = make_client(self.ids[who])
        resp = post(client, "/pay/start", data={"plan_code": "season_pass_2027"})
        self.assertEqual(resp.status_code, 302, resp.data[:300])
        order_id = resp.headers["Location"].rsplit("/", 1)[-1]
        if submit_utr:
            r = post(client, f"/pay/order/{order_id}/submit", data={"utr": submit_utr})
            self.assertEqual(r.status_code, 200, r.data)
        return client, order_id

    def order(self, order_id):
        return next(o for o in DB.rows("orders") if o["id"] == order_id)


# ======================================================================
# PHASE 0: security
# ======================================================================

class SecurityTests(Base):
    def test_post_without_origin_is_blocked(self):
        r = APP.test_client().post("/auth/login", data={"username": "alice", "password": "secret123"})
        self.assertEqual(r.status_code, 403)
        self.assertIn(b"could not be verified", r.data)

    def test_post_from_another_website_is_blocked(self):
        r = APP.test_client().post("/auth/login", data={"username": "alice", "password": "secret123"},
                                   headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_same_site_post_passes_the_guard(self):
        r = post(APP.test_client(), "/auth/login", data={"username": "alice", "password": "wrong"})
        self.assertEqual(r.status_code, 401)           # reached the login code, which said "wrong password"

    def test_referer_is_accepted_when_origin_is_missing(self):
        r = APP.test_client().post("/auth/login", data={"username": "alice", "password": "wrong"},
                                   headers={"Referer": "http://localhost/auth/login"})
        self.assertEqual(r.status_code, 401)

    def test_json_posts_are_guarded_too_and_answer_in_json(self):
        r = APP.test_client().post("/pay/start", json={"x": 1})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(r.get_json()["ok"])

    def test_webhook_is_exempt_from_origin_check_but_needs_the_secret(self):
        c = APP.test_client()
        self.assertEqual(c.post("/tg-webhook", json={}).status_code, 403)
        ok = c.post("/tg-webhook", json={}, headers={"X-Telegram-Bot-Api-Secret-Token": pay.tg_webhook_secret()})
        self.assertEqual(ok.status_code, 200)

    def test_security_headers_and_no_store(self):
        r = APP.test_client().get("/ping")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertIn("no-store", r.headers["Cache-Control"])
        self.assertIn("Referrer-Policy", r.headers)

    def test_hsts_only_on_https(self):
        c = APP.test_client()
        self.assertNotIn("Strict-Transport-Security", c.get("/ping").headers)
        self.assertIn("Strict-Transport-Security", c.get("/ping", base_url="https://localhost").headers)

    def test_404_page_is_friendly_and_leaks_nothing(self):
        r = APP.test_client().get("/definitely/not/here")
        self.assertEqual(r.status_code, 404)
        self.assertIn(b"Page not found", r.data)
        self.assertNotIn(b"Traceback", r.data)

    def test_errors_are_json_for_javascript_callers(self):
        r = APP.test_client().get("/pay/order/not-a-real-id/status.json", headers={"Accept": "application/json"})
        self.assertIn(r.status_code, (401, 404))
        self.assertFalse(r.get_json()["ok"])

    def test_unexpected_crash_shows_generic_500_without_details(self):
        with mock.patch.object(pay, "payments_ready", side_effect=RuntimeError("SECRET-DB-PASSWORD-123")):
            c = make_client(self.ids["alice"])
            r = post(c, "/pay/start", data={"plan_code": "season_pass_2027"})
        self.assertEqual(r.status_code, 500)
        self.assertNotIn(b"SECRET-DB-PASSWORD-123", r.data)
        self.assertIn(b"Something went wrong", r.data)

    def test_ping_db_touches_the_database(self):
        DB.insert("roles", {"id": 1, "name": "student"})
        self.assertEqual(APP.test_client().get("/ping-db").status_code, 200)
        DB.fail_tables.add("roles")
        self.assertEqual(APP.test_client().get("/ping-db").status_code, 503)

    def test_rate_limiter_blocks_then_recovers(self):
        for _ in range(3):
            self.assertEqual(security.rl_hit("t", "k", 3, 60), 0)
        self.assertGreater(security.rl_hit("t", "k", 3, 60), 0)
        self.assertEqual(security.rl_blocked("t", "other", 3, 60), 0)
        security.rl_clear("t", "k")
        self.assertEqual(security.rl_hit("t", "k", 3, 60), 0)

    def test_blocked_check_does_not_record(self):
        for _ in range(10):
            security.rl_blocked("peek", "k", 1, 60)
        self.assertEqual(security.rl_hit("peek", "k", 1, 60), 0)


class ClientIpTests(Base):
    def ip_for(self, headers=None, env=None):
        with mock.patch.dict(os.environ, env or {}):
            with APP.test_request_context("/", headers=headers or {}, environ_base={"REMOTE_ADDR": "10.0.0.9"}):
                return security.client_ip()

    def test_off_render_headers_that_anyone_could_fake_are_ignored(self):
        self.assertEqual(self.ip_for({"True-Client-IP": "1.2.3.4", "CF-Connecting-IP": "5.6.7.8"}), "10.0.0.9")

    def test_on_render_the_cloudflare_headers_are_used(self):
        env = {"RENDER": "true"}
        self.assertEqual(self.ip_for({"True-Client-IP": "1.2.3.4", "CF-Connecting-IP": "5.6.7.8"}, env), "1.2.3.4")
        self.assertEqual(self.ip_for({"CF-Connecting-IP": "5.6.7.8"}, env), "5.6.7.8")
        self.assertEqual(self.ip_for({}, env), "10.0.0.9")

    def test_a_faked_forwarded_for_list_does_not_fool_us_on_render(self):
        env = {"RENDER": "true"}
        headers = {"X-Forwarded-For": "9.9.9.9, 8.8.8.8", "True-Client-IP": "1.2.3.4"}
        self.assertEqual(self.ip_for(headers, env), "1.2.3.4")

    def test_explicit_header_setting_wins(self):
        env = {"RENDER": "true", "CLIENT_IP_HEADER": "X-Real-IP"}
        self.assertEqual(self.ip_for({"X-Real-IP": "7.7.7.7, 6.6.6.6", "True-Client-IP": "1.2.3.4"}, env), "7.7.7.7")

    def test_different_visitors_are_not_lumped_together_by_the_login_throttle(self):
        with mock.patch.dict(os.environ, {"RENDER": "true"}):
            c = APP.test_client()
            attacker = {"True-Client-IP": "1.1.1.1"}
            codes = [post(c, "/auth/login", data={"username": "alice", "password": "x"}, headers=dict(attacker)).status_code
                     for _ in range(9)]
            self.assertEqual(codes[-1], 429)
            other = post(c, "/auth/login", data={"username": "alice", "password": "secret123"},
                         headers={"True-Client-IP": "2.2.2.2"})
            self.assertEqual(other.status_code, 302)               # a different student is unaffected

    def test_network_check_page_is_admin_only_and_shows_the_resolved_ip(self):
        self.assertEqual(APP.test_client().get("/admin/network-check").status_code, 403)
        self.assertEqual(make_client(self.ids["alice"]).get("/admin/network-check").status_code, 403)
        page = make_client(self.ids["owner"]).get("/admin/network-check", headers={"X-Forwarded-For": "3.3.3.3"})
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Resolved client IP", page.data)
        self.assertIn(b"3.3.3.3", page.data)          # ProxyFix trusts one proxy hop (off Render)


class AuthTests(Base):
    def test_login_works_and_session_holds_no_access_token(self):
        c = APP.test_client()
        r = post(c, "/auth/login", data={"username": "Alice", "password": "secret123"})
        self.assertEqual(r.status_code, 302)
        with c.session_transaction() as s:
            self.assertEqual(s["user_id"], self.ids["alice"])
            self.assertNotIn("access_token", s)

    def test_session_cookie_has_security_flags(self):
        r = post(APP.test_client(), "/auth/login", data={"username": "alice", "password": "secret123"},
                 base_url="https://localhost")
        cookie = r.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Secure", cookie)

    def test_repeated_wrong_passwords_get_throttled(self):
        c = APP.test_client()
        codes = [post(c, "/auth/login", data={"username": "alice", "password": "nope"}).status_code for _ in range(9)]
        self.assertEqual(codes[:8], [401] * 8)
        self.assertEqual(codes[8], 429)
        # another account from the same network is not locked by that
        self.assertEqual(post(c, "/auth/login", data={"username": "bob", "password": "nope"}).status_code, 401)

    def test_correct_login_resets_the_failure_counter(self):
        c = APP.test_client()
        for _ in range(5):
            post(c, "/auth/login", data={"username": "alice", "password": "nope"})
        self.assertEqual(post(c, "/auth/login", data={"username": "alice", "password": "secret123"}).status_code, 302)
        for _ in range(5):
            self.assertEqual(post(c, "/auth/login", data={"username": "alice", "password": "nope"}).status_code, 401)

    def test_register_never_shows_raw_errors(self):
        DB.create_user_error = Exception("boom: password=hunter2 host=db.internal.example")
        r = post(APP.test_client(), "/auth/register", data={
            "username": "newkid", "password": "abcdef", "confirm_password": "abcdef", "full_name": "New Kid"})
        self.assertEqual(r.status_code, 400)
        self.assertNotIn(b"hunter2", r.data)
        self.assertNotIn(b"db.internal", r.data)
        self.assertIn(b"couldn", r.data)

    def test_register_success_creates_profile_and_logs_in(self):
        c = APP.test_client()
        r = post(c, "/auth/register", data={
            "username": "newkid", "password": "abcdef", "confirm_password": "abcdef", "full_name": "New Kid"})
        self.assertEqual(r.status_code, 302)
        profile = next(p for p in DB.rows("profiles") if p["username"] == "newkid")
        with c.session_transaction() as s:
            self.assertEqual(s["user_id"], profile["id"])

    def test_register_duplicate_username_is_reported(self):
        r = post(APP.test_client(), "/auth/register", data={
            "username": "alice", "password": "abcdef", "confirm_password": "abcdef", "full_name": "X"})
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"already taken", r.data)

    def test_register_username_length_rules(self):
        for bad in ("ab", "x" * 21):
            r = post(APP.test_client(), "/auth/register", data={
                "username": bad, "password": "abcdef", "confirm_password": "abcdef", "full_name": "X"})
            self.assertEqual(r.status_code, 400)


# ======================================================================
# PHASE 1: who may see what
# ======================================================================

class AccessTests(Base):
    def has(self, uid):
        with APP.test_request_context():
            return access.user_has_premium(uid)

    def test_no_subscription_means_no_premium(self):
        self.assertFalse(self.has(self.ids["alice"]))

    def test_active_subscription_unlocks(self):
        give_subscription(self.ids["alice"])
        self.assertTrue(self.has(self.ids["alice"]))

    def test_expired_subscription_does_not_unlock(self):
        give_subscription(self.ids["alice"], minutes_valid=-5)
        self.assertFalse(self.has(self.ids["alice"]))

    def test_revoked_subscription_does_not_unlock(self):
        give_subscription(self.ids["alice"], status="revoked")
        self.assertFalse(self.has(self.ids["alice"]))

    def test_someone_elses_subscription_does_not_unlock(self):
        give_subscription(self.ids["bob"])
        self.assertFalse(self.has(self.ids["alice"]))

    def test_admin_always_has_premium(self):
        self.assertTrue(self.has(self.ids["owner"]))

    def test_anonymous_has_no_premium(self):
        self.assertFalse(self.has(None))

    def test_database_failure_fails_closed(self):
        give_subscription(self.ids["alice"])
        DB.fail_tables.update({"subscriptions", "profiles"})
        self.assertFalse(self.has(self.ids["alice"]))

    def test_positive_answer_is_cached_but_negative_is_not(self):
        self.assertFalse(self.has(self.ids["alice"]))
        give_subscription(self.ids["alice"])                      # approved a moment later
        self.assertTrue(self.has(self.ids["alice"]))              # visible immediately

    def test_tests_free_premium_and_legacy_grants(self):
        free = {"id": "t-free", "is_premium": False}
        premium = {"id": "t-prem", "is_premium": True}
        alice = self.ids["alice"]
        self.assertTrue(access.can_access_test(free, None))
        self.assertFalse(access.can_access_test(premium, alice))
        self.assertFalse(access.can_access_test(None, alice))
        DB.insert("test_access_grants", {"test_id": "t-prem", "user_id": alice})   # old per-test purchase
        self.assertTrue(access.can_access_test(premium, alice))
        self.assertFalse(access.can_access_test(premium, self.ids["bob"]))

    def test_models_wrapper_uses_the_central_rule(self):
        from app.shared.models import user_has_access_to_test
        give_subscription(self.ids["alice"])
        with APP.test_request_context():
            self.assertTrue(user_has_access_to_test({"id": "t", "is_premium": True}, self.ids["alice"]))
            self.assertFalse(user_has_access_to_test({"id": "t", "is_premium": True}, self.ids["bob"]))

    def test_a_failed_plan_lookup_is_not_remembered(self):
        DB.fail_tables.add("plans")
        with APP.test_request_context():
            self.assertEqual(access.get_active_plans(), [])
            DB.fail_tables.discard("plans")
            self.assertEqual(len(access.get_active_plans()), 1)         # recovers on the very next call

    def test_plans_are_cached_for_a_short_time(self):
        with APP.test_request_context():
            self.assertEqual(len(access.get_active_plans()), 1)
            DB.rows("plans").clear()
            self.assertEqual(len(access.get_active_plans()), 1)         # served from the cache

    def test_premium_status_for_templates(self):
        give_subscription(self.ids["alice"])
        with APP.test_request_context():
            from flask import session
            session["user_id"] = self.ids["alice"]
            status = access.premium_status()
        self.assertTrue(status["active"])
        self.assertEqual(status["plan_name"], "Season Pass")
        self.assertTrue(status["expires_label"])


# ======================================================================
# PHASE 0: premium practice sets are no longer free for everyone
# ======================================================================

class PracticeGateTests(Base):
    CH = "chapter-1"
    BASE_URL = "/streams/neet/practice/chapter/chapter-1/mcq/cat/folder"

    def setUp(self):
        super().setUp()
        for set_name, premium in (("free-set", False), ("premium-set", True)):
            for n in range(2):
                DB.insert("questions", {
                    "chapter_id": self.CH, "is_pyq": False, "category": "cat", "folder_name": "folder",
                    "set_name": set_name, "is_premium": premium, "marks": 4,
                    "question_text": f"Q{n} of {set_name}", "option_a": "A", "option_b": "B",
                    "option_c": "C", "option_d": "D", "correct_option": "B",
                    "explanation": "Because.", "image_url": None, "has_image": False,
                    "created_by": "ADMIN-UUID-SHOULD-NOT-LEAK",
                })
        stream = {"id": "s1", "slug": "neet", "name": "NEET"}
        chapter = {"id": self.CH, "name": "Kinematics"}
        for target in ("app.user.routes._practice_ctx",):
            p = mock.patch(target, return_value=(stream, chapter))
            p.start()
            self.addCleanup(p.stop)

    def url(self, set_name, tail=""):
        return f"{self.BASE_URL}/{set_name}{tail}"

    def make_attempt(self, who, set_name, submitted=False):
        return DB.insert("practice_attempts", {
            "id": f"att-{who}-{set_name}", "user_id": self.ids[who], "chapter_id": self.CH, "mode": "mcq",
            "category": "cat", "folder_name": "folder", "set_name": set_name,
            "submitted_at": fakes.ts() if submitted else None, "attempt_kind": "full", "parent_attempt_id": None,
            "started_at": fakes.ts(),
        })

    def test_free_set_opens_for_a_free_student(self):
        att = self.make_attempt("alice", "free-set")
        r = make_client(self.ids["alice"]).get(self.url("free-set", "/run") + f"?attempt_id={att['id']}")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Q0 of free-set", r.data)

    def test_runner_only_receives_whitelisted_columns(self):
        att = self.make_attempt("alice", "free-set")
        r = make_client(self.ids["alice"]).get(self.url("free-set", "/run") + f"?attempt_id={att['id']}")
        self.assertNotIn(b"ADMIN-UUID-SHOULD-NOT-LEAK", r.data)

    def test_premium_run_url_is_blocked_for_a_free_student(self):
        att = self.make_attempt("alice", "premium-set")
        r = make_client(self.ids["alice"]).get(self.url("premium-set", "/run") + f"?attempt_id={att['id']}")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/premium-set"))

    def test_premium_questions_never_reach_a_free_students_browser(self):
        att = self.make_attempt("alice", "premium-set")
        r = make_client(self.ids["alice"]).get(self.url("premium-set", "/run") + f"?attempt_id={att['id']}", follow_redirects=True)
        self.assertNotIn(b"Q0 of premium-set", r.data)
        self.assertNotIn(b"Because.", r.data)

    def test_premium_start_is_blocked_for_a_free_student(self):
        with mock.patch("app.user.routes.start_practice_attempt", return_value=("att-new", "ok")) as start:
            r = post(make_client(self.ids["alice"]), self.url("premium-set", "/start"), data={"kind": "full"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(start.called)

    def test_premium_review_is_blocked_for_a_free_student(self):
        self.make_attempt("alice", "premium-set", submitted=True)
        r = make_client(self.ids["alice"]).get(self.url("premium-set", "/analyse/full"))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/premium-set"))

    def test_overview_shows_unlock_card_instead_of_start_button(self):
        r = make_client(self.ids["alice"]).get(self.url("premium-set"))
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Season Pass", r.data)
        self.assertNotIn(b"/start", r.data)

    def test_overview_of_free_set_still_has_start_button(self):
        r = make_client(self.ids["alice"]).get(self.url("free-set"))
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"/start", r.data)

    def test_paying_student_can_start_and_run_premium(self):
        give_subscription(self.ids["alice"])
        with mock.patch("app.user.routes.start_practice_attempt", return_value=("att-new", "ok")):
            r = post(make_client(self.ids["alice"]), self.url("premium-set", "/start"), data={"kind": "full"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/run", r.headers["Location"])
        att = self.make_attempt("alice", "premium-set")
        r = make_client(self.ids["alice"]).get(self.url("premium-set", "/run") + f"?attempt_id={att['id']}")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Q0 of premium-set", r.data)

    def test_admin_can_open_premium(self):
        att = self.make_attempt("owner", "premium-set")
        r = make_client(self.ids["owner"]).get(self.url("premium-set", "/run") + f"?attempt_id={att['id']}")
        self.assertEqual(r.status_code, 200)

    def test_expired_pass_locks_premium_again(self):
        give_subscription(self.ids["alice"], minutes_valid=-1)
        att = self.make_attempt("alice", "premium-set")
        r = make_client(self.ids["alice"]).get(self.url("premium-set", "/run") + f"?attempt_id={att['id']}")
        self.assertEqual(r.status_code, 302)

    def test_database_hiccup_never_hands_out_premium(self):
        att = self.make_attempt("alice", "premium-set")
        original = DB.fail_tables.copy()
        DB.fail_tables.add("questions")
        try:
            r = make_client(self.ids["alice"]).get(self.url("premium-set", "/run") + f"?attempt_id={att['id']}")
        finally:
            DB.fail_tables = original
        self.assertNotEqual(r.status_code, 200)

    def test_not_logged_in_goes_to_login(self):
        r = APP.test_client().get(self.url("premium-set", "/run"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/auth/login", r.headers["Location"])


# ======================================================================
# PHASE 1: the student's payment journey
# ======================================================================

class PaymentFlowTests(Base):
    def test_requires_login(self):
        c = APP.test_client()
        self.assertIn("/auth/login", c.get("/pay").headers["Location"])
        self.assertIn("/auth/login", post(c, "/pay/start", data={"plan_code": "season_pass_2027"}).headers["Location"])
        r = post(c, "/pay/order/00000000-0000-4000-8000-000000000000/submit", data={"utr": "123456789012"})
        self.assertEqual(r.status_code, 401)

    def test_plans_page_offers_the_plan(self):
        r = make_client(self.ids["alice"]).get("/pay")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Season Pass", r.data)
        self.assertIn("₹299".encode(), r.data)

    def test_plans_page_for_a_subscriber_says_active_and_hides_buy_button(self):
        give_subscription(self.ids["alice"])
        r = make_client(self.ids["alice"]).get("/pay")
        self.assertIn(b"is active", r.data)
        self.assertNotIn(b"/pay/start", r.data)

    def test_start_creates_an_order_with_a_unique_paise_amount(self):
        _, order_id = self.new_order("alice")
        o = self.order(order_id)
        self.assertEqual(o["status"], "created")
        self.assertEqual(o["base_amount"], 299.0)
        self.assertTrue(299.01 <= o["payable_amount"] <= 299.99)

    def test_double_click_reuses_the_same_order(self):
        c, first = self.new_order("alice")
        r = post(c, "/pay/start", data={"plan_code": "season_pass_2027"})
        self.assertTrue(r.headers["Location"].endswith(first))
        self.assertEqual(len(DB.rows("orders")), 1)

    def test_many_students_never_share_an_amount(self):
        amounts = set()
        for i in range(40):
            uid = DB.add_auth_user(f"kid{i}", "secret123")
            DB.insert("profiles", {"id": uid, "username": f"kid{i}", "full_name": "Kid", "role_id": 1})
            c = make_client(uid)
            r = post(c, "/pay/start", data={"plan_code": "season_pass_2027"})
            order_id = r.headers["Location"].rsplit("/", 1)[-1]
            amounts.add(self.order(order_id)["payable_amount"])
        self.assertEqual(len(amounts), 40)

    def test_already_subscribed_cannot_pay_again(self):
        give_subscription(self.ids["alice"])
        # the real SQL function refuses via 'already_active' only for the same plan:
        DB.rows("subscriptions")[0]["plan_id"] = PLAN_ID
        r = post(make_client(self.ids["alice"]), "/pay/start", data={"plan_code": "season_pass_2027"}, follow_redirects=True)
        self.assertEqual(len(DB.rows("orders")), 0)
        self.assertIn(b"already have an active plan", r.data)

    def test_unknown_plan_is_refused(self):
        r = post(make_client(self.ids["alice"]), "/pay/start", data={"plan_code": "nope"}, follow_redirects=True)
        self.assertEqual(len(DB.rows("orders")), 0)
        self.assertIn(b"isn&#39;t available", r.data)

    def test_checkout_page_shows_exact_amount_upi_id_and_link(self):
        c, order_id = self.new_order("alice")
        amount = "%.2f" % self.order(order_id)["payable_amount"]
        r = c.get(f"/pay/order/{order_id}")
        self.assertEqual(r.status_code, 200)
        html = r.data.decode()
        self.assertIn("Pay exactly", html)
        self.assertIn(amount, html)
        self.assertIn("sangam@fam", html)
        self.assertIn(f"upi://pay?pa=sangam@fam&amp;pn=Sangam%20Study&amp;am={amount}&amp;cu=INR&amp;tn=SSP-", html.replace("&", "&amp;") if "&amp;" not in html else html)
        self.assertIn('id="pay-submit"', html)

    def test_checkout_works_even_if_qr_library_is_missing(self):
        c, order_id = self.new_order("alice")
        with mock.patch.object(pay, "segno", None):
            r = c.get(f"/pay/order/{order_id}")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"QR code isn't available", r.data)
        self.assertIn(b"sangam@fam", r.data)

    def test_checkout_draws_qr_when_available(self):
        c, order_id = self.new_order("alice")
        with mock.patch.object(pay, "qr_svg", return_value='<svg id="fake-qr"></svg>'):
            r = c.get(f"/pay/order/{order_id}")
        self.assertIn(b'id="fake-qr"', r.data)

    def test_other_students_cannot_open_or_submit_someones_order(self):
        _, order_id = self.new_order("alice")
        bob = make_client(self.ids["bob"])
        self.assertEqual(bob.get(f"/pay/order/{order_id}").status_code, 404)
        self.assertEqual(bob.get(f"/pay/order/{order_id}/status.json").status_code, 404)
        r = post(bob, f"/pay/order/{order_id}/submit", data={"utr": "123456789012"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.order(order_id)["status"], "created")

    def test_bad_order_id_is_a_404_not_a_crash(self):
        c = make_client(self.ids["alice"])
        self.assertEqual(c.get("/pay/order/not-a-uuid").status_code, 404)
        self.assertEqual(c.get("/pay/order/' or 1=1 --").status_code, 404)

    def test_utr_must_be_twelve_digits(self):
        c, order_id = self.new_order("alice")
        for bad in ("", "12345", "1234567890123", "abcdefghijkl"):
            r = post(c, f"/pay/order/{order_id}/submit", data={"utr": bad})
            self.assertEqual(r.status_code, 400, bad)
            self.assertFalse(r.get_json()["ok"])
        self.assertEqual(self.order(order_id)["status"], "created")

    def test_submitting_a_utr_notifies_telegram_with_buttons(self):
        c, order_id = self.new_order("alice")
        r = post(c, f"/pay/order/{order_id}/submit", data={"utr": "1234 5678 9012"})   # spaces are ignored
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        o = self.order(order_id)
        self.assertEqual((o["status"], o["utr"]), ("submitted", "123456789012"))
        sent = self.tg.of("sendMessage")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["json"]["chat_id"], ADMIN_TG_ID)
        self.assertIn("123456789012", sent[0]["json"]["text"])
        self.assertIn("%.2f" % o["payable_amount"], sent[0]["json"]["text"])
        buttons = [b["callback_data"] for row in sent[0]["json"]["reply_markup"]["inline_keyboard"] for b in row]
        self.assertEqual(buttons, [f"ord:a:{order_id}", f"ord:r:{order_id}"])
        self.assertEqual(o["tg_message_id"], 78)

    def test_screenshot_is_cleaned_and_forwarded_to_telegram(self):
        c, order_id = self.new_order("alice")
        raw = png_bytes()
        r = post(c, f"/pay/order/{order_id}/submit", content_type="multipart/form-data",
                 data={"utr": "123456789012", "screenshot": (io.BytesIO(raw), "shot.png")})
        self.assertEqual(r.status_code, 200, r.data)
        photo = self.tg.of("sendPhoto")
        self.assertEqual(len(photo), 1)
        sent_bytes = photo[0]["files"]["photo"][1]
        self.assertTrue(sent_bytes.startswith(b"\xff\xd8"))            # re-encoded as JPEG
        self.assertLess(len(sent_bytes), len(raw))
        o = self.order(order_id)
        self.assertEqual((o["tg_file_id"], o["tg_message_id"]), ("FILEID", 77))

    def test_non_image_upload_is_refused_and_nothing_is_saved(self):
        c, order_id = self.new_order("alice")
        r = post(c, f"/pay/order/{order_id}/submit", content_type="multipart/form-data",
                 data={"utr": "123456789012", "screenshot": (io.BytesIO(b"<html>not an image</html>"), "x.png")})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.order(order_id)["status"], "created")
        self.assertEqual(self.tg.calls, [])

    def test_huge_upload_is_refused_early(self):
        c, order_id = self.new_order("alice")
        r = post(c, f"/pay/order/{order_id}/submit", content_type="multipart/form-data",
                 data={"utr": "123456789012", "screenshot": (io.BytesIO(b"0" * (7 * 1024 * 1024)), "big.png")})
        self.assertEqual(r.status_code, 413)
        self.assertEqual(self.order(order_id)["status"], "created")

    def test_the_same_utr_cannot_be_used_by_two_students(self):
        ca, oa = self.new_order("alice", submit_utr="123456789012")
        cb, ob = self.new_order("bob")
        r = post(cb, f"/pay/order/{ob}/submit", data={"utr": "123456789012"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("already been submitted", r.get_json()["error"])
        self.assertEqual(self.order(ob)["status"], "created")

    def test_double_click_on_submit_notifies_only_once(self):
        c, order_id = self.new_order("alice")
        for _ in range(3):
            self.assertEqual(post(c, f"/pay/order/{order_id}/submit", data={"utr": "123456789012"}).status_code, 200)
        self.assertEqual(len(self.tg.of("sendMessage")), 1)

    def test_a_different_utr_after_submitting_is_refused(self):
        c, order_id = self.new_order("alice", submit_utr="123456789012")
        r = post(c, f"/pay/order/{order_id}/submit", data={"utr": "999999999999"})
        self.assertEqual(r.status_code, 409)

    def test_payment_still_saved_when_telegram_is_down(self):
        c, order_id = self.new_order("alice")
        self.tg.fail = True
        r = post(c, f"/pay/order/{order_id}/submit", data={"utr": "123456789012"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.order(order_id)["status"], "submitted")      # waiting in /admin/payments

    def test_late_payer_can_still_send_utr_after_window_ended(self):
        c, order_id = self.new_order("alice")
        self.order(order_id)["expires_at"] = fakes.ts(-5)
        page = c.get(f"/pay/order/{order_id}")
        self.assertIn(b"payment window has ended", page.data)
        self.assertIn(b"I already paid", page.data)
        r = post(c, f"/pay/order/{order_id}/submit", data={"utr": "123456789012"})
        self.assertEqual(r.status_code, 200)

    def test_late_payer_is_told_if_the_amount_was_given_away(self):
        c, order_id = self.new_order("alice")
        o = self.order(order_id)
        o["status"] = "expired"
        DB.insert("orders", {"user_id": self.ids["bob"], "plan_id": PLAN_ID, "base_amount": 299.0,
                             "payable_amount": o["payable_amount"], "status": "created", "utr": None,
                             "expires_at": fakes.ts(30), "created_at": fakes.ts()})
        r = post(c, f"/pay/order/{order_id}/submit", data={"utr": "123456789012"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("start a new payment", r.get_json()["error"])

    def test_status_json_follows_the_order(self):
        c, order_id = self.new_order("alice", submit_utr="123456789012")
        self.assertEqual(c.get(f"/pay/order/{order_id}/status.json").get_json()["status"], "submitted")
        self.order(order_id)["status"] = "approved"
        self.assertEqual(c.get(f"/pay/order/{order_id}/status.json").get_json()["status"], "approved")

    def test_every_order_state_renders(self):
        c, order_id = self.new_order("alice")
        expectations = {
            "created": b"Pay exactly", "submitted": b"verifying your payment", "approved": b"Payment approved",
            "rejected": b"couldn", "expired": b"payment window has ended", "cancelled": b"payment was cancelled",
        }
        o = self.order(order_id)
        o["utr"] = "123456789012"
        o["reject_reason"] = "Reason shown to the student."
        give_subscription(self.ids["alice"])
        for status, phrase in expectations.items():
            o["status"] = status
            r = c.get(f"/pay/order/{order_id}")
            self.assertEqual(r.status_code, 200, status)
            self.assertIn(phrase, r.data, status)

    def test_rejected_student_sees_the_reason_and_can_pay_again(self):
        c, order_id = self.new_order("alice", submit_utr="123456789012")
        o = self.order(order_id)
        o.update(status="rejected", reject_reason="Reason shown to the student.")
        page = c.get(f"/pay/order/{order_id}")
        self.assertIn(b"Reason shown to the student.", page.data)
        r = post(c, "/pay/start", data={"plan_code": "season_pass_2027"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(r.headers["Location"].endswith(order_id))        # a fresh order

    def test_history_lists_my_payments_only(self):
        c, order_id = self.new_order("alice", submit_utr="123456789012")
        self.new_order("bob")
        page = c.get("/pay/history")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.data.count(b"/pay/order/"), 1)
        self.assertIn(b"Verifying", page.data)

    def test_payments_not_configured_blocks_start(self):
        with mock.patch.dict(os.environ, {"UPI_ID": ""}):
            r = post(make_client(self.ids["alice"]), "/pay/start", data={"plan_code": "season_pass_2027"}, follow_redirects=True)
        self.assertEqual(len(DB.rows("orders")), 0)
        self.assertIn(b"aren&#39;t available yet", r.data)

    def test_start_is_rate_limited(self):
        c = make_client(self.ids["alice"])
        codes = []
        for _ in range(12):
            codes.append(post(c, "/pay/start", data={"plan_code": "nope"}).status_code)
        self.assertEqual(len(DB.rows("orders")), 0)
        # after 10 tries the flash says "Too many attempts"
        r = post(c, "/pay/start", data={"plan_code": "nope"}, follow_redirects=True)
        self.assertIn(b"Too many attempts", r.data)


# ======================================================================
# PHASE 1: Telegram approve / reject
# ======================================================================

class WebhookTests(Base):
    def setUp(self):
        super().setUp()
        self.client, self.order_id = self.new_order("alice", submit_utr="123456789012")
        self.tg.calls.clear()

    def send(self, update, secret=None):
        secret = pay.tg_webhook_secret() if secret is None else secret
        return APP.test_client().post("/tg-webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": secret})

    def press(self, data, from_id=ADMIN_TG_ID, photo=False, message_id=None):
        if message_id is None:
            message_id = self.order(self.order_id)["tg_message_id"]       # the card saved on the order
        message = {"message_id": message_id, "chat": {"id": from_id}}
        if photo:
            message["photo"] = [{}]
        return self.send({"callback_query": {"id": "cbq1", "from": {"id": from_id}, "data": data, "message": message}})

    def has_access(self, who="alice"):
        with APP.test_request_context():
            return access.user_has_premium(self.ids[who])

    def test_wrong_or_missing_secret_is_rejected(self):
        self.assertEqual(self.send({}, secret="wrong").status_code, 403)
        self.assertEqual(APP.test_client().post("/tg-webhook", json={}).status_code, 403)

    def test_approve_unlocks_the_student(self):
        self.assertFalse(self.has_access())
        self.assertEqual(self.press(f"ord:a:{self.order_id}").status_code, 200)
        o = self.order(self.order_id)
        self.assertEqual((o["status"], o["reviewed_by"]), ("approved", f"telegram:{ADMIN_TG_ID}"))
        self.assertEqual(len(DB.rows("subscriptions")), 1)
        self.assertTrue(self.has_access())
        edits = self.tg.of("editMessageText")
        self.assertEqual(len(edits), 1)
        self.assertIn("APPROVED", edits[0]["json"]["text"])
        self.assertEqual(edits[0]["json"]["reply_markup"], {"inline_keyboard": []})     # buttons removed

    def test_pressing_a_resent_card_updates_both_cards(self):
        self.press(f"ord:a:{self.order_id}", message_id=9999)             # e.g. a card re-sent by /pending
        edited = sorted(c["json"]["message_id"] for c in self.tg.of("editMessageText"))
        self.assertEqual(edited, sorted([9999, self.order(self.order_id)["tg_message_id"]]))

    def test_photo_cards_are_edited_as_captions(self):
        self.press(f"ord:a:{self.order_id}", photo=True)
        self.assertEqual(len(self.tg.of("editMessageCaption")), 1)

    def test_a_stranger_cannot_approve(self):
        self.press(f"ord:a:{self.order_id}", from_id=999)
        self.assertEqual(self.order(self.order_id)["status"], "submitted")
        self.assertEqual(DB.rows("subscriptions"), [])
        alert = self.tg.of("answerCallbackQuery")[0]["json"]
        self.assertTrue(alert["show_alert"])

    def test_pressing_approve_twice_creates_only_one_subscription(self):
        self.press(f"ord:a:{self.order_id}")
        self.press(f"ord:a:{self.order_id}")
        self.assertEqual(len(DB.rows("subscriptions")), 1)

    def test_reject_offers_reasons_then_rejects_with_the_chosen_one(self):
        self.press(f"ord:r:{self.order_id}")
        markup = self.tg.of("editMessageReplyMarkup")[0]["json"]["reply_markup"]["inline_keyboard"]
        self.assertEqual(len(markup), 5)                                   # 4 reasons + Back
        self.press(f"ord:x:{self.order_id}:2")
        o = self.order(self.order_id)
        self.assertEqual(o["status"], "rejected")
        self.assertIn("amount", o["reject_reason"])
        self.assertFalse(self.has_access())
        self.assertIn(b"doesn&#39;t match the amount", self.client.get(f"/pay/order/{self.order_id}").data)

    def test_back_button_restores_approve_and_reject(self):
        self.press(f"ord:b:{self.order_id}")
        markup = self.tg.of("editMessageReplyMarkup")[0]["json"]["reply_markup"]["inline_keyboard"]
        self.assertEqual([b["callback_data"] for row in markup for b in row],
                         [f"ord:a:{self.order_id}", f"ord:r:{self.order_id}"])
        self.assertEqual(self.order(self.order_id)["status"], "submitted")

    def test_a_rejected_payment_can_still_be_approved_later(self):
        self.press(f"ord:x:{self.order_id}:1")
        self.press(f"ord:a:{self.order_id}")
        self.assertEqual(self.order(self.order_id)["status"], "approved")
        self.assertTrue(self.has_access())

    def test_unknown_order_and_garbage_data_do_not_crash(self):
        for data in ("ord:a:00000000-0000-4000-8000-000000000000", "ord:a:not-a-uuid", "junk", "", "ord:z:" + self.order_id):
            self.assertEqual(self.press(data).status_code, 200)
        self.assertEqual(self.order(self.order_id)["status"], "submitted")

    def test_approval_is_saved_even_if_telegram_is_down(self):
        self.tg.fail = True
        self.press(f"ord:a:{self.order_id}")
        self.assertEqual(self.order(self.order_id)["status"], "approved")

    def test_start_command_tells_a_stranger_their_chat_id(self):
        self.send({"message": {"chat": {"id": 4242, "type": "private"}, "from": {"id": 4242}, "text": "/start"}})
        text = self.tg.of("sendMessage")[0]["json"]["text"]
        self.assertIn("4242", text)
        self.assertIn("TG_ADMIN_CHAT_ID", text)

    def test_start_command_confirms_the_owner(self):
        self.send({"message": {"chat": {"id": ADMIN_TG_ID, "type": "private"}, "from": {"id": ADMIN_TG_ID}, "text": "/start"}})
        self.assertIn("Connected", self.tg.of("sendMessage")[0]["json"]["text"])

    def test_pending_command_resends_waiting_payments_to_the_owner_only(self):
        self.send({"message": {"chat": {"id": 4242, "type": "private"}, "from": {"id": 4242}, "text": "/pending"}})
        self.assertEqual(self.tg.calls, [])
        self.send({"message": {"chat": {"id": ADMIN_TG_ID, "type": "private"}, "from": {"id": ADMIN_TG_ID}, "text": "/pending"}})
        cards = self.tg.of("sendMessage")
        self.assertEqual(len(cards), 1)
        self.assertIn("123456789012", cards[0]["json"]["text"])

    def test_group_chats_are_ignored(self):
        self.send({"message": {"chat": {"id": -1, "type": "group"}, "from": {"id": ADMIN_TG_ID}, "text": "/start"}})
        self.assertEqual(self.tg.calls, [])

    def test_html_in_names_cannot_inject_into_the_admin_message(self):
        profile = next(p for p in DB.rows("profiles") if p["id"] == self.ids["alice"])
        profile["full_name"] = "<b>Evil</b> & co"
        order = pay.get_order(self.order_id)
        text = pay.format_admin_message(order)
        self.assertIn("&lt;b&gt;Evil&lt;/b&gt; &amp; co", text)


# ======================================================================
# PHASE 1: admin panel
# ======================================================================

class AdminPanelTests(Base):
    def setUp(self):
        super().setUp()
        self.student, self.order_id = self.new_order("alice", submit_utr="123456789012")
        self.admin = make_client(self.ids["owner"])
        self.tg.calls.clear()

    def test_only_admins_can_open_the_panel(self):
        self.assertEqual(APP.test_client().get("/admin/payments").status_code, 403)
        self.assertEqual(make_client(self.ids["alice"]).get("/admin/payments").status_code, 403)
        self.assertEqual(self.admin.get("/admin/payments").status_code, 200)

    def test_students_cannot_approve_through_the_web_routes(self):
        r = post(make_client(self.ids["alice"]), f"/admin/payments/{self.order_id}/approve")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.order(self.order_id)["status"], "submitted")

    def test_panel_lists_the_waiting_payment_with_numbers(self):
        page = self.admin.get("/admin/payments").data.decode()
        self.assertIn("123456789012", page)
        self.assertIn("alice", page)
        self.assertIn("Waiting for you", page)

    def test_web_approve(self):
        r = post(self.admin, f"/admin/payments/{self.order_id}/approve", follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.order(self.order_id)["status"], "approved")
        self.assertEqual(self.order(self.order_id)["reviewed_by"], f"web:{self.ids['owner']}")
        with APP.test_request_context():
            self.assertTrue(access.user_has_premium(self.ids["alice"]))

    def test_web_approve_needs_a_same_site_request(self):
        r = self.admin.post(f"/admin/payments/{self.order_id}/approve")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.order(self.order_id)["status"], "submitted")

    def test_admin_redirects_never_leave_our_own_site(self):
        r = post(self.admin, f"/admin/payments/{self.order_id}/approve", headers={"Referer": "https://evil.example/x"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "/admin/payments")
        _, other = self.new_order("bob", submit_utr="210987654321")
        r = post(self.admin, f"/admin/payments/{other}/reject", data={"reason": "1"},
                 headers={"Referer": "http://localhost/admin/payments?show=all"})
        self.assertEqual(r.headers["Location"], "http://localhost/admin/payments?show=all")

    def test_web_reject_with_reason(self):
        post(self.admin, f"/admin/payments/{self.order_id}/reject", data={"reason": "4"})
        o = self.order(self.order_id)
        self.assertEqual(o["status"], "rejected")
        self.assertIn("screenshot", o["reject_reason"])

    def test_web_revoke_takes_access_away(self):
        post(self.admin, f"/admin/payments/{self.order_id}/approve")
        post(self.admin, f"/admin/payments/{self.order_id}/revoke")
        self.assertEqual(self.order(self.order_id)["status"], "cancelled")
        with APP.test_request_context():
            self.assertFalse(access.user_has_premium(self.ids["alice"]))

    def test_bad_ids_are_404(self):
        self.assertEqual(post(self.admin, "/admin/payments/not-a-uuid/approve").status_code, 404)
        self.assertEqual(self.admin.get("/admin/payments/not-a-uuid/screenshot").status_code, 404)

    def test_screenshot_proxy_serves_the_image_without_exposing_the_token(self):
        self.order(self.order_id)["tg_file_id"] = "FILEID"
        with mock.patch.object(pay, "tg_fetch_file", return_value=b"\xff\xd8JPEGDATA"):
            r = self.admin.get(f"/admin/payments/{self.order_id}/screenshot")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "image/jpeg")
        self.assertNotIn(b"TEST-TOKEN", r.data)
        self.assertEqual(make_client(self.ids["alice"]).get(f"/admin/payments/{self.order_id}/screenshot").status_code, 403)

    def test_screenshot_404_when_there_is_none(self):
        self.assertEqual(self.admin.get(f"/admin/payments/{self.order_id}/screenshot").status_code, 404)

    def test_connect_telegram_sets_the_webhook_with_the_secret(self):
        r = post(self.admin, "/admin/payments/telegram/connect", follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        call = self.tg.of("setWebhook")[0]["json"]
        self.assertTrue(call["url"].endswith("/tg-webhook"))
        self.assertEqual(call["secret_token"], pay.tg_webhook_secret())
        self.assertEqual(call["allowed_updates"], ["message", "callback_query"])

    def test_test_message_goes_to_the_admin_chat(self):
        post(self.admin, "/admin/payments/telegram/test")
        self.assertEqual(self.tg.of("sendMessage")[0]["json"]["chat_id"], ADMIN_TG_ID)

    def test_resend_to_telegram(self):
        post(self.admin, f"/admin/payments/{self.order_id}/resend")
        self.assertEqual(len(self.tg.of("sendMessage")), 1)
        self.assertIsNotNone(self.order(self.order_id)["tg_message_id"])

    def test_abandoned_orders_are_expired_when_the_panel_opens(self):
        _, stale = self.new_order("bob")
        self.order(stale)["expires_at"] = fakes.ts(-1)
        self.admin.get("/admin/payments")
        self.assertEqual(self.order(stale)["status"], "expired")

    def test_setup_checks_show_webhook_even_before_admin_chat_id_is_set(self):
        info = {"url": "https://example.test/tg-webhook", "pending_update_count": 0}
        with mock.patch.dict(os.environ, {"TG_ADMIN_CHAT_ID": ""}), \
             mock.patch.object(pay, "tg_webhook_info", return_value=info):
            page = self.admin.get("/admin/payments").data.decode()
        self.assertIn("Bot is connected to", page)
        self.assertIn("TG_ADMIN_CHAT_ID", page)                 # still tells the owner what is missing

    def test_filter_tabs_render(self):
        for tab in ("pending", "approved", "problems", "all", "garbage"):
            self.assertEqual(self.admin.get(f"/admin/payments?show={tab}").status_code, 200)


# ======================================================================
# Small pure functions
# ======================================================================

class HelperTests(Base):
    def test_upi_link_format(self):
        link = pay.build_upi_link(299.5, "abcdef12-0000-4000-8000-000000000000")
        self.assertEqual(link, "upi://pay?pa=sangam@fam&pn=Sangam%20Study&am=299.50&cu=INR&tn=SSP-ABCDEF")

    def test_amount_text_has_no_float_noise(self):
        self.assertEqual(pay.amount_text(299.37), "299.37")
        self.assertEqual(pay.amount_text(299), "299.00")
        self.assertEqual(pay.amount_text("299.1"), "299.10")

    def test_upi_id_validation(self):
        for good in ("sangam@fam", "a.b-c_1@okaxis"):
            with mock.patch.dict(os.environ, {"UPI_ID": good}):
                self.assertTrue(pay.payments_ready(), good)
        for bad in ("", "no-at-sign", "@fam", "a b@fam", "x@"):
            with mock.patch.dict(os.environ, {"UPI_ID": bad}):
                self.assertFalse(pay.payments_ready(), bad)

    def test_qr_matrix_becomes_a_scalable_svg(self):
        svg = pay.matrix_to_svg([[1, 0, 1], [0, 1, 0], [1, 0, 1]], border=2)
        self.assertIn('viewBox="0 0 7 7"', svg)
        self.assertIn("M2,2h1v1h-1z", svg)
        self.assertIn("M4,2h1v1h-1z", svg)
        root = re.match(r"<svg[^>]*>", svg).group(0)
        self.assertNotIn("width=", root)
        self.assertNotIn("height=", root)

    def test_qr_svg_uses_the_qr_library_matrix(self):
        class FakeQR:
            matrix = (bytearray([1, 0]), bytearray([0, 1]))

        class FakeSegno:
            calls = []

            @classmethod
            def make(cls, data, **kwargs):
                cls.calls.append((data, kwargs))
                return FakeQR()

        with mock.patch.object(pay, "segno", FakeSegno):
            svg = pay.qr_svg("upi://pay?pa=x@y")
        self.assertEqual(FakeSegno.calls, [("upi://pay?pa=x@y", {"error": "m", "micro": False})])
        self.assertIn('viewBox="0 0 10 10"', svg)          # 2 modules + the 4-module quiet zone on each side
        with mock.patch.object(pay, "segno", None):
            self.assertIsNone(pay.qr_svg("anything"))

    def test_qr_svg_survives_a_library_error(self):
        class Broken:
            @staticmethod
            def make(*a, **k):
                raise ValueError("boom")

        with mock.patch.object(pay, "segno", Broken):
            self.assertIsNone(pay.qr_svg("x"))

    def test_run_of_dark_modules_is_merged(self):
        svg = pay.matrix_to_svg([[1, 1, 1, 0]], border=0)
        self.assertIn("M0,0h3v1h-3z", svg)

    def test_money_and_time_formatting(self):
        self.assertEqual(access.format_inr(299), "₹299")
        self.assertEqual(access.format_inr(299.37), "₹299.37")
        self.assertEqual(access.format_inr(299, always_paise=True), "₹299.00")
        self.assertEqual(access.format_inr(1234567), "₹1,234,567")
        self.assertEqual(access.format_inr(None), "")
        self.assertEqual(access.format_ist_datetime("2026-10-12T10:30:00Z"), "12 Oct 2026, 4:00 PM IST")
        self.assertEqual(access.format_ist_date("2027-06-30T18:29:59Z"), "30 Jun 2027")
        self.assertEqual(access.format_ist_date("garbage"), "")

    def test_short_ref(self):
        self.assertEqual(pay.short_ref("abcdef12-3456-4789-8abc-def012345678"), "ABCDEF12")

    def test_webhook_secret_is_stable_and_uses_allowed_characters(self):
        a, b = pay.tg_webhook_secret(), pay.tg_webhook_secret()
        self.assertEqual(a, b)
        self.assertRegex(a, r"^[A-Za-z0-9_-]{1,256}$")

    def test_is_uuid(self):
        self.assertTrue(pay.is_uuid("11111111-1111-4111-8111-111111111111"))
        for bad in ("", None, "x", "11111111-1111", 5):
            self.assertFalse(pay.is_uuid(bad))


class ScreenshotTests(unittest.TestCase):
    def test_png_with_transparency_becomes_a_small_jpeg(self):
        out = pay.sanitize_screenshot(png_bytes((3000, 2000), mode="RGBA"))
        from PIL import Image
        img = Image.open(io.BytesIO(out))
        self.assertEqual(img.format, "JPEG")
        self.assertLessEqual(max(img.size), 1280)

    def test_gps_and_other_exif_data_is_stripped(self):
        from PIL import Image
        img = Image.new("RGB", (400, 300), (10, 20, 30))
        exif = Image.Exif()
        exif[0x010F] = "SecretCameraMaker"
        exif[0x0132] = "2026:01:01 10:00:00"
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif)
        self.assertTrue(Image.open(io.BytesIO(buf.getvalue())).getexif())          # input really has exif
        out = pay.sanitize_screenshot(buf.getvalue())
        self.assertFalse(Image.open(io.BytesIO(out)).getexif())
        self.assertNotIn(b"SecretCameraMaker", out)

    def test_refuses_a_picture_with_an_absurd_pixel_count(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("1", (9000, 9000)).save(buf, format="PNG")          # 81 million pixels, tiny file
        with self.assertRaises(pay.ScreenshotError) as ctx:
            pay.sanitize_screenshot(buf.getvalue())
        self.assertEqual(str(ctx.exception), "too_large")

    def test_refuses_empty_oversized_unreadable_and_wrong_type(self):
        cases = {
            b"": "empty",
            b"0" * (pay.MAX_UPLOAD_BYTES + 1): "too_large",
            b"just text": "unreadable",
        }
        for raw, code in cases.items():
            with self.assertRaises(pay.ScreenshotError) as ctx:
                pay.sanitize_screenshot(raw)
            self.assertEqual(str(ctx.exception), code)
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buf, format="GIF")
        with self.assertRaises(pay.ScreenshotError) as ctx:
            pay.sanitize_screenshot(buf.getvalue())
        self.assertEqual(str(ctx.exception), "bad_type")


if __name__ == "__main__":
    unittest.main(verbosity=2)
