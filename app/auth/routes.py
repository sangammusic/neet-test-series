from flask import render_template, request, redirect, url_for, session, flash

from app.auth import auth_bp
from app.extensions import supabase_public
from app.shared.utils import get_or_create_guest_id, set_guest_cookie


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")

    try:
        result = supabase_public.auth.sign_in_with_password(
            {"email": email, "password": password}
        )
    except Exception:
        flash("Invalid email or password.", "error")
        return render_template("login.html"), 401

    if not result.session:
        # This happens when "Confirm email" is enabled in Supabase Auth
        # settings and the user hasn't clicked the confirmation link yet.
        # sign_in_with_password doesn't raise in this case — it just
        # returns a user with session=None — so this has to be checked
        # explicitly or the next two lines crash with an AttributeError.
        flash("Please confirm your email before logging in — check your inbox.", "error")
        return render_template("login.html"), 401

    session["user_id"] = result.user.id
    session["access_token"] = result.session.access_token
    return redirect(url_for("user.stream_select"))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")

    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    full_name = request.form.get("full_name", "").strip()

    try:
        result = supabase_public.auth.sign_up(
            {"email": email, "password": password}
        )
    except Exception as exc:
        flash(f"Registration failed: {exc}", "error")
        return render_template("register.html"), 400

    # Create the matching profiles row (role_id defaults to student=1)
    if result.user:
        supabase_public.table("profiles").insert(
            {"id": result.user.id, "full_name": full_name}
        ).execute()

    flash("Account created — please check your email to confirm, then log in.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("user.landing"))


@auth_bp.route("/guest/start")
def guest_start():
    """
    Entry point for 'Continue without Login'. Ensures a guest_id
    cookie + row exist, then sends them to stream selection.
    """
    guest_id, is_new = get_or_create_guest_id()

    if is_new:
        from app.extensions import supabase_admin
        supabase_admin.table("guests").insert({"guest_id": guest_id}).execute()

    response = redirect(url_for("user.stream_select"))
    if is_new:
        response = set_guest_cookie(response, guest_id)
    return response
