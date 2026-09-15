"""
Chapter-wise question bank admin (`questions` table).

Bulk upload follows the 2-level / 4-combination structure:
    Level 1: MCQ vs PYQ           -> is_pyq (PYQ requires pyq_year)
    Level 2: Topic-wise vs Random -> topic_name (Random -> "General")
"""
import json

from flask import render_template, request, redirect, url_for, flash, jsonify

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.extensions import supabase_admin

BUCKET_NAME = "question-images"

REQUIRED_BULK_FIELDS = (
    "question_text", "option_a", "option_b", "option_c", "option_d",
    "correct_option", "difficulty_id",
)

def _delete_image_from_storage(image_url):
    """
    Helper function for Pillar 4: Deep Storage Cleanup.
    Extracts the file path from the public image_url and permanently 
    deletes it from the Supabase Storage bucket.
    """
    if not image_url:
        return
    try:
        # URL format: https://[project_ref].supabase.co/storage/v1/object/public/question-images/questions/[id]/[uuid].jpg
        if "question-images/" in image_url:
            path = image_url.split("question-images/")[1]
            supabase_admin.storage.from_(BUCKET_NAME).remove([path])
    except Exception as e:
        print(f"Failed to delete image from storage {image_url}: {e}")


def _validate_bulk_question(raw, difficulty_ids, chapter_id):
    """
    Validates + normalizes one question dict from the bulk-paste JSON.
    Enforces the MCQ vs PYQ rule, and handles Custom Book/Topic names seamlessly.
    """
    if not isinstance(raw, dict):
        return None, "not a JSON object"

    missing = [f for f in REQUIRED_BULK_FIELDS if not str(raw.get(f, "")).strip()]
    if missing:
        return None, f"missing required field(s): {', '.join(missing)}"

    correct_option = str(raw.get("correct_option", "")).strip().upper()
    if correct_option not in ("A", "B", "C", "D"):
        return None, f"correct_option must be A/B/C/D, got {raw.get('correct_option')!r}"

    try:
        difficulty_id = int(raw.get("difficulty_id"))
    except (TypeError, ValueError):
        return None, f"difficulty_id must be an integer, got {raw.get('difficulty_id')!r}"
    if difficulty_id not in difficulty_ids:
        return None, f"difficulty_id {difficulty_id} does not match any known difficulty level"

    # --- Level 1: Strict MCQ vs PYQ ---
    question_type = str(raw.get("question_type", "mcq")).strip().lower()
    if question_type not in ("mcq", "pyq"):
        return None, f"question_type must be 'mcq' or 'pyq', got {raw.get('question_type')!r}"
    is_pyq = (question_type == "pyq")

    pyq_year = raw.get("pyq_year")
    if is_pyq:
        if pyq_year in (None, ""):
            return None, "question_type is 'pyq' but pyq_year is missing"
        try:
            pyq_year = int(pyq_year)
        except (TypeError, ValueError):
            return None, f"pyq_year must be an integer, got {pyq_year!r}"
    else:
        pyq_year = None  # MCQ never carries a pyq_year

    # --- Level 2: Custom Books & Topic-wise Handling ---
    # topic_name will capture standard topics OR Custom Names like "HC Verma Specials"
    topic_name = str(raw.get("topic_name", "")).strip() or "General"

    image_url = str(raw.get("image_url", "")).strip() if raw.get("image_url") else None
    has_image = bool(raw.get("has_image", bool(image_url)))

    payload = {
        "chapter_id": chapter_id,
        "difficulty_id": difficulty_id,
        "topic_name": topic_name,
        "question_text": str(raw["question_text"]).strip(),
        "option_a": str(raw["option_a"]).strip(),
        "option_b": str(raw["option_b"]).strip(),
        "option_c": str(raw["option_c"]).strip(),
        "option_d": str(raw["option_d"]).strip(),
        "correct_option": correct_option,
        "explanation": (str(raw.get("explanation", "")).strip() if raw.get("explanation") else None),
        "is_pyq": is_pyq,
        "pyq_year": pyq_year,
        "image_url": image_url,
        "has_image": has_image,
        "is_premium": bool(raw.get("is_premium", False)),
    }
    return payload, None


@admin_bp.route("/questions")
@admin_required
def questions_list():
    streams = supabase_admin.table("streams").select("id, name").order("display_order").execute().data

    stream_id = request.args.get("stream_id")
    subject_id = request.args.get("subject_id")
    chapter_id = request.args.get("chapter_id")

    subjects = []
    chapters = []
    questions = []
    chapter = None

    if stream_id:
        subjects = (
            supabase_admin.table("subjects")
            .select("id, name")
            .eq("stream_id", stream_id)
            .eq("is_active", True)
            .order("display_order")
            .execute()
            .data
        )

    if subject_id:
        chapters = (
            supabase_admin.table("chapters")
            .select("id, name")
            .eq("subject_id", subject_id)
            .eq("is_active", True)
            .order("display_order")
            .execute()
            .data
        )

    if chapter_id:
        chapter = supabase_admin.table("chapters").select("id, name").eq("id", chapter_id).single().execute().data
        questions = (
            supabase_admin.table("questions")
            .select("id, question_text, is_pyq, pyq_year, topic_name, is_premium, difficulty_id, has_image, image_url")
            .eq("chapter_id", chapter_id)
            .order("created_at", desc=True)
            .execute()
            .data
        )

    difficulty_levels = supabase_admin.table("difficulty_levels").select("id, name").order("display_order").execute().data

    return render_template(
        "admin_questions.html",
        streams=streams, subjects=subjects, chapters=chapters, questions=questions,
        chapter=chapter, difficulty_levels=difficulty_levels,
        selected_stream_id=stream_id, selected_subject_id=subject_id, selected_chapter_id=chapter_id,
    )


@admin_bp.route("/questions/bulk-upload", methods=["POST"])
@admin_required
def questions_bulk_upload():
    """
    Updated for Pillar 2 (AJAX 25-Chunk Support & Strict Hierarchy).
    Chapter creation is no longer silent. The chapter_id must be provided.
    """
    is_ajax = request.args.get("ajax") == "1"

    stream_id = request.form.get("stream_id") if not request.is_json else request.json.get("stream_id")
    subject_id = request.form.get("subject_id") if not request.is_json else request.json.get("subject_id")
    chapter_id = request.form.get("chapter_id") if not request.is_json else request.json.get("chapter_id")

    if request.is_json:
        raw_json_data = request.json.get("bulk_json")
        raw_json = json.dumps(raw_json_data) if isinstance(raw_json_data, list) else str(raw_json_data or "").strip()
    else:
        raw_json = request.form.get("bulk_json", "").strip()

    redirect_target = lambda: redirect(url_for(  # noqa: E731
        "admin.questions_list", stream_id=stream_id, subject_id=subject_id, chapter_id=chapter_id
    ))

    if not chapter_id:
        if is_ajax: return jsonify({"ok": False, "error": "Chapter must be selected before uploading."}), 400
        flash("Chapter must be selected before uploading.", "error")
        return redirect_target()

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

    if not isinstance(parsed, list) or not parsed:
        if is_ajax: return jsonify({"ok": False, "error": "Expects a non-empty JSON array of question objects."}), 400
        flash("Bulk upload expects a JSON array of question objects, e.g. [{...}, {...}].", "error")
        return redirect_target()

    difficulty_ids = {
        d["id"] for d in
        supabase_admin.table("difficulty_levels").select("id").execute().data
    }

    valid_payloads = []
    errors = []
    for i, raw in enumerate(parsed, start=1):
        payload, error = _validate_bulk_question(raw, difficulty_ids, chapter_id)
        if error:
            errors.append(f"row {i}: {error}")
        else:
            valid_payloads.append(payload)

    inserted_ids = []
    if valid_payloads:
        try:
            result = supabase_admin.table("questions").insert(valid_payloads).execute()
            inserted_ids = [row["id"] for row in result.data]
        except Exception as exc:
            if is_ajax: return jsonify({"ok": False, "error": f"Upload failed at the database level: {exc}"}), 500
            flash(f"Upload failed at the database level: {exc}", "error")
            return redirect_target()

    # --- AJAX RESPONSE FOR CHUNKING ---
    if is_ajax:
        return jsonify({
            "ok": True if inserted_ids else False,
            "inserted": len(inserted_ids),
            "errors": errors
        })

    # --- STANDARD FORM RESPONSE ---
    if valid_payloads and not errors:
        flash(f"Bulk upload complete — {len(valid_payloads)} question(s) inserted.", "success")
    elif valid_payloads and errors:
        flash(
            f"{len(valid_payloads)} question(s) inserted, but "
            f"{len(errors)} row(s) were skipped — {'; '.join(errors[:5])}"
            + (f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""),
            "error",
        )
    else:
        flash(
            f"No questions were inserted — all {len(errors)} row(s) failed validation. "
            f"{'; '.join(errors[:5])}" + (f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""),
            "error",
        )

    return redirect_target()


@admin_bp.route("/questions/<question_id>/edit", methods=["POST"])
@admin_required
def questions_edit(question_id):
    """
    NEW ROUTE: Pillar 2 (Live JSON Edit).
    Updates a chapter-wise question (Custom Books, MCQs, PYQs). Cleanly handles image flags.
    """
    raw = request.json
    if not raw:
        return jsonify({"ok": False, "error": "No JSON payload received"}), 400

    # Fetch chapter_id and old image from existing row to pass into validator
    old_q = supabase_admin.table("questions").select("chapter_id, image_url, has_image").eq("id", question_id).single().execute().data
    if not old_q:
        return jsonify({"ok": False, "error": "Question not found."}), 404

    chapter_id = old_q["chapter_id"]
    difficulty_ids = {d["id"] for d in supabase_admin.table("difficulty_levels").select("id").execute().data}

    payload, error = _validate_bulk_question(raw, difficulty_ids, chapter_id)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    # Pillar 4: If admin toggled has_image to false but an image existed, physically delete it
    if old_q.get("image_url") and not payload.get("has_image"):
        _delete_image_from_storage(old_q["image_url"])
        payload["image_url"] = None

    try:
        supabase_admin.table("questions").update(payload).eq("id", question_id).execute()
        return jsonify({"ok": True, "has_image": payload.get("has_image")})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@admin_bp.route("/questions/<question_id>/delete", methods=["POST"])
@admin_required
def questions_delete(question_id):
    """
    Updated for Pillar 4: Deep Storage Cleanup.
    Permanently deletes the question from the DB AND wipes its image from the bucket.
    """
    stream_id = request.args.get("stream_id")
    subject_id = request.args.get("subject_id")
    chapter_id = request.args.get("chapter_id")
    
    try:
        q = supabase_admin.table("questions").select("image_url").eq("id", question_id).single().execute().data
        if q and q.get("image_url"):
            _delete_image_from_storage(q["image_url"])

        supabase_admin.table("questions").delete().eq("id", question_id).execute()
        flash("Question and its image (if any) permanently deleted.", "success")
    except Exception as exc:
        flash(f"Could not delete question: {exc}", "error")
        
    return redirect(url_for("admin.questions_list", stream_id=stream_id, subject_id=subject_id, chapter_id=chapter_id))
