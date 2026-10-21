# SANGAM STUDY v2.1 — Final Fixed Build (2026-10-21)
"""
access.py -- the ONE place that decides "may this person see this content?"

Every premium decision in the app goes through the functions below, so there is
exactly one rule to read, test and fix:

    a person has premium  <=>  they are an admin
                               OR they have an ACTIVE, un-expired subscription

  * user_has_premium(user_id)        -> bool    (practice sets, "does this person see locks?")
  * can_access_test(test, user_id)   -> bool    (mock tests; also honours old per-test grants)
  * premium_status()                 -> dict    (for templates: dashboard banner etc.)

SAFETY RULES
  * Fails CLOSED: if the database errors, the answer is "no access", never "yes".
  * Only POSITIVE answers are cached (60 s). A student whose payment was just
    approved is therefore never stuck behind a stale "no".
"""
import logging
from datetime import datetime, timedelta, timezone

from flask import g, session

from app.extensions import supabase_admin, cache

logger = logging.getLogger(__name__)

ADMIN_ROLE_ID = 2                       # roles.id = 2 -> 'admin'
_POSITIVE_TTL = 60                      # seconds a "yes" may be cached (per worker)

# India has no daylight saving, so a fixed offset is exact (no tz database needed).
IST = timezone(timedelta(hours=5, minutes=30))

# Built-in fallback plan so /pay always renders the Season Pass card even before
# sql/phase1_payments.sql has been run in Supabase.
DEFAULT_SEASON_PASS_PLAN = {
    "id": "00000000-0000-4000-8000-000000000299",
    "code": "season_pass_2027",
    "name": "Season Pass",
    "description": "Every premium mock test and practice set, until your NEET / JEE 2027 exam season ends.",
    "price_inr": 299,
    "valid_until": "2027-06-30T23:59:59+05:30",
    "valid_days": None,
    "stream_id": None,
    "features": [
        "All premium mock tests",
        "All premium chapter-wise practice sets",
        "Detailed explanations and analysis",
        "One payment, no renewals",
    ],
}


# ----------------------------------------------------------------------
# Time / money formatting helpers (also registered as Jinja filters)
# ----------------------------------------------------------------------

def _utc_stamp():
    """'2026-10-12T10:15:30.123456Z' -- no '+' sign, so it is URL-safe in a filter."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_ts(value):
    """Postgres timestamp string -> aware datetime (UTC), or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def format_ist_date(value):
    dt = parse_ts(value)
    return dt.astimezone(IST).strftime("%d %b %Y").lstrip("0") if dt else ""


def format_ist_datetime(value):
    dt = parse_ts(value)
    if not dt:
        return ""
    local = dt.astimezone(IST)
    hour = local.strftime("%I").lstrip("0") or "12"
    return f"{local.day} {local.strftime('%b %Y')}, {hour}:{local.strftime('%M %p')} IST"


def format_inr(value, always_paise=False):
    """299 -> '₹299' ; 299.37 -> '₹299.37' ; always_paise=True -> '₹299.00'."""
    try:
        amount = round(float(value), 2)
    except (TypeError, ValueError):
        return ""
    if always_paise or abs(amount - round(amount)) > 0.001:
        return f"₹{amount:,.2f}"
    return f"₹{int(round(amount)):,}"


# ----------------------------------------------------------------------
# Plans (small, rarely changes -> cached 2 minutes; failures are never cached)
# ----------------------------------------------------------------------

_PLANS_KEY = "plans:active"
_PLANS_TTL = 120


def get_active_plans(include_fallback=False):
    cached = cache.get(_PLANS_KEY)
    if cached:
        return cached
    try:
        res = (
            supabase_admin.table("plans")
            .select("id, code, name, description, price_inr, valid_until, valid_days, features, stream_id")
            .eq("is_active", True)
            .order("display_order")
            .execute()
        )
        rows = res.data or []
    except Exception as e:
        logger.error(f"Error fetching active plans: {e}")
        return [dict(DEFAULT_SEASON_PASS_PLAN)] if include_fallback else []
    if rows:
        cache.set(_PLANS_KEY, rows, timeout=_PLANS_TTL)
        return rows
    return [dict(DEFAULT_SEASON_PASS_PLAN)] if include_fallback else []


# ----------------------------------------------------------------------
# Core checks
# ----------------------------------------------------------------------

def is_admin_user(user_id):
    """True if profiles.role_id == 2. Fails closed."""
    if not user_id:
        return False
    key = f"isadmin:{user_id}"
    if cache.get(key):
        return True
    try:
        rows = supabase_admin.table("profiles").select("role_id").eq("id", user_id).execute().data or []
        ok = bool(rows) and rows[0].get("role_id") == ADMIN_ROLE_ID
    except Exception as e:
        logger.error(f"Error checking admin role for {user_id}: {e}")
        return False
    if ok:
        cache.set(key, True, timeout=_POSITIVE_TTL)
    return ok


def get_active_subscription(user_id):
    """The user's currently valid subscription row (with plan name), or None."""
    if not user_id:
        return None
    now = _utc_stamp()
    try:
        res = (
            supabase_admin.table("subscriptions")
            .select("id, plan_id, stream_id, starts_at, expires_at, plans(name)")
            .eq("user_id", user_id)
            .eq("status", "active")
            .lte("starts_at", now)
            .gt("expires_at", now)
            .order("expires_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None
    except Exception as e:
        logger.error(f"Error fetching subscription for {user_id}: {e}")
        return None


def user_has_premium(user_id):
    """Admin, or holder of an active subscription. Fails closed."""
    if not user_id:
        return False
    key = f"premium:{user_id}"
    if cache.get(key):
        return True
    if get_active_subscription(user_id) is not None or is_admin_user(user_id):
        cache.set(key, True, timeout=_POSITIVE_TTL)
        return True
    return False


def forget_premium(user_id):
    """Call right after granting/revoking access so this worker forgets the old answer."""
    if user_id:
        cache.delete(f"premium:{user_id}")


def can_access_test(test, user_id):
    """
    Mock-test access.
      * free test                      -> everyone
      * premium test                   -> premium holders, OR a legacy per-test
                                          grant (test_access_grants)
    """
    if not test:
        return False
    if not test.get("is_premium"):
        return True
    if not user_id:
        return False
    if user_has_premium(user_id):
        return True
    try:
        res = (
            supabase_admin.table("test_access_grants")
            .select("test_id")
            .eq("test_id", test["id"])
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        return bool(res.data)
    except Exception as e:
        logger.error(f"Error checking test grant for user {user_id}, test {test.get('id')}: {e}")
        return False


# ----------------------------------------------------------------------
# For templates: {% set ps = premium_status() %}
# ----------------------------------------------------------------------

def premium_status():
    """
    Small dict for UI (dashboard banner, plan page). Computed lazily and at most
    once per request. Keys: active, is_admin, plan_name, expires_label,
    offer_name, offer_price (the plan to promote when not active).
    """
    uid = session.get("user_id")
    memo = getattr(g, "_premium_status", None)
    if memo is not None and memo.get("uid") == uid:
        return memo

    status = {
        "uid": uid, "active": False, "is_admin": False, "plan_name": None,
        "expires_label": None, "offer_name": None, "offer_price": None,
    }
    if uid:
        if is_admin_user(uid):
            status.update(active=True, is_admin=True, plan_name="Admin access")
        else:
            sub = get_active_subscription(uid)
            if sub:
                expires = parse_ts(sub.get("expires_at"))
                status.update(
                    active=True,
                    plan_name=(sub.get("plans") or {}).get("name") or "Premium",
                    expires_label=format_ist_date(expires) if expires else None,
                )
    if not status["active"]:
        plans = get_active_plans(include_fallback=True)
        if plans:
            status["offer_name"] = plans[0].get("name")
            status["offer_price"] = plans[0].get("price_inr")

    g._premium_status = status
    return status
