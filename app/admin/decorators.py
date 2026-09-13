"""
@admin_required wraps every route in app/admin/*. It checks the
logged-in user's role_id in `profiles` — NOT a session flag the
client could theoretically tamper with — every single request.
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

        res = (
            supabase_admin.table("profiles")
            .select("role_id")
            .eq("id", user_id)
            .single()
            .execute()
        )
        if not res.data or res.data["role_id"] != 2:  # 2 = admin, from roles table
            abort(403)

        return view_func(*args, **kwargs)

    return wrapped
