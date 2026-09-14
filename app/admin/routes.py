from flask import render_template, redirect, url_for, flash

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.extensions import supabase_admin
from app.admin.image_routes import BUCKET_NAME


@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    """
    Landing page after admin login. Shows quick content counts so it's
    obvious at a glance what's populated and what still needs data.
    """
    def _count(table):
        res = supabase_admin.table(table).select("id", count="exact").execute()
        return res.count or 0

    def _count_pending_images(table):
        res = (
            supabase_admin.table(table)
            .select("id", count="exact")
            .eq("has_image", True)
            .is_("image_url", "null")
            .execute()
        )
        return res.count or 0

    counts = {
        "streams": _count("streams"),
        "subjects": _count("subjects"),
        "chapters": _count("chapters"),
        "questions": _count("questions"),
        "tests": _count("tests"),
    }

    # FEATURE: Garbage tracking (to show Admin how much junk can be cleaned).
    # Check constraint: either user_id or guest_id is set. 
    # So is_('user_id', 'null') perfectly filters out guest attempts safely.
    garbage_info = {
        "guest_attempts": supabase_admin.table("test_attempts").select("id", count="exact").is_("user_id", "null").execute().count or 0,
        "incomplete_attempts": supabase_admin.table("test_attempts").select("id", count="exact").is_("submitted_at", "null").execute().count or 0,
    }

    # Storage usage: every uploaded image is compressed to ~30-100KB
    # (see app/admin/image_routes.py compress_image()), so counting
    # objects and estimating at a conservative 100KB/image gives a
    # useful "how close to the 1GB free-tier limit am I" signal
    # without needing to sum actual byte sizes via the Storage API.
    uploaded_count = 0
    try:
        # list() on the bucket root only returns top-level "folders"
        # (questions/, mock_questions/) not a recursive file count, so
        # walk one level down per table prefix instead.
        for prefix in ("questions", "mock_questions"):
            folders = supabase_admin.storage.from_(BUCKET_NAME).list(prefix)
            for folder in folders or []:
                files = supabase_admin.storage.from_(BUCKET_NAME).list(f"{prefix}/{folder['name']}")
                uploaded_count += len(files or [])
    except Exception:
        # Bucket may not exist yet if the one-time Storage setup step
        # (sql/migration_question_images.sql) hasn't been done — don't
        # let that break the whole dashboard.
        uploaded_count = None

    storage_info = {
        "uploaded_count": uploaded_count,
        "estimated_mb": round((uploaded_count or 0) * 100 / 1024, 1) if uploaded_count is not None else None,
        "pending_questions": _count_pending_images("questions"),
        "pending_mock_questions": _count_pending_images("mock_questions"),
    }

    return render_template("admin_dashboard.html", counts=counts, storage_info=storage_info, garbage_info=garbage_info)


@admin_bp.route("/cleanup/guests", methods=["POST"])
@admin_required
def cleanup_guest_attempts():
    """
    Nuclear option for Admin: Deletes all test attempts made by Guests.
    Because of the DB's ON DELETE CASCADE, this automatically wipes millions 
    of linked `attempt_answers` rows instantly, saving massive DB storage.
    """
    try:
        supabase_admin.table("test_attempts").delete().is_("user_id", "null").execute()
        flash("All Guest attempts and their data have been permanently wiped.", "success")
    except Exception as exc:
        flash(f"Cleanup failed: {exc}", "error")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/cleanup/incomplete", methods=["POST"])
@admin_required
def cleanup_incomplete_attempts():
    """
    Nuclear option for Admin: Deletes all abandoned test attempts (never submitted).
    Frees up storage taken by students who started a test but closed the tab.
    """
    try:
        supabase_admin.table("test_attempts").delete().is_("submitted_at", "null").execute()
        flash("All abandoned/incomplete test attempts have been permanently wiped.", "success")
    except Exception as exc:
        flash(f"Cleanup failed: {exc}", "error")
    return redirect(url_for("admin.dashboard"))
