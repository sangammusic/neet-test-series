"""
@admin_required wraps every route in app/admin/*. It checks the
logged-in user's role_id in `profiles` -- NOT a session flag the
client could theoretically tamper with -- every single request.
"""
from functools import wraps
from flask import session, abort

from app.extensions import supabase_admin


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            abort(403)

        # BUGFIX: .single() used to be used here. .single() throws a
        # hard error (PGRST116, "result contains 0 rows") -- and
        # crashes with a 500 -- the moment the profiles row for this
        # session's user_id doesn't exist. That happened whenever a
        # profile row was missing or out of sync with auth.users
        # (e.g. from an account being deleted/recreated by hand).
        # A missing profile should mean "not an admin" (403), never
        # a server crash (500). Using a plain select + checking the
        # list ourselves makes 0 rows a normal, handled case instead
        # of an exception.
        res = (
            supabase_admin.table("profiles")
            .select("role_id")
            .eq("id", user_id)
            .execute()
        )
        rows = res.data or []
        if not rows or rows[0]["role_id"] != 2:  # 2 = admin, from roles table
            abort(403)

        return view_func(*args, **kwargs)

    return wrapped
