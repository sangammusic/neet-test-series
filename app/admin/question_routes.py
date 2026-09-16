"""
Chapter-wise question bank admin (`questions` table).

Upgraded for SANGAM STUDY HUB:
- Folder-in-Folder Architecture (Topic = Folder)
- Inline API creation for Streams, Subjects, Chapters
- Deep Storage Cleanup for Folder Deletion
- Strict Subject/Topic Locking during Bulk Upload
"""
import json
import logging

from flask import render_template, request, redirect, url_for, flash, jsonify

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.extensions import supabase_admin

logger = logging.getLogger(__name__)

BUCKET_NAME = "question-images"

REQUIRED_BULK_FIELDS = (
    "question_text", "option_a", "option_b", "option_c", "option_d",
    "correct_option", "difficulty_id",
)

def _slugify(text: str) -> str:
    """Helper to safely generate URLs from names"""
    import re
    text = str(text).lower().strip()
    return re.sub(r'[^\w\s-]', '', text).replace(' ', '-')

def _delete_image_from_storage(image_url):
    """
    Helper function for Pillar 4: Deep Storage Cleanup.
    Extracts the file path and strips trailing '?' to permanently 
    delete it from the Supabase Storage bucket (Zero-Kachra Policy).
    """
    if not image_url:
        return
    try:
        if "question-images/" in image_url:
            path = image_url.split("question-images/")[1].split("?")[0]
            supabase_admin.storage.from_(BUCKET_NAME).remove([path])
    except Exception as e:
        logger.error(f"Failed to delete image from storage {image_url}: {e}")

def _validate_bulk_question(raw, difficulty_ids, chapter_id, default_marks=4.0, default_negative=1.0):
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
        pyq_year = None

    # Topic Name acts as the Virtual FOLDER
    topic_name = str(raw.get("topic_name", "")).strip() or "General / Uncategorized"

    image_url = str(raw.get("image_url", "")).strip() if raw.get("image_url") else None
    has_image = bool(raw.get("has_image", bool(image_url)))
    
    try:
        marks = float(raw.get("marks", default_marks))
    except (TypeError, ValueError):
        marks = float(default_marks)

    try:
        negative_marks = abs(float(raw.get("negative_marks", default_negative)))
    except (TypeError, ValueError):
        negative_marks = abs(float(default_negative))

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
        "marks": marks,
        "negative_marks": negative_marks,
    }
    return payload, None


# ==========================================
# PAGE ROUTE (Main Dashboard UI)
# ==========================================
@admin_bp.route("/questions")
@admin_required
def questions_list():
    streams = supabase_admin.table("streams").select("id, name").order("display_order").execute().data

    stream_id = request.args.get("stream_id")
    subject_id = request.args.get("subject_id")
    chapter_id = request.args.get("chapter_id")

    subjects, chapters, questions, chapter = [], [], [], None

    if stream_id:
        subjects = supabase_admin.table("subjects").select("id, name").eq("stream_id", stream_id).eq("is_active", True).order("display_order").execute().data

    if subject_id:
        chapters = supabase_admin.table("chapters").select("id, name").eq("subject_id", subject_id).eq("is_active", True).order("display_order").execute().data

    if chapter_id:
        chapter = supabase_admin.table("chapters").select("id, name").eq("id", chapter_id).single().execute().data
        
        try:
            questions = supabase_admin.table("questions").select("id, question_text, is_pyq, pyq_year, topic_name, is_premium, difficulty_id, has_image, image_url, marks, negative_marks").eq("chapter_id", chapter_id).order("created_at", desc=True).execute().data
        except Exception as e:
            # Fallback for old schema
            if "marks" in str(e) or "column" in str(e).lower():
                questions = supabase_admin.table("questions").select("id, question_text, is_pyq, pyq_year, topic_name, is_premium, difficulty_id, has_image, image_url").eq("chapter_id", chapter_id).order("created_at", desc=True).execute().data

    difficulty_levels = supabase_admin.table("difficulty_levels").select("id, name").order("display_order").execute().data

    return render_template(
        "admin_questions.html",
        streams=streams, subjects=subjects, chapters=chapters, questions=questions,
        chapter=chapter, difficulty_levels=difficulty_levels,
        selected_stream_id=stream_id, selected_subject_id=subject_id, selected_chapter_id=chapter_id,
    )


# ==========================================
# BULK UPLOAD ENGINE
# ==========================================
@admin_bp.route("/questions/bulk-upload", methods=["POST"])
@admin_required
def questions_bulk_upload():
    """
    SANGAM STUDY HUB: Secure AJAX 25-Chunk Upload with Duplicate Detection
    """
    is_ajax = request.args.get("ajax") == "1"

    if request.is_json:
        chapter_id = request.json.get("chapter_id")
        default_marks = request.json.get("marks", 4)
        default_negative = request.json.get("negative_marks", request.json.get("negative", 1))
        raw_json_data = request.json.get("bulk_json")
        raw_json = json.dumps(raw_json_data) if isinstance(raw_json_data, list) else str(raw_json_data or "").strip()
    else:
        chapter_id = request.form.get("chapter_id")
        default_marks = request.form.get("marks", 4)
        default_negative = request.form.get("negative_marks", 1)
        raw_json = request.form.get("bulk_json", "").strip()

    if not chapter_id:
        return jsonify({"ok": False, "error": "Chapter must be selected before uploading."}), 400

    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        return jsonify({"ok": False, "error": "Invalid JSON format."}), 400

    if not isinstance(parsed, list) or not parsed:
        return jsonify({"ok": False, "error": "Expects a non-empty JSON array."}), 400

    difficulty_ids = {d["id"] for d in supabase_admin.table("difficulty_levels").select("id").execute().data}

    valid_payloads, errors = [], []
    for i, raw in enumerate(parsed, start=1):
        payload, error = _validate_bulk_question(raw, difficulty_ids, chapter_id, default_marks, default_negative)
        if error:
            errors.append(f"row {i}: {error}")
        else:
            valid_payloads.append(payload)

    # DUPLICATE DETECTION LOGIC
    if valid_payloads:
        existing_questions = supabase_admin.table("questions").select("question_text").eq("chapter_id", chapter_id).execute().data
        existing_texts = {q["question_text"].strip().lower() for q in existing_questions if q.get("question_text")}

        incoming_texts = set()
        for p in valid_payloads:
            clean_text = p["question_text"].strip().lower()
            if clean_text in existing_texts:
                return jsonify({"ok": False, "error": f"Duplicate Detected: '{clean_text[:40]}...' already exists in this chapter!"}), 400
            if clean_text in incoming_texts:
                return jsonify({"ok": False, "error": f"Duplicate Detected inside pasted chunk: '{clean_text[:40]}...'"}), 400
            incoming_texts.add(clean_text)

    inserted_ids, warning_msg = [], None
    
    if valid_payloads and not errors:
        try:
            result = supabase_admin.table("questions").insert(valid_payloads).execute()
            inserted_ids = [row["id"] for row in result.data]
        except Exception as exc:
            if "marks" in str(exc) or "column" in str(exc).lower():
                fallback_payloads = [{k: v for k, v in p.items() if k not in ["marks", "negative_marks"]} for p in valid_payloads]
                try:
                    result = supabase_admin.table("questions").insert(fallback_payloads).execute()
                    inserted_ids = [row["id"] for row in result.data]
                    warning_msg = "Questions saved! BUT 'marks' were ignored. Run SQL Migration."
                except Exception as fallback_exc:
                    return jsonify({"ok": False, "error": f"Upload failed: {fallback_exc}"}), 500
            else:
                return jsonify({"ok": False, "error": f"DB Upload failed: {exc}"}), 500

    resp = {"ok": True if inserted_ids else False, "inserted": len(inserted_ids), "errors": errors}
    if warning_msg: resp["warning"] = warning_msg
    return jsonify(resp)


# ==========================================
# FOLDER MANAGEMENT APIs (SANGAM EXCLUSIVE)
# ==========================================
@admin_bp.route("/questions/folder/rename", methods=["POST"])
@admin_required
def questions_folder_rename():
    """Renames a Virtual Folder by bulk updating 'topic_name' for that chapter."""
    data = request.json
    old_name = str(data.get("old_name", "")).strip()
    new_name = str(data.get("new_name", "")).strip()
    chapter_id = data.get("chapter_id")

    if not all([old_name, new_name, chapter_id]):
        return jsonify({"ok": False, "error": "Missing required data."}), 400

    try:
        supabase_admin.table("questions").update({"topic_name": new_name}).eq("chapter_id", chapter_id).eq("topic_name", old_name).execute()
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@admin_bp.route("/questions/folder/delete", methods=["POST"])
@admin_required
def questions_folder_delete():
    """
    NUCLEAR FOLDER WIPE: Deletes all questions in a Topic Folder and completely 
    erases all associated images from the Storage Bucket.
    """
    data = request.json
    folder_name = str(data.get("folder_name", "")).strip()
    chapter_id = data.get("chapter_id")

    if not folder_name or not chapter_id:
        return jsonify({"ok": False, "error": "Missing folder or chapter data."}), 400

    try:
        # 1. Fetch images to destroy from bucket
        questions = supabase_admin.table("questions").select("id, image_url").eq("chapter_id", chapter_id).eq("topic_name", folder_name).execute().data
        question_ids = [q["id"] for q in questions]
        
        image_paths = []
        for q in questions:
            if q.get("image_url") and "question-images/" in q["image_url"]:
                clean_path = q["image_url"].split("question-images/")[1].split("?")[0]
                image_paths.append(clean_path)

        # 2. Batch wipe images from Supabase Storage (50 at a time)
        if image_paths:
            for i in range(0, len(image_paths), 50):
                try:
                    supabase_admin.storage.from_(BUCKET_NAME).remove(image_paths[i:i+50])
                except Exception as e:
                    logger.warning(f"Bucket delete error during folder wipe: {e}")

        # 3. Batch delete rows from DB (40 at a time to prevent URI Too Long)
        if question_ids:
            for i in range(0, len(question_ids), 40):
                chunk = question_ids[i:i+40]
                supabase_admin.table("questions").delete().in_("id", chunk).execute()

        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ==========================================
# INLINE CREATION APIs (Quick Add)
# ==========================================
@admin_bp.route("/api/streams/create", methods=["POST"])
@admin_required
def api_create_stream():
    name = request.json.get("name", "").strip()
    if not name: return jsonify({"ok": False, "error": "Name is required"}), 400
    try:
        supabase_admin.table("streams").insert({"name": name, "slug": _slugify(name)}).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@admin_bp.route("/api/subjects/create", methods=["POST"])
@admin_required
def api_create_subject():
    name = request.json.get("name", "").strip()
    stream_id = request.json.get("stream_id")
    if not name or not stream_id: return jsonify({"ok": False, "error": "Name and Stream required"}), 400
    try:
        supabase_admin.table("subjects").insert({"name": name, "slug": _slugify(name), "stream_id": stream_id}).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@admin_bp.route("/api/chapters/create", methods=["POST"])
@admin_required
def api_create_chapter():
    name = request.json.get("name", "").strip()
    subject_id = request.json.get("subject_id")
    if not name or not subject_id: return jsonify({"ok": False, "error": "Name and Subject required"}), 400
    try:
        supabase_admin.table("chapters").insert({"name": name, "slug": _slugify(name), "subject_id": subject_id}).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ==========================================
# SINGLE QUESTION MANAGEMENT
# ==========================================
@admin_bp.route("/questions/<question_id>/edit", methods=["POST"])
@admin_required
def questions_edit(question_id):
    raw = request.json
    old_q = supabase_admin.table("questions").select("chapter_id, image_url, has_image").eq("id", question_id).single().execute().data
    difficulty_ids = {d["id"] for d in supabase_admin.table("difficulty_levels").select("id").execute().data}

    payload, error = _validate_bulk_question(raw, difficulty_ids, old_q["chapter_id"])
    if error: return jsonify({"ok": False, "error": error}), 400

    if old_q.get("image_url") and not payload.get("has_image"):
        _delete_image_from_storage(old_q["image_url"])
        payload["image_url"] = None

    try:
        supabase_admin.table("questions").update(payload).eq("id", question_id).execute()
        return jsonify({"ok": True, "has_image": payload.get("has_image")})
    except Exception as exc:
        if "marks" in str(exc):
            payload.pop("marks", None)
            payload.pop("negative_marks", None)
            supabase_admin.table("questions").update(payload).eq("id", question_id).execute()
            return jsonify({"ok": True, "has_image": payload.get("has_image")})
        return jsonify({"ok": False, "error": str(exc)}), 500


@admin_bp.route("/questions/<question_id>/delete", methods=["POST"])
@admin_required
def questions_delete(question_id):
    stream_id, subject_id, chapter_id = request.args.get("stream_id"), request.args.get("subject_id"), request.args.get("chapter_id")
    try:
        q = supabase_admin.table("questions").select("image_url").eq("id", question_id).single().execute().data
        if q and q.get("image_url"):
            _delete_image_from_storage(q["image_url"])

        supabase_admin.table("questions").delete().eq("id", question_id).execute()
        flash("Question permanently deleted.", "success")
    except Exception as exc:
        flash(f"Delete failed: {exc}", "error")
    return redirect(url_for("admin.questions_list", stream_id=stream_id, subject_id=subject_id, chapter_id=chapter_id))
