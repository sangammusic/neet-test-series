from flask import render_template, request, redirect, url_for, session, flash

from app.auth import auth_bp
from app.extensions import supabase_public, supabase_admin
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

    # --- TEMP: email verification bypass (see Task A note below) ---
    # We use supabase_admin.auth.admin.create_user() instead of the
    # normal supabase_public.auth.sign_up(). The admin create_user
    # endpoint accepts email_confirm=True, which marks the user as
    # already confirmed at creation time — no confirmation email is
    # sent, and sign_in_with_password works on the very next request.
    #
    # This does NOT depend on the "Confirm email" toggle in the
    # Supabase Dashboard (Authentication -> Providers -> Email). It
    # works whether that toggle is on or off, because email_confirm
    # is being set explicitly per-user here, not inherited from the
    # project-wide setting. That's deliberate: if the dashboard
    # toggle gets flipped back on later for some other reason, this
    # route keeps working without anyone having to remember why.
    #
    # TO RE-ENABLE EMAIL VERIFICATION LATER: replace the
    # supabase_admin.auth.admin.create_user(...) call below with the
    # original supabase_public.auth.sign_up(...) call (kept in a
    # comment further down), and change the flash message back to
    # "check your email to confirm."
    try:
        result = supabase_admin.auth.admin.create_user(
            {
                "email": email,
                "password": password,
                "email_confirm": True,  # <-- the actual bypass
            }
        )
    except Exception as exc:
        flash(f"Registration failed: {exc}", "error")
        return render_template("register.html"), 400

    # ORIGINAL (email-verification-required) call, for reference:
    # result = supabase_public.auth.sign_up(
    #     {"email": email, "password": password}
    # )

    # Create the matching profiles row (role_id defaults to student=1).
    # Uses supabase_admin here (not supabase_public) because RLS on
    # `profiles` only allows a row to be inserted by an authenticated
    # session matching auth.uid() — and at this point in the request
    # there IS no session yet (that's created by sign_in_with_password
    # a few lines down). supabase_admin bypasses RLS so the profile
    # can be created before the session exists.
    if result.user:
        supabase_admin.table("profiles").insert(
            {"id": result.user.id, "full_name": full_name}
        ).execute()
    else:
        # create_user succeeded but returned no user — shouldn't
        # normally happen, but don't silently continue if it does.
        flash("Registration failed: no user returned.", "error")
        return render_template("register.html"), 400

    # Immediately sign in, since the user is already confirmed — no
    # need to send them to the login page and make them log in twice.
    try:
        sign_in_result = supabase_public.auth.sign_in_with_password(
            {"email": email, "password": password}
        )
    except Exception:
        # Account was created fine; auto-login just didn't go through
        # (rare, but don't block registration on it). Fall back to
        # sending them to the login page instead of erroring out.
        flash("Account created — please log in.", "success")
        return redirect(url_for("auth.login"))

    if not sign_in_result.session:
        flash("Account created — please log in.", "success")
        return redirect(url_for("auth.login"))

    session["user_id"] = sign_in_result.user.id
    session["access_token"] = sign_in_result.session.access_token
    flash("Account created — you're all set.", "success")
    return redirect(url_for("user.stream_select"))


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
        supabase_admin.table("guests").insert({"guest_id": guest_id}).execute()

    response = redirect(url_for("user.stream_select"))
    if is_new:
        response = set_guest_cookie(response, guest_id)
    return response
