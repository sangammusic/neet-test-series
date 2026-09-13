from flask import render_template, request, redirect, url_for, session

from app.user import user_bp
from app.extensions import supabase_admin
from app.shared.models import (
    get_active_streams,
    get_stream_by_slug,
    get_test_categories_for_stream,
)
from app.shared.utils import get_or_create_guest_id, is_logged_in, current_user_id


@user_bp.route("/")
def landing():
    """
    Step 1 of onboarding: Login/Register vs Continue as Guest.
    If they already have a session (logged in OR guest cookie with
    a stream already chosen), skip straight past this screen.
    """
    if is_logged_in():
        return redirect(url_for("user.stream_select"))
    return render_template("landing.html")


@user_bp.route("/streams", methods=["GET", "POST"])
def stream_select():
    """
    Step 2: pick one or more streams. Works identically for
    logged-in users and guests — only the storage table differs.
    """
    streams = get_active_streams()

    if request.method == "GET":
        return render_template("stream_select.html", streams=streams)

    selected_ids = request.form.getlist("stream_ids")  # multi-select checkboxes
    if not selected_ids:
        return render_template(
            "stream_select.html", streams=streams, error="Pick at least one stream."
        )

    if is_logged_in():
        user_id = current_user_id()
        rows = [{"user_id": user_id, "stream_id": sid} for sid in selected_ids]
        # upsert so re-selecting doesn't throw a duplicate-key error
        supabase_admin.table("user_streams").upsert(rows).execute()
    else:
        guest_id, is_new = get_or_create_guest_id()
        if is_new:
            supabase_admin.table("guests").insert({"guest_id": guest_id}).execute()
        rows = [{"guest_id": guest_id, "stream_id": sid} for sid in selected_ids]
        supabase_admin.table("guest_streams").upsert(rows).execute()

    session["active_stream_ids"] = selected_ids
    # Land them on the first selected stream's dashboard
    first_stream = next((s for s in streams if s["id"] == selected_ids[0]), None)
    return redirect(url_for("user.stream_dashboard", slug=first_stream["slug"]))


@user_bp.route("/streams/<slug>")
def stream_dashboard(slug):
    """
    Step 3: Free / Paid tabs for the chosen stream.
    """
    stream = get_stream_by_slug(slug)
    if not stream:
        return render_template("shared/404.html"), 404

    categories = get_test_categories_for_stream(stream["id"])
    free_categories = [c for c in categories if not c["is_premium"]]
    paid_categories = [c for c in categories if c["is_premium"]]

    return render_template(
        "dashboard.html",
        stream=stream,
        free_categories=free_categories,
        paid_categories=paid_categories,
    )


@user_bp.route("/switch-stream")
def switch_stream():
    """Lets a user with multiple streams jump between them anytime."""
    return redirect(url_for("user.stream_select"))
