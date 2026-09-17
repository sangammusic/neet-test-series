import logging
from flask import render_template, redirect, url_for, flash, request, jsonify
from datetime import datetime

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

    try:
        incomplete_count = supabase_admin.table("test_attempts").select("id", count="exact").is_("submitted_at", "null").limit(1).execute().count or 0
    except Exception:
        incomplete_count = 0

    garbage_info = {
        "incomplete_attempts": incomplete_count,
    }

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
                    clean_path = q["image_url"].split("question-images/")[1].split("?")[0]
                    image_paths.append(clean_path)

        for i in range(0, len(image_paths), 50):
            try:
                supabase_admin.storage.from_(BUCKET_NAME).remove(image_paths[i:i+50])
            except Exception as e:
                logger.warning(f"Orphan sweep: bucket batch delete error: {e}")

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


# ==========================================
# MISSING TEST SUBMIT LOGIC FIX
# ==========================================
@admin_bp.route("/tests/submit", methods=["POST"])
def submit_test():
    """
    CRITICAL FIX: Handles the final test submission and calculates the score.
    """
    data = request.json
    if not data or "attempt_id" not in data:
        return jsonify({"ok": False, "error": "Missing attempt ID"}), 400

    attempt_id = data["attempt_id"]

    try:
        # Get answers associated with this attempt
        answers = supabase_admin.table("attempt_answers").select("mock_question_id, selected_option").eq("attempt_id", attempt_id).execute().data
        
        # Get attempt details and test settings
        attempt = supabase_admin.table("test_attempts").select("test_id").eq("id", attempt_id).maybe_single().execute().data
        if not attempt:
            return jsonify({"ok": False, "error": "Attempt not found"}), 404
            
        test = supabase_admin.table("tests").select("marks_per_question, negative_marking").eq("id", attempt["test_id"]).maybe_single().execute().data
        
        marks_per_question = test.get("marks_per_question", 4)
        negative_marking = test.get("negative_marking", 1)

        # Calculate score
        score = 0
        correct_count = 0
        incorrect_count = 0
        
        for ans in answers:
            if ans.get("selected_option"):
                q_data = supabase_admin.table("mock_questions").select("correct_option").eq("id", ans["mock_question_id"]).maybe_single().execute().data
                if q_data:
                    if ans["selected_option"] == q_data.get("correct_option"):
                        score += marks_per_question
                        correct_count += 1
                    else:
                        score -= negative_marking
                        incorrect_count += 1

        # Finalize the attempt
        supabase_admin.table("test_attempts").update({
            "score": score,
            "correct_answers": correct_count,
            "incorrect_answers": incorrect_count,
            "submitted_at": datetime.utcnow().isoformat()
        }).eq("id", attempt_id).execute()

        return jsonify({"ok": True, "redirect": url_for('user.test_result', attempt_id=attempt_id)})

    except Exception as e:
        logger.error(f"Test submission failed: {e}")
        return jsonify({"ok": False, "error": "Server calculation error"}), 500
