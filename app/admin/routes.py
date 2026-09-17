import logging
from flask import render_template, redirect, url_for, flash

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.extensions import supabase_admin
from app.admin.image_routes import BUCKET_NAME

# Setup basic logger to catch silent errors without crashing the dashboard
logger = logging.getLogger(__name__)


def _count_orphaned_mock_questions():
    """
    A mock_questions row is orphaned once it has no row left pointing to it in
    mock_test_questions — this happens when a test's delete route removed the
    mapping/test but left the question row itself behind.
    """
    try:
        all_ids = {r["id"] for r in supabase_admin.table("mock_questions").select("id").execute().data}
        if not all_ids:
            return 0
        mapped_ids = {
            r["mock_question_id"]
            for r in supabase_admin.table("mock_test_questions").select("mock_question_id").execute().data
            if r.get("mock_question_id")
        }
        return len(all_ids - mapped_ids)
    except Exception as e:
        logger.error(f"Error counting orphaned mock_questions: {e}")
        return 0


@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    """
    Landing page after admin login. Shows quick content counts.
    CRITICAL FIX: Removed nested Storage API calls that caused massive timeouts.
    Now uses O(1) Database queries to count uploaded images instantly.
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

    def _count_uploaded_images(table):
        """Fast DB-level count of questions that actually have an image_url mapped."""
        try:
            res = (
                supabase_admin.table(table)
                .select("id", count="exact")
                .not_.is_("image_url", "null")
                .limit(1)
                .execute()
            )
            return res.count or 0
        except Exception as e:
            logger.error(f"Error counting uploaded images in {table}: {e}")
            return 0

    counts = {
        "streams": _count("streams"),
        "subjects": _count("subjects"),
        "chapters": _count("chapters"),
        "questions": _count("questions"),
        "tests": _count("tests"),
    }

    # FEATURE: Garbage tracking (Abandoned attempts).
    # NOTE: guest-attempt tracking was removed along with guest mode --
    # every test_attempts row now always has a user_id, so a
    # "guest_attempts" count would always read zero going forward.
    try:
        incomplete_count = supabase_admin.table("test_attempts").select("id", count="exact").is_("submitted_at", "null").limit(1).execute().count or 0
    except Exception:
        incomplete_count = 0

    garbage_info = {
        "incomplete_attempts": incomplete_count,
    }

    # O(1) FAST STORAGE CALCULATION: Replaces the N+1 API crash bug!
    # A compressed NEET diagram is roughly ~100KB, so 10 images = ~1MB.
    try:
        q_images = _count_uploaded_images("questions")
        mq_images = _count_uploaded_images("mock_questions")
        uploaded_count = q_images + mq_images
        estimated_mb = round((uploaded_count * 100) / 1024, 1)
    except Exception as e:
        logger.warning(f"Fast storage calc failed: {e}")
        uploaded_count = None
        estimated_mb = None

    storage_info = {
        "uploaded_count": uploaded_count,
        "estimated_mb": estimated_mb,
        "pending_questions": _count_pending_images("questions"),
        "pending_mock_questions": _count_pending_images("mock_questions"),
    }

    orphan_info = {
        "orphaned_mock_questions": _count_orphaned_mock_questions(),
    }

    return render_template(
        "admin_dashboard.html",
        counts=counts,
        storage_info=storage_info,
        garbage_info=garbage_info,
        orphan_info=orphan_info,
    )


@admin_bp.route("/cleanup/legacy-guest-attempts", methods=["POST"])
@admin_required
def cleanup_legacy_guest_attempts():
    """
    One-time historical cleanup only.

    Guest mode has been permanently removed from the app -- no new
    test_attempts row will ever be created without a user_id again.
    This route exists purely so an admin can purge any *old* guest
    attempt rows (user_id IS NULL) that were created back when guest
    mode still existed, freeing their storage via the DB's ON DELETE
    CASCADE (which also removes their attempt_answers rows).

    Safe to run repeatedly -- once there are no more NULL-user_id
    rows left, this is a no-op.
    """
    try:
        res = supabase_admin.table("test_attempts").delete().is_("user_id", "null").execute()
        deleted_count = len(res.data) if res.data else 0
        flash(f"Success: {deleted_count} legacy guest attempts and their data have been permanently wiped.", "success")
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
        deleted_count = len(res.data) if res.data else 0
        flash(f"Success: {deleted_count} abandoned/incomplete test attempts have been permanently wiped.", "success")
    except Exception as exc:
        flash(f"Cleanup failed: {exc}", "error")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/cleanup/orphaned-mock-questions", methods=["POST"])
@admin_required
def cleanup_orphaned_mock_questions():
    """
    Sweeps up mock_questions rows (and their bucket images) that got left
    behind by a previous test/folder delete.
    SANGAM STUDY HUB FIX: Strips trailing '?' from paths to ensure Supabase 
    Storage deletes the orphaned images correctly instead of silently failing.
    """
    try:
        all_ids = {r["id"] for r in supabase_admin.table("mock_questions").select("id").execute().data}
        mapped_ids = {
            r["mock_question_id"]
            for r in supabase_admin.table("mock_test_questions").select("mock_question_id").execute().data
            if r.get("mock_question_id")
        }
        orphan_ids = list(all_ids - mapped_ids)

        if not orphan_ids:
            flash("No orphaned questions found — system is already 100% clean.", "success")
            return redirect(url_for("admin.dashboard"))

        # 1. Delete their bucket images first
        image_paths = []
        for i in range(0, len(orphan_ids), 100):
            chunk = orphan_ids[i:i + 100]
            rows = (
                supabase_admin.table("mock_questions")
                .select("image_url")
                .in_("id", chunk)
                .execute()
                .data
            )
            for q in rows:
                if q.get("image_url") and "question-images/" in q["image_url"]:
                    # SANGAM STUDY HUB FIX: Extract path and strip trailing ?
                    clean_path = q["image_url"].split("question-images/")[1].split("?")[0]
                    image_paths.append(clean_path)

        for i in range(0, len(image_paths), 50):
            try:
                supabase_admin.storage.from_(BUCKET_NAME).remove(image_paths[i:i+50])
            except Exception as e:
                logger.warning(f"Orphan sweep: bucket batch delete error: {e}")

        # 2. Delete the orphaned rows themselves
        for i in range(0, len(orphan_ids), 40):
            chunk = orphan_ids[i:i+40]
            supabase_admin.table("mock_questions").delete().in_("id", chunk).execute()

        flash(
            f"Success: {len(orphan_ids)} orphaned question(s) and {len(image_paths)} orphaned image(s) "
            "permanently wiped from DB and Storage.",
            "success",
        )
    except Exception as exc:
        flash(f"Orphan cleanup failed: {exc}", "error")

    return redirect(url_for("admin.dashboard"))
