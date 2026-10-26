# STUDY SANGAM HUB v3.0 — Per-Test-Series Access & Formatting Helpers
"""
access.py -- the ONE place that decides "may this person see this content?"

Updated Architecture (v3.0):
  * "Season Pass" has been completely removed.
  * Chapter-wise practice sets are open to all logged-in students.
  * Mock Test Series folders (`mock_test_series`) can be configured by Admin as:
      - FREE: every test inside the folder is free for all logged-in students.
      - PAID (with a specific Price in INR): locked for normal students until
        they purchase that Test Series and Admin approves their payment via the
        Telegram Bot.
  * Admins (`role_id == 2`) always have access to everything.
"""
import logging
from datetime import datetime, timedelta, timezone

from flask import g, session

from app.extensions import supabase_admin, cache

logger = logging.getLogger(__name__)

ADMIN_ROLE_ID = 2                       # roles.id = 2 -> 'admin'
_POSITIVE_TTL = 60                      # seconds a "yes" may be cached (per worker)

# India has no daylight saving, so a fixed offset is exact.
IST = timezone(timedelta(hours=5, minutes=30))

SERIES_PLAN_PREFIX = "series_"
BOT_QR_PLAN_CODE = "__bot_qr_config__"

# Legacy constant kept only so any old import does not raise ImportError
DEFAULT_SEASON_PASS_PLAN = {
    "id": "00000000-0000-4000-8000-000000000299",
    "code": "season_pass_2027",
    "name": "Test Series Pass",
    "description": "Unlock paid mock test series.",
    "price_inr": 99,
    "valid_until": "2029-12-31T23:59:59+05:30",
    "valid_days": 3650,
    "stream_id": None,
    "features": [],
}


# ----------------------------------------------------------------------
# Time / money formatting helpers (also registered as Jinja filters)
# ----------------------------------------------------------------------

def _utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_ts(value):
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
    try:
        amount = round(float(value), 2)
    except (TypeError, ValueError):
        return ""
    if always_paise or abs(amount - round(amount)) > 0.001:
        return f"₹{amount:,.2f}"
    return f"₹{int(round(amount)):,}"


# ----------------------------------------------------------------------
# Series Plan Helpers (maps each Paid Test Series to a `plans` row)
# ----------------------------------------------------------------------

def series_plan_code(series_id):
    return f"{SERIES_PLAN_PREFIX}{series_id}"


def extract_series_id_from_code(code):
    code_str = str(code or "")
    if code_str.startswith(SERIES_PLAN_PREFIX):
        return code_str[len(SERIES_PLAN_PREFIX):]
    return None


_PLANS_KEY = "plans:all_series"
_PLANS_TTL = 120


def get_all_series_plans_map():
    """
    Returns dict mapping series_id -> plan row for all test series stored in `plans`.
    """
    cached = cache.get(_PLANS_KEY)
    if cached is not None:
        return cached
    try:
        res = (
            supabase_admin.table("plans")
            .select("id, code, name, description, price_inr, valid_until, valid_days, features, stream_id, is_active")
            .execute()
        )
        rows = res.data or []
    except Exception as e:
        logger.error(f"Error fetching series plans: {e}")
        return {}

    by_series = {}
    for r in rows:
        code = str(r.get("code") or "")
        if code == BOT_QR_PLAN_CODE:
            continue
        sid = extract_series_id_from_code(code)
        key = sid or code or str(r.get("id") or "")
        if key:
            by_series[key] = r
    cache.set(_PLANS_KEY, by_series, timeout=_PLANS_TTL)
    return by_series


def invalidate_series_plans_cache():
    cache.delete(_PLANS_KEY)


def get_active_plans(include_fallback=False):
    """Kept for backward compatibility."""
    plans_map = get_all_series_plans_map()
    active = [p for p in plans_map.values() if p.get("is_active")]
    if active:
        return active
    return [dict(DEFAULT_SEASON_PASS_PLAN)] if include_fallback else []


def get_series_pricing(series_row_or_id):
    """
    Given a `mock_test_series` row dict (or series_id string), returns:
      {"is_paid": bool, "price_inr": float, "plan": dict_or_None}
    Checks both columns on `mock_test_series` (`is_paid` / `is_premium` / `price_inr`)
    AND the synced `plans` table row (`code = 'series_<id>'`).
    """
    if isinstance(series_row_or_id, dict):
        series = series_row_or_id
        sid = str(series.get("id") or "")
    else:
        sid = str(series_row_or_id or "")
        series = {}
        if sid:
            try:
                series = (
                    supabase_admin.table("mock_test_series")
                    .select("*")
                    .eq("id", sid)
                    .maybe_single()
                    .execute()
                    .data or {}
                )
            except Exception:
                series = {}

    plans_map = get_all_series_plans_map()
    plan = plans_map.get(sid)

    # Check explicit columns on mock_test_series first if present
    col_paid = series.get("is_paid") if "is_paid" in series else series.get("is_premium")
    col_price = series.get("price_inr")

    if col_paid is not None:
        is_paid = bool(col_paid)
        try:
            price = round(float(col_price or (plan.get("price_inr") if plan else 0) or 0), 2)
        except (TypeError, ValueError):
            price = 0.0
    elif plan is not None:
        is_paid = bool(plan.get("is_active"))
        try:
            price = round(float(plan.get("price_inr") or 0), 2)
        except (TypeError, ValueError):
            price = 0.0
    else:
        is_paid = False
        price = 0.0

    if price <= 0:
        is_paid = False
        price = 0.0

    return {"is_paid": is_paid, "price_inr": price, "plan": plan}


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
    """The user's most recent active subscription row, or None."""
    if not user_id:
        return None
    now = _utc_stamp()
    try:
        res = (
            supabase_admin.table("subscriptions")
            .select("id, plan_id, stream_id, starts_at, expires_at, plans(name, code)")
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


def get_unlocked_series_ids_for_user(user_id):
    """
    Returns the set of `series_id` strings that `user_id` has unlocked via
    an approved order or active subscription.
    """
    if not user_id:
        return set()
    key = f"unlocked_series:{user_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    unlocked = set()
    plans_map = get_all_series_plans_map()
    plan_id_to_series = {p["id"]: sid for sid, p in plans_map.items() if p.get("id")}

    now = _utc_stamp()
    # 1. Check active subscriptions
    try:
        subs = (
            supabase_admin.table("subscriptions")
            .select("plan_id, status, starts_at, expires_at")
            .eq("user_id", user_id)
            .eq("status", "active")
            .execute()
            .data or []
        )
        for s in subs:
            if s.get("expires_at") and s["expires_at"] <= now:
                continue
            sid = plan_id_to_series.get(s.get("plan_id"))
            if sid:
                unlocked.add(sid)
    except Exception as e:
        logger.error(f"Error reading subscriptions for {user_id}: {e}")

    # 2. Check approved orders directly
    try:
        ords = (
            supabase_admin.table("orders")
            .select("plan_id, status")
            .eq("user_id", user_id)
            .eq("status", "approved")
            .execute()
            .data or []
        )
        for o in ords:
            sid = plan_id_to_series.get(o.get("plan_id"))
            if sid:
                unlocked.add(sid)
    except Exception as e:
        logger.error(f"Error reading approved orders for {user_id}: {e}")

    if unlocked:
        cache.set(key, unlocked, timeout=_POSITIVE_TTL)
    return unlocked


def user_has_series_access(series_row_or_id, user_id):
    """
    True if:
      * the Test Series is Free, OR
      * the user is an Admin, OR
      * the user has purchased and been approved for this specific Test Series.
    """
    pricing = get_series_pricing(series_row_or_id)
    if not pricing["is_paid"]:
        return True
    if not user_id:
        return False
    if is_admin_user(user_id):
        return True
    sid = series_row_or_id.get("id") if isinstance(series_row_or_id, dict) else str(series_row_or_id or "")
    if sid and sid in get_unlocked_series_ids_for_user(user_id):
        return True
    # Also honour legacy universal active subscription if one exists with no series prefix
    sub = get_active_subscription(user_id)
    if sub:
        plan_info = sub.get("plans") or {}
        if isinstance(plan_info, dict) and not extract_series_id_from_code(plan_info.get("code")):
            return True
    return False


def enrich_series_list(series_list, user_id=None):
    """
    Annotates a list of `mock_test_series` dicts with:
      * is_paid (bool)
      * price_inr (float)
      * has_access (bool)
      * pending_order_id (str or None)
    """
    if not series_list:
        return []
    is_admin = is_admin_user(user_id) if user_id else False
    unlocked_ids = get_unlocked_series_ids_for_user(user_id) if (user_id and not is_admin) else set()
    plans_map = get_all_series_plans_map()
    plan_id_to_series = {p["id"]: sid for sid, p in plans_map.items() if p.get("id")}

    pending_by_series = {}
    if user_id:
        try:
            open_ords = (
                supabase_admin.table("orders")
                .select("id, plan_id, status")
                .eq("user_id", user_id)
                .in_("status", ["submitted", "created"])
                .order("created_at", desc=True)
                .execute()
                .data or []
            )
            for o in open_ords:
                sid = plan_id_to_series.get(o.get("plan_id"))
                if sid and sid not in pending_by_series:
                    pending_by_series[sid] = o
        except Exception:
            pass

    out = []
    for s in series_list:
        row = dict(s)
        sid = str(row.get("id") or "")
        pricing = get_series_pricing(row)
        row["is_paid"] = pricing["is_paid"]
        row["price_inr"] = pricing["price_inr"]
        if not pricing["is_paid"]:
            row["has_access"] = True
        elif is_admin or (sid in unlocked_ids):
            row["has_access"] = True
        else:
            row["has_access"] = False
        pending_ord = pending_by_series.get(sid)
        row["pending_order_id"] = pending_ord["id"] if pending_ord else None
        row["pending_status"] = pending_ord["status"] if pending_ord else None
        out.append(row)
    return out


def user_has_premium(user_id):
    """Admin, or holder of an active subscription. Kept for compatibility."""
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
    """Call right after granting/revoking access so this worker forgets cached state."""
    if user_id:
        cache.delete(f"premium:{user_id}")
        cache.delete(f"unlocked_series:{user_id}")


def can_access_test(test, user_id):
    """
    Mock-test access rule:
      1. If the test belongs to a Test Series folder (`series_id`):
         - If that Test Series folder is FREE -> everyone has access.
         - If that Test Series folder is PAID -> requires `user_has_series_access(series_id, user_id)`
           or a legacy per-test grant.
      2. If the test has no `series_id` (standalone):
         - Free if `not test.get("is_premium")`, else requires admin / subscription / grant.
    """
    if not test:
        return False
    series_id = test.get("series_id")
    if series_id:
        pricing = get_series_pricing(series_id)
        if not pricing["is_paid"] and not test.get("is_premium"):
            return True
        if pricing["is_paid"]:
            if not user_id:
                return False
            if user_has_series_access(series_id, user_id):
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
            except Exception:
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


def premium_status():
    """
    Template helper dict. Season Pass offer promotions are removed;
    tracks `is_admin` and `active` status cleanly.
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
                    plan_name=(sub.get("plans") or {}).get("name") or "Unlocked Series",
                    expires_label=format_ist_date(expires) if expires else None,
                )

    g._premium_status = status
    return status
