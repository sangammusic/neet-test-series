import re
from flask import render_template, request, redirect, url_for, session, flash

from app.auth import auth_bp
from app.extensions import supabase_public, supabase_admin
from app.shared.utils import is_logged_in


def is_valid_username(username):
    """
    Security check: Only allows alphanumeric characters and underscores.
    Blocks spaces, @, dots, and other special characters to prevent injection.
    """
    return bool(re.match(r"^[a-zA-Z0-9_]+$", username))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if is_logged_in():
            return redirect(url_for("user.start_learning"))
        return render_template("login.html")

    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")

    # 1. Backend Validation
    if not username or not is_valid_username(username):
        flash("Invalid username format. Only letters, numbers, and underscores allowed.", "error")
        return render_template("login.html"), 400

    # 2. Convert to Dummy Email
    dummy_email = f"{username}@sangam.local"

    try:
        result = supabase_public.auth.sign_in_with_password(
            {"email": dummy_email, "password": password}
        )
    except Exception:
        flash("Invalid username or password.", "error")
        return render_template("login.html"), 401

    if not result.session:
        flash("Something went wrong. Please try again.", "error")
        return render_template("login.html"), 401

    session.permanent = True
    session["user_id"] = result.user.id
    session["access_token"] = result.session.access_token
    return redirect(url_for("user.start_learning"))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        if is_logged_in():
            return redirect(url_for("user.start_learning"))
        return render_template("register.html")

    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")
    full_name = request.form.get("full_name", "").strip()

    # 1. Backend Validation (Injection Prevention)
    if not username or not is_valid_username(username):
        flash("Invalid username. Only letters, numbers, and underscores are allowed (no spaces or @).", "error")
        return render_template("register.html"), 400

    # 2. Password must be entered twice and must match.
    # (This mirrors the two-step confirmation already used on the
    # settings/change-password flow -- registration was the one place
    # in the app that only asked once, which is what this fixes.)
    if not password or len(password) < 6:
        flash("Password must be at least 6 characters.", "error")
        return render_template("register.html"), 400

    if not confirm_password:
        flash("Please confirm your password.", "error")
        return render_template("register.html"), 400

    if password != confirm_password:
        flash("Password and confirmation do not match.", "error")
        return render_template("register.html"), 400

    # 3. Convert to Dummy Email
    dummy_email = f"{username}@sangam.local"

    try:
        result = supabase_admin.auth.admin.create_user(
            {
                "email": dummy_email,
                "password": password,
                "email_confirm": True,
            }
        )
    except Exception as exc:
        error_msg = str(exc)
        # Catch duplicate dummy email error from Supabase
        if "already registered" in error_msg.lower() or "unique" in error_msg.lower():
            flash(f"Username '{username}' is already taken! Please choose another one.", "error")
        else:
            flash(f"Registration failed: {error_msg}", "error")
        return render_template("register.html"), 400

    if result.user:
        try:
            # Save the unique username to the profiles table
            supabase_admin.table("profiles").insert(
                {
                    "id": result.user.id,
                    "full_name": full_name,
                    "username": username
                }
            ).execute()
        except Exception as db_exc:
            # Failsafe: If profiles insert fails, delete the auth user so they aren't stuck in limbo
            supabase_admin.auth.admin.delete_user(result.user.id)
            flash(f"Username '{username}' is already taken! Please choose another.", "error")
            return render_template("register.html"), 400
    else:
        flash("Registration failed: no user returned.", "error")
        return render_template("register.html"), 400

    try:
        sign_in_result = supabase_public.auth.sign_in_with_password(
            {"email": dummy_email, "password": password}
        )
    except Exception:
        flash("Account created — please log in.", "success")
        return redirect(url_for("auth.login"))

    if not sign_in_result.session:
        flash("Account created — please log in.", "success")
        return redirect(url_for("auth.login"))

    session.permanent = True
    session["user_id"] = sign_in_result.user.id
    session["access_token"] = sign_in_result.session.access_token
    flash("Account created — you're all set.", "success")
    return redirect(url_for("user.start_learning"))


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


# NOTE: guest_start() has been permanently removed. There is no more
# "Continue without login" path anywhere in this app -- every visitor
# hits the login page first. See app/user/routes.py: landing() now
# redirects straight to auth.login for anonymous visitors.
