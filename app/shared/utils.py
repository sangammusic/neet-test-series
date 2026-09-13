"""
Helpers used by both admin and user blueprints.
"""
import uuid
from flask import request, session

GUEST_COOKIE_NAME = "sangam_guest_id"
GUEST_COOKIE_MAX_AGE = 60 * 60 * 24 * 180  # 180 days


def get_or_create_guest_id():
    """
    Reads the guest_id cookie if present. If not, generates a new
    UUID — the caller is responsible for setting it on the response
    (see set_guest_cookie below) and inserting a row into `guests`.

    Returns (guest_id: str, is_new: bool)
    """
    existing = request.cookies.get(GUEST_COOKIE_NAME)
    if existing:
        return existing, False
    return str(uuid.uuid4()), True


def set_guest_cookie(response, guest_id):
    response.set_cookie(
        GUEST_COOKIE_NAME,
        guest_id,
        max_age=GUEST_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
    )
    return response


def current_user_id():
    """Returns the logged-in Supabase auth user id, or None."""
    return session.get("user_id")


def is_logged_in():
    return current_user_id() is not None
