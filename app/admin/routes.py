import logging
from flask import render_template, redirect, url_for, flash

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.extensions import supabase_admin
from app.admin.image_routes import BUCKET_NAME

# Setup basic logger to catch silent errors without crashing the dashboard
logger = logging.getLogger(__name__)

@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    """
    Landing page after admin login. Shows quick content counts.
    Optimized: Added strict error handling and .limit(1) to make 
    count queries drastically faster via PostgREST.
    """
    def _count(table):
        try:
            # limit(1) is a massive performance boost for count="exact" queries in Supabase
            res = supabase_admin.table(table).select("id", count="exact").limit(1).execute()
            return res.count or 0
        except Exception as e:
            logger.error(f"Error counting {table}: {e}")
            return 0

    def _count_pending_images(table):
        try:
            res = (
                supabase_admin.table(table)
                .select("id", count="exact")
                .eq("has_image", True)
                .is_("image_url", "null")
                .limit(1)
                .execute()
            )
            return res.count or 0
        except Exception as e:
            logger.error(f"Error counting pending images in {table}: {e}")
            return 0

    counts = {
        "streams": _count("streams"),
        "subjects": _count("subjects"),
        "chapters": _count("chapters"),
        "questions": _count("questions"),
        "tests": _count("tests"),
    }

    # FEATURE: Garbage tracking (Guest and Abandoned attempts).
    try:
        guest_count = supabase_admin.table("test_attempts").select("id", count="exact").is_("user_id", "null").limit(1).execute().count or 0
    except Exception:
        guest_count = 0

    try:
        incomplete_count = supabase_admin.table("test_attempts").select("id", count="exact").is_("submitted_at", "null").limit(1).execute().count or 0
    except Exception:
        incomplete_count = 0

    garbage_info = {
        "guest_attempts": guest_count,
        "incomplete_attempts": incomplete_count,
    }

    # Storage usage estimation (~100KB per compressed image)
    uploaded_count = 0
    try:
        for prefix in ("questions", "mock_questions"):
            folders = supabase_admin.storage.from_(BUCKET_NAME).list(prefix)
            for folder in folders or []:
                # Ignore hidden system files (like .emptyFolderPlaceholder)
                if folder.get('name', '').startswith('.'):
                    continue
                files = supabase_admin.storage.from_(BUCKET_NAME).list(f"{prefix}/{folder['name']}")
                
                # Count only valid image files
                valid_files = [f for f in (files or []) if not f.get('name', '').startswith('.')]
                uploaded_count += len(valid_files)
    except Exception as e:
        logger.warning(f"Storage bucket list failed: {e}")
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
        res = supabase_admin.table("test_attempts").delete().is_("user_id", "null").execute()
        # Fetch actual deleted count for a more satisfying UI message
        deleted_count = len(res.data) if res.data else 0
        flash(f"Success: {deleted_count} Guest attempts and their data have been permanently wiped.", "success")
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
        res = supabase_admin.table("test_attempts").delete().is_("submitted_at", "null").execute()
        # Fetch actual deleted count
        deleted_count = len(res.data) if res.data else 0
        flash(f"Success: {deleted_count} abandoned/incomplete test attempts have been permanently wiped.", "success")
    except Exception as exc:
        flash(f"Cleanup failed: {exc}", "error")
    return redirect(url_for("admin.dashboard"))
