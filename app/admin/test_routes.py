"""
Mock Test admin: create a test, then map mock_questions to it.

Mapping is direct-to-test: the admin pastes a JSON array (same shape
as the Mock Question bulk upload) straight into the test-manage page.
Each row is validated + inserted into mock_questions (via the same
_validate_mock_question used by mock_question_routes.py, so subject_name
resolution and stream-locking behave identically in both places),
then immediately linked to this test_id in mock_test_questions.

There is no Stream/Subject/Chapter picker here — a mock test spans
the whole syllabus by design, so the only unit of work is "this test"
and "this pasted batch of mixed-subject questions".
"""
import json

from flask import render_template, request, redirect, url_for, flash, jsonify

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.admin.mock_question_routes import _validate_mock_question
from app.extensions import supabase_admin

# BUCKET_NAME is required for the Deep Storage Cleanup logic
BUCKET_NAME = "question-images"


def _delete_image_from_storage(image_url):
    """
    Helper function for Pillar 4: Deep Storage Cleanup.
    Extracts the file path from the public image_url and permanently 
    deletes it from the Supabase Storage bucket.
    """
    if not image_url:
        return
    try:
        # URL format: https://[project_ref].supabase.co/storage/v1/object/public/question-images/mock_questions/[id]/[uuid].jpg
        # We need to extract everything after 'question-images/'
        if "question-images/" in image_url:
            path = image_url.split("question-images/")[1]
            supabase_admin.storage.from_(BUCKET_NAME).remove([path])
    except Exception as e:
        print(f"Failed to delete image from storage {image_url}: {e}")


@admin_bp.route("/tests")
@admin_required
def tests_list():
    streams = supabase_admin.table("streams").select("id, name").order("display_order").execute().data
    categories = supabase_admin.table("test_categories").select("id, name, stream_id").order("display_order").execute().data
    tests = (
        supabase_admin.table("tests")
        .select("id, title, duration_minutes, total_marks, is_premium, is_active, streams(name)")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    return render_template("admin_tests.html", streams=streams, categories=categories, tests=tests)


@admin_bp.route("/tests/create", methods=["POST"])
@admin_required
def tests_create():
    negative_marking_raw = float(request.form.get("negative_marking") or 0)
    if negative_marking_raw < 0:
        flash("Negative Marking can't be a negative number — it was saved as its positive value instead.", "error")
    negative_marking = abs(negative_marking_raw)

    payload = {
        "stream_id": request.form.get("stream_id"),
        "category_id": request.form.get("category_id"),
        "title": request.form.get("title", "").strip(),
        "description": request.form.get("description", "").strip() or None,
        "duration_minutes": int(request.form.get("duration_minutes") or 60),
        "total_marks": int(request.form.get("total_marks")) if request.form.get("total_marks") else None,
        "negative_marking": negative_marking,
        "is_premium": request.form.get("is_premium") == "on",
        "price_inr": float(request.form.get("price_inr") or 0),
    }
    try:
        result = supabase_admin.table("tests").insert(payload).execute()
        test_id = result.data[0]["id"]
        flash("Test created — now paste questions below to map them.", "success")
        return redirect(url_for("admin.tests_manage", test_id=test_id))
    except Exception as exc:
        flash(f"Could not create test: {exc}", "error")
        return redirect(url_for("admin.tests_list"))


@admin_bp.route("/tests/<test_id>/manage")
@admin_required
def tests_manage(test_id):
    test = supabase_admin.table("tests").select("*").eq("id", test_id).single().execute().data

    mapped = (
        supabase_admin.table("mock_test_questions")
        .select("mock_question_id, mock_questions(question_text, topic_name, is_pyq, pyq_year, has_image, image_url, subjects(name))")
        .eq("test_id", test_id)
        .execute()
        .data
    )

    return render_template("admin_test_manage.html", test=test, mapped=mapped)


@admin_bp.route("/tests/<test_id>/questions/bulk-map", methods=["POST"])
@admin_required
def tests_bulk_map_questions(test_id):
    """
    Updated for Pillar 1 (25-Chunk Upload): Now supports AJAX submissions.
    If ?ajax=1 is passed, it returns JSON instead of redirecting/flashing, 
    allowing the frontend to clear the box and prep for the next batch seamlessly.
    """
    is_ajax = request.args.get("ajax") == "1"
    
    test = supabase_admin.table("tests").select("id, stream_id").eq("id", test_id).single().execute().data
    if not test:
        if is_ajax: return jsonify({"ok": False, "error": "Test not found."}), 404
        flash("Test not found.", "error")
        return redirect(url_for("admin.tests_list"))

    stream_id = test["stream_id"]
    
    # Support both AJAX JSON payloads and standard form payloads
    if request.is_json:
        raw_json_data = request.json.get("bulk_json")
        raw_json = json.dumps(raw_json_data) if isinstance(raw_json_data, list) else str(raw_json_data or "").strip()
    else:
        raw_json = request.form.get("bulk_json", "").strip()

    redirect_target = lambda: redirect(url_for("admin.tests_manage", test_id=test_id))  # noqa: E731

    if not raw_json:
        if is_ajax: return jsonify({"ok": False, "error": "Paste a JSON array of questions before submitting."}), 400
        flash("Paste a JSON array of questions before submitting.", "error")
        return redirect_target()

    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        if is_ajax: return jsonify({"ok": False, "error": f"Invalid JSON — could not parse: {exc}"}), 400
        flash(f"Invalid JSON — could not parse: {exc}", "error")
        return redirect_target()

    if not isinstance(parsed, list):
        if is_ajax: return jsonify({"ok": False, "error": "Expects a JSON array of question objects."}), 400
        flash("Expects a JSON array of question objects, e.g. [{...}, {...}].", "error")
        return redirect_target()

    if not parsed:
        if is_ajax: return jsonify({"ok": False, "error": "The pasted JSON array is empty — nothing to upload."}), 400
        flash("The pasted JSON array is empty — nothing to upload.", "error")
        return redirect_target()

    subjects_for_stream = (
        supabase_admin.table("subjects")
        .select("id, name")
        .eq("stream_id", stream_id)
        .execute()
        .data
    )
    subject_name_to_id = {s["name"].strip().lower(): s["id"] for s in subjects_for_stream}

    difficulty_ids = {
        d["id"] for d in
        supabase_admin.table("difficulty_levels").select("id").execute().data
    }

    valid_payloads = []
    errors = []
    for i, raw in enumerate(parsed, start=1):
        payload, error = _validate_mock_question(raw, subject_name_to_id, difficulty_ids, stream_id)
        if error:
            errors.append(f"row {i}: {error}")
        else:
            valid_payloads.append(payload)

    inserted_ids = []
    if valid_payloads:
        try:
            result = supabase_admin.table("mock_questions").insert(valid_payloads).execute()
            inserted_ids = [row["id"] for row in result.data]
        except Exception as exc:
            if is_ajax: return jsonify({"ok": False, "error": f"Upload failed at DB level: {exc}"}), 500
            flash(f"Upload failed at the database level: {exc}", "error")
            return redirect_target()

    if inserted_ids:
        try:
            # We must determine the current max question_order for this test so chunked uploads append correctly
            existing_orders = supabase_admin.table("mock_test_questions").select("question_order").eq("test_id", test_id).execute().data
            start_order = max([row["question_order"] for row in existing_orders], default=-1) + 1 if existing_orders else 0
            
            mapping_rows = [
                {"test_id": test_id, "mock_question_id": qid, "question_order": start_order + i}
                for i, qid in enumerate(inserted_ids)
            ]
            supabase_admin.table("mock_test_questions").upsert(
                mapping_rows, on_conflict="test_id,mock_question_id"
            ).execute()
        except Exception as exc:
            if is_ajax: return jsonify({"ok": False, "error": f"Mapped failed: {exc}"}), 500
            flash(f"{len(inserted_ids)} question(s) inserted, but linking failed: {exc}", "error")
            return redirect_target()

    # AJAX Response for 25-Chunk logic
    if is_ajax:
        return jsonify({
            "ok": True if inserted_ids else False,
            "inserted": len(inserted_ids),
            "errors": errors
        })

    # Standard Fallback Response
    if inserted_ids and not errors:
        flash(f"{len(inserted_ids)} question(s) inserted and mapped to this test.", "success")
    elif inserted_ids and errors:
        flash(
            f"{len(inserted_ids)} question(s) inserted and mapped, but "
            f"{len(errors)} row(s) were skipped — {'; '.join(errors[:5])}"
            + (f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""),
            "error",
        )
    else:
        flash(
            f"No questions were mapped — all {len(errors)} row(s) failed validation. "
            f"{'; '.join(errors[:5])}" + (f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""),
            "error",
        )

    return redirect_target()


@admin_bp.route("/tests/<test_id>/questions/<question_id>/edit", methods=["POST"])
@admin_required
def tests_edit_question(test_id, question_id):
    """
    NEW ROUTE: Pillar 1 (Live JSON Edit). 
    Updates the question via JSON modal. If has_image becomes True, the UI will 
    automatically display the Upload Image box.
    """
    raw = request.json
    if not raw:
        return jsonify({"ok": False, "error": "No JSON payload received"}), 400

    test = supabase_admin.table("tests").select("stream_id").eq("id", test_id).single().execute().data
    if not test:
        return jsonify({"ok": False, "error": "Test not found."}), 404

    stream_id = test["stream_id"]
    subjects = supabase_admin.table("subjects").select("id, name").eq("stream_id", stream_id).execute().data
    subject_name_to_id = {s["name"].strip().lower(): s["id"] for s in subjects}
    difficulty_ids = {d["id"] for d in supabase_admin.table("difficulty_levels").select("id").execute().data}

    # Validate using the existing robust function
    payload, error = _validate_mock_question(raw, subject_name_to_id, difficulty_ids, stream_id)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    # Handle image toggling logic (if admin unchecks has_image, delete existing image to save storage)
    old_q = supabase_admin.table("mock_questions").select("image_url, has_image").eq("id", question_id).single().execute().data
    if old_q and old_q.get("image_url") and not payload.get("has_image"):
        _delete_image_from_storage(old_q["image_url"])
        payload["image_url"] = None

    try:
        supabase_admin.table("mock_questions").update(payload).eq("id", question_id).execute()
        return jsonify({"ok": True, "has_image": payload.get("has_image")})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@admin_bp.route("/tests/<test_id>/questions/<question_id>/remove", methods=["POST"])
@admin_required
def tests_remove_question(test_id, question_id):
    """
    Updated for Pillar 4: Deep Storage Cleanup.
    Since questions belong exclusively to the test, removing the question 
    deletes it entirely from DB AND clears its image from the bucket.
    """
    try:
        # Check if an image exists and delete it permanently
        q = supabase_admin.table("mock_questions").select("image_url").eq("id", question_id).single().execute().data
        if q and q.get("image_url"):
            _delete_image_from_storage(q["image_url"])

        # Delete the question completely. (mock_test_questions mapping row will automatically 
        # be cascade-deleted by Supabase, keeping DB clean).
        supabase_admin.table("mock_questions").delete().eq("id", question_id).execute()
        
        flash("Question and its image (if any) permanently deleted.", "success")
    except Exception as exc:
        flash(f"Failed to remove question: {exc}", "error")
        
    return redirect(url_for("admin.tests_manage", test_id=test_id))


@admin_bp.route("/tests/<test_id>/delete", methods=["POST"])
@admin_required
def tests_delete(test_id):
    """
    Updated for Pillar 4: Deep Storage Cleanup.
    Deleting a test now fetches ALL its mapped questions, deletes every single 
    associated image from the 1GB Storage bucket, and then deletes the test 
    and all questions from the database. Zero Kachra Policy.
    """
    try:
        # 1. Fetch all mapped questions and their image URLs
        mapped = (
            supabase_admin.table("mock_test_questions")
            .select("mock_question_id, mock_questions(image_url)")
            .eq("test_id", test_id)
            .execute()
            .data
        )

        question_ids = []
        for row in mapped:
            qid = row.get("mock_question_id")
            if qid:
                question_ids.append(qid)
                
            q = row.get("mock_questions")
            if q and q.get("image_url"):
                # 2. Delete the physical image file from Supabase Storage
                _delete_image_from_storage(q["image_url"])

        # 3. Delete the Test itself (this cascades and wipes test_attempts, answers, and mock_test_questions)
        supabase_admin.table("tests").delete().eq("id", test_id).execute()

        # 4. Safely delete the orphaned mock_questions themselves
        if question_ids:
            supabase_admin.table("mock_questions").delete().in_("id", question_ids).execute()

        flash("Test deleted successfully. All associated questions and images have been permanently wiped from storage.", "success")
    except Exception as exc:
        flash(f"Could not completely delete test: {exc}", "error")
        
    return redirect(url_for("admin.tests_list"))
