"""
Helpers used by both admin and user blueprints.

NOTE: Guest-mode support (guest_id cookie, anonymous guest rows) has
been permanently removed from this app. Every route now requires a
real logged-in account -- see app/user/routes.py and
app/auth/routes.py. If you're looking for get_or_create_guest_id /
set_guest_cookie / GUEST_COOKIE_NAME, they used to live here; they
were deleted on purpose, not lost by accident.
"""
from flask import session


def current_user_id():
    """Returns the logged-in Supabase auth user id, or None."""
    return session.get("user_id")


def is_logged_in():
    return current_user_id() is not None
