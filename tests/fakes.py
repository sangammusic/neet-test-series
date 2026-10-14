"""
A tiny in-memory stand-in for Supabase, so the tests never touch a real database,
a real login service or Telegram.

It understands only the small slice of the query builder that this app uses
(select / insert / update / delete, eq / neq / in_ / lt / lte / gt / gte / is_,
order / limit / maybe_single, and "plans(name)"-style embeds).

The payment SQL functions (create_order, submit_order, approve_order, ...) are
re-implemented below in Python with the SAME rules as sql/phase1_payments.sql,
so the web-side flow (routes, templates, Telegram webhook) can be tested end to end.
(The SQL itself is checked by running the script once in Supabase -- see the setup guide.)
"""
import functools
import random
import re
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone


def ts(delta_minutes=0):
    """Timestamp in the same fixed-width format app code uses, so string compare == time compare."""
    t = datetime.now(timezone.utc) + timedelta(minutes=delta_minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


# which column links a table to the table it embeds, e.g. orders.plan_id -> plans.id
EMBED_FK = {
    ("orders", "plans"): ("plan_id", "plans"),
    ("orders", "profiles"): ("user_id", "profiles"),
    ("subscriptions", "plans"): ("plan_id", "plans"),
}


class FakeDB:
    def __init__(self):
        self.reset()

    def reset(self):
        self.tables = {}
        self.fail_tables = set()
        self.auth_users = {}            # email -> {"id", "password"}
        self.create_user_error = None   # set to an Exception to make signup blow up

    def rows(self, name):
        return self.tables.setdefault(name, [])

    def insert(self, name, row):
        row = dict(row)
        row.setdefault("id", str(uuid.uuid4()))
        self.rows(name).append(row)
        return row

    def add_auth_user(self, username, password):
        uid = str(uuid.uuid4())
        self.auth_users[f"{username}@sangam.local"] = {"id": uid, "password": password}
        return uid


class Query:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.op, self.cols, self.payload = "select", "*", None
        self.filters, self._order, self._limit, self._single = [], None, None, None

    # ---- builders
    def select(self, cols="*", **_):
        self.op, self.cols = "select", cols
        return self

    def insert(self, rows):
        self.op, self.payload = "insert", rows
        return self

    def update(self, vals):
        self.op, self.payload = "update", vals
        return self

    def delete(self):
        self.op = "delete"
        return self

    def _f(self, col, op, val):
        self.filters.append((col, op, val))
        return self

    def eq(self, c, v): return self._f(c, "eq", v)
    def neq(self, c, v): return self._f(c, "neq", v)
    def in_(self, c, v): return self._f(c, "in", list(v))
    def lt(self, c, v): return self._f(c, "lt", v)
    def lte(self, c, v): return self._f(c, "lte", v)
    def gt(self, c, v): return self._f(c, "gt", v)
    def gte(self, c, v): return self._f(c, "gte", v)

    def is_(self, c, v):
        return self._f(c, "is", None if v in ("null", None) else v)

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, a, b):
        self._limit = b - a + 1
        return self

    def maybe_single(self):
        self._single = "maybe"
        return self

    def single(self):
        self._single = "single"
        return self

    # ---- execution
    def _match(self, row):
        for col, op, val in self.filters:
            cur = row.get(col)
            if op == "eq" and cur != val: return False
            if op == "neq" and cur == val: return False
            if op == "in" and cur not in val: return False
            if op == "is" and cur != val: return False
            if cur is None and op in ("lt", "lte", "gt", "gte"): return False
            if op == "lt" and not cur < val: return False
            if op == "lte" and not cur <= val: return False
            if op == "gt" and not cur > val: return False
            if op == "gte" and not cur >= val: return False
        return True

    def _project(self, row):
        if self.cols.strip() == "*":
            return dict(row)
        embeds = re.findall(r"(\w+)\(([^()]*)\)", self.cols)
        flat = re.sub(r"\w+\([^()]*\)", "", self.cols)
        out = {c.strip(): row.get(c.strip()) for c in flat.split(",") if c.strip()}
        for emb_name, emb_cols in embeds:
            fk = EMBED_FK.get((self.name, emb_name))
            target = None
            if fk:
                fk_col, tbl = fk
                target = next((r for r in self.db.rows(tbl) if r.get("id") == row.get(fk_col)), None)
            out[emb_name] = None if target is None else {
                c.strip(): target.get(c.strip()) for c in emb_cols.split(",") if c.strip()
            }
        return out

    def execute(self):
        if self.name in self.db.fail_tables:
            raise RuntimeError("simulated database failure")
        rows = self.db.rows(self.name)

        if self.op == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            return Resp([self.db.insert(self.name, p) for p in payload])

        matched = [r for r in rows if self._match(r)]

        if self.op == "update":
            for r in matched:
                r.update(self.payload)
            return Resp([dict(r) for r in matched])

        if self.op == "delete":
            for r in matched:
                rows.remove(r)
            return Resp([dict(r) for r in matched])

        if self._order:
            col, desc = self._order
            matched.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        if self._limit is not None:
            matched = matched[: self._limit]
        out = [self._project(r) for r in matched]
        if self._single in ("maybe", "single"):
            return Resp(out[0] if out else None)
        return Resp(out)


# ----------------------------------------------------------------------
# Python mirrors of sql/phase1_payments.sql  (same rules, same return shapes)
# ----------------------------------------------------------------------

def _open_status(o):
    return o["status"] in ("created", "submitted")


def rpc_create_order(db, p):
    plan = next((x for x in db.rows("plans") if x["code"] == p["p_plan_code"] and x.get("is_active", True)), None)
    if not plan:
        return {"ok": False, "error": "plan_not_found"}
    now = ts()
    for o in db.rows("orders"):
        if o["status"] == "created" and o["expires_at"] < now:
            o["status"] = "expired"
    uid = p["p_user_id"]
    for s in db.rows("subscriptions"):
        if s["user_id"] == uid and s["plan_id"] == plan["id"] and s["status"] == "active" and s["starts_at"] <= now < s["expires_at"]:
            return {"ok": False, "error": "already_active"}
    mine = [o for o in db.rows("orders") if o["user_id"] == uid and o["plan_id"] == plan["id"] and _open_status(o)]
    if mine:
        mine.sort(key=lambda o: o["created_at"], reverse=True)
        return {"ok": True, "reused": True, "order_id": mine[0]["id"]}
    taken = {round(float(o["payable_amount"]), 2) for o in db.rows("orders") if _open_status(o)}
    free = [k for k in range(1, 100) if round(float(plan["price_inr"]) + k / 100.0, 2) not in taken]
    if not free:
        return {"ok": False, "error": "busy"}
    k = random.choice(free)
    order = db.insert("orders", {
        "user_id": uid, "plan_id": plan["id"], "base_amount": float(plan["price_inr"]),
        "payable_amount": round(float(plan["price_inr"]) + k / 100.0, 2), "status": "created",
        "utr": None, "tg_file_id": None, "tg_message_id": None, "reject_reason": None,
        "reviewed_by": None, "reviewed_at": None, "submitted_at": None,
        "expires_at": ts(45), "created_at": ts(),
    })
    return {"ok": True, "reused": False, "order_id": order["id"]}


def rpc_submit_order(db, p):
    order = next((o for o in db.rows("orders") if o["id"] == p["p_order_id"] and o["user_id"] == p["p_user_id"]), None)
    if not order:
        return {"ok": False, "error": "not_found"}
    if order["status"] == "submitted":
        return {"ok": True, "already": True} if order["utr"] == p["p_utr"] else {"ok": False, "error": "already_submitted"}
    if order["status"] == "approved":
        return {"ok": False, "error": "already_approved"}
    if order["status"] in ("rejected", "cancelled"):
        return {"ok": False, "error": "closed"}
    if any(o["utr"] == p["p_utr"] and o["id"] != order["id"] for o in db.rows("orders")):
        return {"ok": False, "error": "utr_used"}
    if any(o["id"] != order["id"] and _open_status(o) and o["payable_amount"] == order["payable_amount"] for o in db.rows("orders")):
        return {"ok": False, "error": "amount_reassigned"}
    order.update(status="submitted", utr=p["p_utr"], submitted_at=ts())
    return {"ok": True, "already": False}


def rpc_approve_order(db, p):
    order = next((o for o in db.rows("orders") if o["id"] == p["p_order_id"]), None)
    if not order:
        return {"ok": False, "error": "not_found"}
    if order["status"] == "approved":
        return {"ok": True, "already": True, "user_id": order["user_id"]}
    if order["status"] not in ("submitted", "rejected"):
        return {"ok": False, "error": "bad_status", "status": order["status"]}
    if order["user_id"] is None:
        return {"ok": False, "error": "user_deleted"}
    plan = next(x for x in db.rows("plans") if x["id"] == order["plan_id"])
    expires = plan.get("valid_until") or ts(60 * 24 * int(plan.get("valid_days") or 0))
    if expires <= ts():
        return {"ok": False, "error": "plan_expired"}
    order.update(status="approved", reviewed_by=p["p_reviewer"], reviewed_at=ts(), reject_reason=None)
    if not any(s.get("order_id") == order["id"] for s in db.rows("subscriptions")):
        db.insert("subscriptions", {
            "user_id": order["user_id"], "plan_id": plan["id"], "stream_id": plan.get("stream_id"),
            "order_id": order["id"], "source": "payment", "status": "active",
            "starts_at": ts(-1), "expires_at": expires, "created_at": ts(),
        })
    return {"ok": True, "already": False, "user_id": order["user_id"], "expires_at": expires}


def rpc_reject_order(db, p):
    order = next((o for o in db.rows("orders") if o["id"] == p["p_order_id"]), None)
    if not order:
        return {"ok": False, "error": "not_found"}
    if order["status"] == "rejected":
        return {"ok": True, "already": True, "user_id": order["user_id"]}
    if order["status"] != "submitted":
        return {"ok": False, "error": "bad_status", "status": order["status"]}
    order.update(status="rejected", reject_reason=(p["p_reason"] or "Payment could not be verified.")[:200],
                 reviewed_by=p["p_reviewer"], reviewed_at=ts())
    return {"ok": True, "already": False, "user_id": order["user_id"]}


def rpc_revoke_order(db, p):
    order = next((o for o in db.rows("orders") if o["id"] == p["p_order_id"]), None)
    if not order:
        return {"ok": False, "error": "not_found"}
    if order["status"] != "approved":
        return {"ok": False, "error": "bad_status", "status": order["status"]}
    for s in db.rows("subscriptions"):
        if s.get("order_id") == order["id"]:
            s["status"] = "revoked"
    order.update(status="cancelled", reject_reason=p["p_reason"], reviewed_by=p["p_reviewer"], reviewed_at=ts())
    return {"ok": True, "user_id": order["user_id"]}


def rpc_payments_summary(db, p):
    approved = [o for o in db.rows("orders") if o["status"] == "approved"]
    total = round(sum(o["payable_amount"] for o in approved), 2)
    return {
        "revenue_today": total, "revenue_month": total, "revenue_total": total,
        "approved_count": len(approved),
        "pending_count": len([o for o in db.rows("orders") if o["status"] == "submitted"]),
        "rejected_count": len([o for o in db.rows("orders") if o["status"] == "rejected"]),
    }


RPCS = {
    "create_order": rpc_create_order, "submit_order": rpc_submit_order,
    "approve_order": rpc_approve_order, "reject_order": rpc_reject_order,
    "revoke_order": rpc_revoke_order, "payments_summary": rpc_payments_summary,
}


class _RpcCall:
    def __init__(self, db, name, params):
        self.db, self.name, self.params = db, name, params

    def execute(self):
        if "rpc" in self.db.fail_tables:
            raise RuntimeError("simulated rpc failure")
        return Resp(RPCS[self.name](self.db, self.params))


# ----------------------------------------------------------------------
# Auth + client
# ----------------------------------------------------------------------

class FakeAuthAdmin:
    def __init__(self, db):
        self.db = db

    def create_user(self, attrs):
        if self.db.create_user_error:
            raise self.db.create_user_error
        email = attrs["email"]
        if email in self.db.auth_users:
            raise Exception("A user with this email address has already been registered")
        uid = str(uuid.uuid4())
        self.db.auth_users[email] = {"id": uid, "password": attrs["password"]}
        return types.SimpleNamespace(user=types.SimpleNamespace(id=uid, email=email))

    def delete_user(self, uid):
        for email, u in list(self.db.auth_users.items()):
            if u["id"] == uid:
                del self.db.auth_users[email]

    def get_user_by_id(self, uid):
        for email, u in self.db.auth_users.items():
            if u["id"] == uid:
                return types.SimpleNamespace(user=types.SimpleNamespace(id=uid, email=email))
        return types.SimpleNamespace(user=None)


class FakeAuth:
    def __init__(self, db):
        self.db = db
        self.admin = FakeAuthAdmin(db)

    def sign_in_with_password(self, creds):
        user = self.db.auth_users.get(creds["email"])
        if not user or user["password"] != creds["password"]:
            raise Exception("Invalid login credentials")
        return types.SimpleNamespace(
            user=types.SimpleNamespace(id=user["id"], email=creds["email"]),
            session=types.SimpleNamespace(access_token="tok-" + user["id"]),
        )


class FakeClient:
    def __init__(self, db):
        self.db = db
        self.auth = FakeAuth(db)

    def table(self, name):
        return Query(self.db, name)

    def rpc(self, name, params=None):
        return _RpcCall(self.db, name, params or {})


class _FakeCache:
    """Only used when flask_caching isn't installed in the machine running the tests."""

    def __init__(self, config=None, app=None):
        self._store = {}

    def init_app(self, app):
        pass

    def get(self, key):
        return self._store.get(key)

    def set(self, key, value, timeout=None):
        self._store[key] = value
        return True

    def delete(self, key):
        self._store.pop(key, None)

    def clear(self):
        self._store.clear()

    def _passthrough(self, *dargs, **dkwargs):
        def deco(fn):
            @functools.wraps(fn)
            def wrapper(*a, **kw):
                return fn(*a, **kw)
            return wrapper
        return deco

    memoize = _passthrough
    cached = _passthrough


def install(db):
    """Puts the fake `supabase` module (and, if needed, a fake `flask_caching`) into sys.modules."""
    fake_supabase = types.ModuleType("supabase")
    fake_supabase.create_client = lambda url, key: FakeClient(db)
    fake_supabase.Client = FakeClient
    sys.modules["supabase"] = fake_supabase
    try:
        import flask_caching  # noqa: F401
    except ImportError:
        fake_cache = types.ModuleType("flask_caching")
        fake_cache.Cache = _FakeCache
        sys.modules["flask_caching"] = fake_cache
