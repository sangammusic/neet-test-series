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
# DIAGRAM / IMAGE INTEGRITY DIAGNOSTICS
# ==========================================
@admin_bp.route("/diagnostics/broken-images")
@admin_required
def diagnostics_broken_images():
    """
    SANGAM FIX: Root-cause finder for the "diagram failed to load" bug
    students were hitting while attempting mock tests / practice sets.

    That client-side warning only tells you a specific <img> tag on a
    specific question failed to load right now — it can't tell you
    WHY, or how many other questions are silently affected the same
    way. This route answers that at the data level, for both content
    tables, in one pass:

      1. Pulls every row that has a non-null image_url.
      2. For each one, HEAD-requests the URL (fast, no image body
         downloaded) and records the outcome.
      3. Separately lists every actual object physically present in
         the `question-images` Storage bucket, so a URL that returns
         200 but points at a since-replaced/renamed object still gets
         flagged, not just hard 404s.

    Common causes this surfaces:
      - image_url pointing at a bucket object that Admin's own
        "replace image" / "remove image" flow deleted (see
        _delete_image_from_storage in image_routes.py) but a stale
        image_url string was left on a *different* question that
        happened to share/copy that URL during bulk-paste.
      - has_image=true / image_url set on a question that never
        actually needed a diagram (bulk-paste JSON carried a
        leftover image_url field from a template row that was
        copy-pasted for several questions without clearing it).
      - Bucket made private / renamed after upload, so every
        previously-working public URL now 400s.
      - image_url holding whitespace or other non-URL junk (not
        NULL, not empty string, but not a real http(s) URL either)
        left over from some earlier data-entry step. These are
        reported separately under "junk", not "broken" — no image
        was ever there for these, so there's nothing to fetch or
        flag as a dead link; they just need the stray value cleared.

    This is read-only — it reports, it does not delete anything.
    Use /admin/diagnostics/broken-images/clear to wipe the ones
    confirmed dead (accepts ids from both "broken" and "junk").
    """
    import urllib.request
    import urllib.error

    def _head_check(url, timeout=6):
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "sangam-diagnostics/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            if e.code == 405:
                # Some storage/CDN configs reject HEAD outright but serve
                # GET fine — retry with a byte-range GET so we don't
                # download the whole image just to check it exists.
                try:
                    req2 = urllib.request.Request(
                        url, method="GET",
                        headers={"User-Agent": "sangam-diagnostics/1.0", "Range": "bytes=0-0"},
                    )
                    with urllib.request.urlopen(req2, timeout=timeout) as resp2:
                        return resp2.status
                except urllib.error.HTTPError as e2:
                    return e2.code
                except Exception as e2:
                    return f"error: {e2}"
            return e.code
        except Exception as e:
            return f"error: {e}"

    results = {"questions": [], "mock_questions": [], "junk": {"questions": [], "mock_questions": []}}

    # Which files actually exist in the bucket right now (for the
    # "URL looks fine but object is gone" case, and to avoid hammering
    # every single URL with a network call when we can already tell
    # the object doesn't exist).
    bucket_files = set()
    try:
        stack = [""]
        while stack:
            prefix = stack.pop()
            entries = supabase_admin.storage.from_(BUCKET_NAME).list(prefix) or []
            for e in entries:
                name = e.get("name", "")
                full = f"{prefix}/{name}" if prefix else name
                # Folders come back with id=None in supabase-py; files have metadata
                if e.get("id") is None and e.get("metadata") is None:
                    stack.append(full)
                else:
                    bucket_files.add(full)
    except Exception as e:
        logger.warning(f"broken-images: could not list bucket contents: {e}")
        bucket_files = None  # unknown — skip the bucket cross-check, HTTP check still runs

    import re
    _URL_RE = re.compile(r'^https?://', re.IGNORECASE)

    def _path_from_url(url):
        for marker in (f"/{BUCKET_NAME}/", f"{BUCKET_NAME}/"):
            if marker in url:
                return url.split(marker)[1].split("?")[0]
        return None

    def _check_table(table):
        rows = (
            supabase_admin.table(table)
            .select("id, question_text, image_url")
            .not_.is_("image_url", "null")
            .execute()
            .data
        )
        broken = []
        junk = []
        for row in rows:
            raw_url = row.get("image_url")
            url = (raw_url or "").strip()

            # SANGAM FIX: a non-null, non-empty-string image_url that
            # ISN'T a real http(s) URL (whitespace-only, a stray "-",
            # leftover placeholder text from a bulk-paste template row,
            # etc.) is not "a broken image" — there was never an image
            # to begin with. Treating it as broken and running a network
            # check against it is exactly what produced a false
            # "diagram failed to load" on questions that never had a
            # diagram. These get reported separately as "junk" so they
            # can be cleared without implying any image was ever there.
            if not url or not _URL_RE.match(url):
                junk.append({
                    "id": row["id"],
                    "question_text": (row.get("question_text") or "")[:120],
                    "image_url": raw_url,
                })
                continue

            path = _path_from_url(url)
            in_bucket = (path in bucket_files) if (bucket_files is not None and path) else None

            status = _head_check(url)

            is_broken = (in_bucket is False) or (isinstance(status, int) and status >= 400)
            if is_broken:
                broken.append({
                    "id": row["id"],
                    "question_text": (row.get("question_text") or "")[:120],
                    "image_url": url,
                    "http_status": status,
                    "object_in_bucket": in_bucket,
                })
        return broken, junk

    try:
        results["questions"], results["junk"]["questions"] = _check_table("questions")
    except Exception as e:
        logger.error(f"broken-images: questions check failed: {e}")
    try:
        results["mock_questions"], results["junk"]["mock_questions"] = _check_table("mock_questions")
    except Exception as e:
        logger.error(f"broken-images: mock_questions check failed: {e}")

    total_broken = len(results["questions"]) + len(results["mock_questions"])
    total_junk = len(results["junk"]["questions"]) + len(results["junk"]["mock_questions"])
    return jsonify({
        "ok": True,
        "total_broken": total_broken,
        "total_junk": total_junk,
        "bucket_listing_available": bucket_files is not None,
        **results,
    })


@admin_bp.route("/diagnostics/broken-images/clear", methods=["POST"])
@admin_required
def diagnostics_clear_broken_images():
    """
    Confirmed-dead-only cleanup: expects a JSON body of the exact shape
    diagnostics_broken_images() returns (or a subset of it, e.g. after
    the admin has reviewed and deselected any false positives in the
    UI), and sets image_url to NULL + has_image to False for every id
    listed. Does NOT delete anything else about the question — the
    question itself, its text/options/explanation, stays intact and
    simply goes back to "no diagram", exactly like removing an image
    normally does.
    """
    payload = request.get_json(silent=True) or {}
    cleared = {"questions": 0, "mock_questions": 0}

    for table in ("questions", "mock_questions"):
        ids = [row["id"] for row in payload.get(table, []) if row.get("id")]
        for i in range(0, len(ids), 40):
            chunk = ids[i:i + 40]
            if not chunk:
                continue
            supabase_admin.table(table).update({
                "image_url": None,
                "has_image": False,
            }).in_("id", chunk).execute()
            cleared[table] += len(chunk)

    return jsonify({"ok": True, "cleared": cleared})


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
