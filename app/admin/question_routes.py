"""
Chapter-wise question bank admin (`questions` table).

SANGAM STUDY HUB — Folder -> Quiz (Set) -> Questions architecture:
Chapter select -> MCQ/PYQ -> Topic-wise/Random -> Folder List ->
Quiz List (inside a folder) -> Deep Question Edit (inside a quiz).

Folders and quizzes are NOT separate DB tables. They are distinct
values of the `folder_name` / `set_name` text columns on `questions`
(see sql/migration_folder_set.sql). A folder or quiz only becomes
visible once it has at least one question in it — same behavior as
the old topic_name system, just one level deeper.
"""
import json
import logging
import re

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

DEFAULT_FOLDER = "General / Uncategorized"


def _slugify(text: str) -> str:
    """Helper to safely generate URLs from names"""
    text = str(text).lower().strip()
    return re.sub(r'[^\w\s-]', '', text).replace(' ', '-')


def _delete_image_from_storage(image_url):
    """
    Helper function for Pillar 4: Deep Storage Cleanup.
    SANGAM STUDY HUB FIX: Safely strips trailing query params ('?') and 
    strictly matches the bucket path to prevent silent Storage API failures.
    """
    if not image_url:
        return
    try:
        bucket_prefix = f"/{BUCKET_NAME}/"
        if bucket_prefix in image_url:
            path = image_url.split(bucket_prefix)[1].split("?")[0]
            supabase_admin.storage.from_(BUCKET_NAME).remove([path])
        elif "question-images/" in image_url:
            path = image_url.split("question-images/")[1].split("?")[0]
            supabase_admin.storage.from_(BUCKET_NAME).remove([path])
    except Exception as e:
        logger.error(f"Failed to delete image from storage {image_url}: {e}")


def _bulk_delete_questions_and_images(question_rows):
    """
    Shared helper: given a list of question rows (each with at least
    id + image_url), wipes their storage images in batches of 50 and
    deletes the question rows in batches of 40. Used by folder
    delete, set delete, and chapter delete.
    """
    question_ids = [q["id"] for q in question_rows]

    image_paths = []
    for q in question_rows:
        if q.get("image_url") and f"/{BUCKET_NAME}/" in q["image_url"]:
            clean_path = q["image_url"].split(f"/{BUCKET_NAME}/")[1].split("?")[0]
            image_paths.append(clean_path)
        elif q.get("image_url") and "question-images/" in q["image_url"]:
            clean_path = q["image_url"].split("question-images/")[1].split("?")[0]
            image_paths.append(clean_path)

    if image_paths:
        for i in range(0, len(image_paths), 50):
            try:
                supabase_admin.storage.from_(BUCKET_NAME).remove(image_paths[i:i + 50])
            except Exception as e:
                logger.warning(f"Bucket delete error during bulk wipe: {e}")

    if question_ids:
        for i in range(0, len(question_ids), 40):
            chunk = question_ids[i:i + 40]
            supabase_admin.table("questions").delete().in_("id", chunk).execute()

    return len(question_ids)


def _validate_bulk_question(raw, difficulty_ids, chapter_id, folder_name, set_name, category,
                             default_marks=4.0, default_negative=1.0):
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
    if pyq_year not in (None, ""):
        try:
            pyq_year = int(pyq_year)
        except (TypeError, ValueError):
            return None, f"pyq_year must be an integer, got {pyq_year!r}"
    else:
        pyq_year = None

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
        "category": category, 
        "folder_name": folder_name,
        "set_name": set_name,
        "topic_name": set_name, 
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


def _fetch_set_questions(chapter_id, is_pyq, category, folder_name, set_name):
    """Shared fetch for the question list inside one quiz (set) strictly by category."""
    try:
        return supabase_admin.table("questions").select(
            "id, question_text, is_pyq, pyq_year, category, folder_name, set_name, is_premium, "
            "difficulty_id, has_image, image_url, marks, negative_marks"
        ).eq("chapter_id", chapter_id).eq("is_pyq", is_pyq).eq("category", category).eq("folder_name", folder_name).eq("set_name", set_name).order("created_at", desc=True).execute().data
    except Exception as e:
        if "marks" in str(e) or "column" in str(e).lower():
            return supabase_admin.table("questions").select(
                "id, question_text, is_pyq, pyq_year, category, folder_name, set_name, is_premium, "
                "difficulty_id, has_image, image_url"
            ).eq("chapter_id", chapter_id).eq("is_pyq", is_pyq).eq("category", category).eq("folder_name", folder_name).eq("set_name", set_name).order("created_at", desc=True).execute().data
        raise


# ==========================================
# PAGE ROUTE — Stream/Subject/Chapter/Type/Category/Folder/Quiz drilldown
# ==========================================
@admin_bp.route("/questions")
@admin_required
def questions_list():
    streams = supabase_admin.table("streams").select("id, name").order("display_order").execute().data

    stream_id = request.args.get("stream_id")
    subject_id = request.args.get("subject_id")
    chapter_id = request.args.get("chapter_id")
    q_type = request.args.get("type")          
    category = request.args.get("category")    
    folder_name = request.args.get("folder")   
    set_name = request.args.get("set")         

    subjects, chapters, chapter = [], [], None
    folders, sets = [], []

    if stream_id:
        subjects = supabase_admin.table("subjects").select("id, name").eq("stream_id", stream_id).eq("is_active", True).order("display_order").execute().data

    if subject_id:
        chapters = supabase_admin.table("chapters").select("id, name").eq("subject_id", subject_id).eq("is_active", True).order("display_order").execute().data

    if chapter_id:
        chapter = supabase_admin.table("chapters").select("id, name").eq("id", chapter_id).single().execute().data

    if chapter_id and q_type and category:
        is_pyq = (q_type == "pyq")

        if not folder_name:
            # VIEW 1: Folder List — strictly filtered by category
            rows = supabase_admin.table("questions").select("folder_name").eq("chapter_id", chapter_id).eq("is_pyq", is_pyq).eq("category", category).execute().data
            seen = []
            for r in rows:
                fn = r.get("folder_name") or DEFAULT_FOLDER
                if fn not in seen:
                    seen.append(fn)
            folders = sorted(seen)

        elif not set_name:
            # VIEW 2: Quiz (Set) List — inside folder and category
            rows = supabase_admin.table("questions").select("set_name, marks, negative_marks").eq("chapter_id", chapter_id).eq("is_pyq", is_pyq).eq("category", category).eq("folder_name", folder_name).execute().data
            grouped = {}
            for r in rows:
                sn = r.get("set_name") or DEFAULT_FOLDER
                if sn not in grouped:
                    grouped[sn] = {"name": sn, "count": 0, "marks": r.get("marks"), "negative_marks": r.get("negative_marks")}
                grouped[sn]["count"] += 1
            sets = sorted(grouped.values(), key=lambda x: x["name"])

        else:
            return redirect(url_for(
                "admin.questions_set_manage",
                stream_id=stream_id, subject_id=subject_id, chapter_id=chapter_id,
                type=q_type, category=category, folder=folder_name, set=set_name,
            ))

    difficulty_levels = supabase_admin.table("difficulty_levels").select("id, name").order("display_order").execute().data

    return render_template(
        "admin_questions.html",
        streams=streams, subjects=subjects, chapters=chapters,
        chapter=chapter, difficulty_levels=difficulty_levels,
        folders=folders, sets=sets,
        selected_stream_id=stream_id, selected_subject_id=subject_id, selected_chapter_id=chapter_id,
        selected_type=q_type, selected_category=category,
        selected_folder=folder_name, selected_set=set_name,
    )


# ==========================================
# VIEW 3 PAGE ROUTE — Deep Question Edit
# ==========================================
@admin_bp.route("/questions/set-manage")
@admin_required
def questions_set_manage():
    stream_id = request.args.get("stream_id")
    subject_id = request.args.get("subject_id")
    chapter_id = request.args.get("chapter_id")
    q_type = request.args.get("type")
    category = request.args.get("category")
    folder_name = request.args.get("folder")
    set_name = request.args.get("set")

    if not all([chapter_id, q_type, category, folder_name, set_name]):
        flash("Missing quiz context — please open a quiz from the folder list.", "error")
        return redirect(url_for("admin.questions_list", stream_id=stream_id, subject_id=subject_id, chapter_id=chapter_id))

    chapter = supabase_admin.table("chapters").select("id, name").eq("id", chapter_id).single().execute().data
    is_pyq = (q_type == "pyq")
    questions = _fetch_set_questions(chapter_id, is_pyq, category, folder_name, set_name)
    difficulty_levels = supabase_admin.table("difficulty_levels").select("id, name").order("display_order").execute().data

    return render_template(
        "admin_set_manage.html",
        chapter=chapter, questions=questions, difficulty_levels=difficulty_levels,
        selected_stream_id=stream_id, selected_subject_id=subject_id, selected_chapter_id=chapter_id,
        selected_type=q_type, selected_category=category,
        selected_folder=folder_name, selected_set=set_name,
    )


# ==========================================
# BULK UPLOAD ENGINE
# ==========================================
@admin_bp.route("/questions/bulk-upload", methods=["POST"])
@admin_required
def questions_bulk_upload():
    if request.is_json:
        chapter_id = request.json.get("chapter_id")
        category = request.json.get("category")
        folder_name = str(request.json.get("folder_name", "")).strip()
        set_name = str(request.json.get("set_name", "")).strip()
        default_marks = request.json.get("marks", 4)
        default_negative = request.json.get("negative_marks", request.json.get("negative", 1))
        raw_json_data = request.json.get("bulk_json")
        raw_json = json.dumps(raw_json_data) if isinstance(raw_json_data, list) else str(raw_json_data or "").strip()
    else:
        chapter_id = request.form.get("chapter_id")
        category = request.form.get("category")
        folder_name = str(request.form.get("folder_name", "")).strip()
        set_name = str(request.form.get("set_name", "")).strip()
        default_marks = request.form.get("marks", 4)
        default_negative = request.form.get("negative_marks", 1)
        raw_json = request.form.get("bulk_json", "").strip()

    if not chapter_id:
        return jsonify({"ok": False, "error": "Chapter must be selected before uploading."}), 400

    if not folder_name or not set_name or not category:
        return jsonify({"ok": False, "error": "Folder, Category, and Quiz must be selected before uploading."}), 400

    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return jsonify({"ok": False, "error": "Invalid JSON format."}), 400

    if not isinstance(parsed, list) or not parsed:
        return jsonify({"ok": False, "error": "Expects a non-empty JSON array."}), 400

    difficulty_ids = {d["id"] for d in supabase_admin.table("difficulty_levels").select("id").execute().data}

    valid_payloads, errors = [], []
    for i, raw in enumerate(parsed, start=1):
        payload, error = _validate_bulk_question(raw, difficulty_ids, chapter_id, folder_name, set_name, category, default_marks, default_negative)
        if error:
            errors.append(f"row {i}: {error}")
        else:
            valid_payloads.append(payload)

    if valid_payloads:
        existing_questions = supabase_admin.table("questions").select("question_text").eq("chapter_id", chapter_id).eq("category", category).eq("folder_name", folder_name).eq("set_name", set_name).execute().data
        existing_texts = {q["question_text"].strip().lower() for q in existing_questions if q.get("question_text")}

        incoming_texts = set()
        for p in valid_payloads:
            clean_text = p["question_text"].strip().lower()
            if clean_text in existing_texts:
                return jsonify({"ok": False, "error": f"Duplicate Detected: '{clean_text[:40]}...' already exists in this quiz!"}), 400
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
                    logger.error(f"Fallback DB Upload failed: {fallback_exc}")
                    return jsonify({"ok": False, "error": "Upload failed. Please try again."}), 500
            else:
                logger.error(f"DB Upload failed: {exc}")
                return jsonify({"ok": False, "error": "DB Upload failed."}), 500

    resp = {"ok": True if inserted_ids else False, "inserted": len(inserted_ids), "inserted_ids": inserted_ids, "errors": errors}
    if warning_msg:
        resp["warning"] = warning_msg
    return jsonify(resp)


@admin_bp.route("/questions/set/undo-chunk", methods=["POST"])
@admin_required
def questions_undo_chunk():
    payload = request.json or {}
    question_ids = payload.get("question_ids")
    
    if not question_ids or not isinstance(question_ids, list):
        return jsonify({"ok": False, "error": "No valid question IDs provided for undo."}), 400
    
    try:
        rows = supabase_admin.table("questions").select("id, image_url").in_("id", question_ids).execute().data
        _bulk_delete_questions_and_images(rows)
        return jsonify({"ok": True, "message": "Chunk successfully undone and wiped."})
    except Exception as exc:
        logger.error(f"Undo chunk error: {exc}")
        return jsonify({"ok": False, "error": "Failed to undo chunk."}), 500


# ==========================================
# FOLDER MANAGEMENT APIs
# ==========================================
@admin_bp.route("/questions/folder/create", methods=["POST"])
@admin_required
def questions_folder_create():
    data = request.json or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"ok": False, "error": "Folder name is required."}), 400
    return jsonify({"ok": True, "folder_name": name})


@admin_bp.route("/questions/folder/rename", methods=["POST"])
@admin_required
def questions_folder_rename():
    data = request.json or {}
    old_name = str(data.get("old_name", "")).strip()
    new_name = str(data.get("new_name", "")).strip()
    chapter_id = data.get("chapter_id")
    q_type = data.get("type")
    category = data.get("category")

    if not all([old_name, new_name, chapter_id, q_type, category]):
        return jsonify({"ok": False, "error": "Missing required data."}), 400

    try:
        is_pyq = (q_type == "pyq")
        supabase_admin.table("questions").update({"folder_name": new_name}).eq("chapter_id", chapter_id).eq("is_pyq", is_pyq).eq("category", category).eq("folder_name", old_name).execute()
        return jsonify({"ok": True})
    except Exception as exc:
        logger.error(f"Folder rename error: {exc}")
        return jsonify({"ok": False, "error": "Failed to rename folder."}), 500


@admin_bp.route("/questions/folder/delete", methods=["POST"])
@admin_required
def questions_folder_delete():
    data = request.json or {}
    folder_name = str(data.get("folder_name", "")).strip()
    chapter_id = data.get("chapter_id")
    q_type = data.get("type")
    category = data.get("category")

    if not all([folder_name, chapter_id, q_type, category]):
        return jsonify({"ok": False, "error": "Missing folder or chapter data."}), 400

    try:
        is_pyq = (q_type == "pyq")
        rows = supabase_admin.table("questions").select("id, image_url").eq("chapter_id", chapter_id).eq("is_pyq", is_pyq).eq("category", category).eq("folder_name", folder_name).execute().data
        _bulk_delete_questions_and_images(rows)
        return jsonify({"ok": True})
    except Exception as exc:
        logger.error(f"Folder delete error: {exc}")
        return jsonify({"ok": False, "error": "Failed to delete folder."}), 500


# ==========================================
# QUIZ / SET MANAGEMENT APIs
# ==========================================
@admin_bp.route("/questions/set/create", methods=["POST"])
@admin_required
def questions_set_create():
    data = request.json or {}
    chapter_id = data.get("chapter_id")
    folder_name = str(data.get("folder_name", "")).strip()
    set_name = str(data.get("set_name", "")).strip()
    q_type = data.get("type")
    category = data.get("category")

    if not all([chapter_id, folder_name, set_name, q_type, category]):
        return jsonify({"ok": False, "error": "Missing required data."}), 400

    try:
        is_pyq = (q_type == "pyq")
        existing = supabase_admin.table("questions").select("id").eq("chapter_id", chapter_id).eq("is_pyq", is_pyq).eq("category", category).eq("folder_name", folder_name).eq("set_name", set_name).limit(1).execute().data
        if existing:
            return jsonify({"ok": False, "error": f"A quiz named '{set_name}' already exists in this folder."}), 400
        return jsonify({"ok": True, "set_name": set_name})
    except Exception as exc:
        logger.error(f"Quiz create error: {exc}")
        return jsonify({"ok": False, "error": "Failed to create quiz."}), 500


@admin_bp.route("/questions/set/rename", methods=["POST"])
@admin_required
def questions_set_rename():
    data = request.json or {}
    chapter_id = data.get("chapter_id")
    folder_name = str(data.get("folder_name", "")).strip()
    old_name = str(data.get("old_name", "")).strip()
    new_name = str(data.get("new_name", "")).strip()
    q_type = data.get("type")
    category = data.get("category")

    if not all([chapter_id, folder_name, old_name, new_name, q_type, category]):
        return jsonify({"ok": False, "error": "Missing required data."}), 400

    try:
        is_pyq = (q_type == "pyq")
        existing = supabase_admin.table("questions").select("id").eq("chapter_id", chapter_id).eq("is_pyq", is_pyq).eq("category", category).eq("folder_name", folder_name).eq("set_name", new_name).limit(1).execute().data
        if existing:
            return jsonify({"ok": False, "error": f"A quiz named '{new_name}' already exists in this folder."}), 400
        supabase_admin.table("questions").update({"set_name": new_name, "topic_name": new_name}).eq("chapter_id", chapter_id).eq("is_pyq", is_pyq).eq("category", category).eq("folder_name", folder_name).eq("set_name", old_name).execute()
        return jsonify({"ok": True})
    except Exception as exc:
        logger.error(f"Quiz rename error: {exc}")
        return jsonify({"ok": False, "error": "Failed to rename quiz."}), 500


@admin_bp.route("/questions/set/delete", methods=["POST"])
@admin_required
def questions_set_delete():
    data = request.json or {}
    chapter_id = data.get("chapter_id")
    folder_name = str(data.get("folder_name", "")).strip()
    set_name = str(data.get("set_name", "")).strip()
    q_type = data.get("type")
    category = data.get("category")

    if not all([chapter_id, folder_name, set_name, q_type, category]):
        return jsonify({"ok": False, "error": "Missing required data."}), 400

    try:
        is_pyq = (q_type == "pyq")
        rows = supabase_admin.table("questions").select("id, image_url").eq("chapter_id", chapter_id).eq("is_pyq", is_pyq).eq("category", category).eq("folder_name", folder_name).eq("set_name", set_name).execute().data
        _bulk_delete_questions_and_images(rows)
        return jsonify({"ok": True})
    except Exception as exc:
        logger.error(f"Quiz delete error: {exc}")
        return jsonify({"ok": False, "error": "Failed to delete quiz."}), 500


# ==========================================
# INLINE ITEM MANAGEMENT APIs
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
        logger.error(f"Stream create error: {e}")
        return jsonify({"ok": False, "error": "Failed to create stream."}), 500

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
        logger.error(f"Subject create error: {e}")
        return jsonify({"ok": False, "error": "Failed to create subject."}), 500

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
        logger.error(f"Chapter create error: {e}")
        return jsonify({"ok": False, "error": "Failed to create chapter."}), 500


@admin_bp.route("/api/streams/edit", methods=["POST"])
@admin_required
def api_edit_stream():
    id = request.json.get("id")
    name = request.json.get("name", "").strip()
    if not id or not name: return jsonify({"ok": False, "error": "ID and Name required"}), 400
    try:
        supabase_admin.table("streams").update({"name": name, "slug": _slugify(name)}).eq("id", id).execute()
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Stream edit error: {e}")
        return jsonify({"ok": False, "error": "Failed to edit stream."}), 500

@admin_bp.route("/api/subjects/edit", methods=["POST"])
@admin_required
def api_edit_subject():
    id = request.json.get("id")
    name = request.json.get("name", "").strip()
    if not id or not name: return jsonify({"ok": False, "error": "ID and Name required"}), 400
    try:
        supabase_admin.table("subjects").update({"name": name, "slug": _slugify(name)}).eq("id", id).execute()
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Subject edit error: {e}")
        return jsonify({"ok": False, "error": "Failed to edit subject."}), 500

@admin_bp.route("/api/chapters/edit", methods=["POST"])
@admin_required
def api_edit_chapter():
    id = request.json.get("id")
    name = request.json.get("name", "").strip()
    if not id or not name: return jsonify({"ok": False, "error": "ID and Name required"}), 400
    try:
        supabase_admin.table("chapters").update({"name": name, "slug": _slugify(name)}).eq("id", id).execute()
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Chapter edit error: {e}")
        return jsonify({"ok": False, "error": "Failed to edit chapter."}), 500


@admin_bp.route("/api/streams/delete", methods=["POST"])
@admin_required
def api_delete_stream():
    stream_id = request.json.get("id")
    if not stream_id: return jsonify({"ok": False, "error": "Stream ID required"}), 400
    try:
        subs = supabase_admin.table("subjects").select("id").eq("stream_id", stream_id).execute().data
        if subs:
            return jsonify({"ok": False, "error": f"Blocked: Stream contains {len(subs)} subjects. Please delete them first."}), 400
        supabase_admin.table("streams").delete().eq("id", stream_id).execute()
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Stream delete error: {e}")
        return jsonify({"ok": False, "error": "Failed to delete stream."}), 500

@admin_bp.route("/api/subjects/delete", methods=["POST"])
@admin_required
def api_delete_subject():
    subject_id = request.json.get("id")
    if not subject_id: return jsonify({"ok": False, "error": "Subject ID required"}), 400
    try:
        chaps = supabase_admin.table("chapters").select("id").eq("subject_id", subject_id).execute().data
        if chaps:
            return jsonify({"ok": False, "error": f"Blocked: Subject contains {len(chaps)} chapters. Please delete them first."}), 400
        supabase_admin.table("subjects").delete().eq("id", subject_id).execute()
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Subject delete error: {e}")
        return jsonify({"ok": False, "error": "Failed to delete subject."}), 500

@admin_bp.route("/api/chapters/delete", methods=["POST"])
@admin_required
def api_delete_chapter():
    chapter_id = request.json.get("id")
    if not chapter_id: return jsonify({"ok": False, "error": "Chapter ID required"}), 400
    try:
        qs = supabase_admin.table("questions").select("id, image_url").eq("chapter_id", chapter_id).execute().data
        _bulk_delete_questions_and_images(qs)
        supabase_admin.table("chapters").delete().eq("id", chapter_id).execute()
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Chapter delete error: {e}")
        return jsonify({"ok": False, "error": "Failed to delete chapter."}), 500


# ==========================================
# SINGLE QUESTION MANAGEMENT
# ==========================================
@admin_bp.route("/questions/<question_id>/edit", methods=["POST"])
@admin_required
def questions_edit(question_id):
    raw = request.json
    try:
        old_q = supabase_admin.table("questions").select("chapter_id, folder_name, set_name, category, image_url, has_image").eq("id", question_id).single().execute().data
        difficulty_ids = {d["id"] for d in supabase_admin.table("difficulty_levels").select("id").execute().data}

        payload, error = _validate_bulk_question(raw, difficulty_ids, old_q["chapter_id"], old_q["folder_name"], old_q["set_name"], old_q["category"])
        if error: return jsonify({"ok": False, "error": error}), 400

        if old_q.get("image_url") and not payload.get("has_image"):
            _delete_image_from_storage(old_q["image_url"])
            payload["image_url"] = None

        supabase_admin.table("questions").update(payload).eq("id", question_id).execute()
        return jsonify({"ok": True, "has_image": payload.get("has_image")})
    except Exception as exc:
        if "marks" in str(exc):
            payload.pop("marks", None)
            payload.pop("negative_marks", None)
            try:
                supabase_admin.table("questions").update(payload).eq("id", question_id).execute()
                return jsonify({"ok": True, "has_image": payload.get("has_image")})
            except Exception as inner_exc:
                logger.error(f"Fallback edit failed: {inner_exc}")
                return jsonify({"ok": False, "error": "Failed to update question."}), 500
        logger.error(f"Question edit failed: {exc}")
        return jsonify({"ok": False, "error": "Failed to update question."}), 500


@admin_bp.route("/questions/<question_id>/delete", methods=["POST"])
@admin_required
def questions_delete(question_id):
    stream_id, subject_id, chapter_id = request.args.get("stream_id"), request.args.get("subject_id"), request.args.get("chapter_id")
    q_type, category = request.args.get("type"), request.args.get("category")
    folder_name, set_name = request.args.get("folder"), request.args.get("set")
    try:
        q = supabase_admin.table("questions").select("image_url").eq("id", question_id).single().execute().data
        if q and q.get("image_url"):
            _delete_image_from_storage(q["image_url"])

        supabase_admin.table("questions").delete().eq("id", question_id).execute()
        flash("Question permanently deleted.", "success")
    except Exception as exc:
        logger.error(f"Question delete failed: {exc}")
        flash("Failed to delete question.", "error")

    if folder_name and set_name:
        return redirect(url_for("admin.questions_set_manage", stream_id=stream_id, subject_id=subject_id, chapter_id=chapter_id, type=q_type, category=category, folder=folder_name, set=set_name))
    return redirect(url_for("admin.questions_list", stream_id=stream_id, subject_id=subject_id, chapter_id=chapter_id))
