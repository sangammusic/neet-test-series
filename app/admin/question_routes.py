"""
Chapter-wise question bank admin (`questions` table).

Bulk upload follows the 2-level / 4-combination structure:
    Level 1: MCQ vs PYQ           -> is_pyq (PYQ requires pyq_year)
    Level 2: Topic-wise vs Random -> topic_name (Random -> "General")

Chapters are auto-created (silently) from a typed chapter name using
_slugify() from subject_routes.py, so a bulk paste never needs the
admin to pre-create the chapter by hand first.
"""
import json

from flask import render_template, request, redirect, url_for, flash

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.admin.subject_routes import _slugify
from app.extensions import supabase_admin


REQUIRED_BULK_FIELDS = (
    "question_text", "option_a", "option_b", "option_c", "option_d",
    "correct_option", "difficulty_id",
)


def _get_or_create_chapter(subject_id, chapter_name):
    """
    Looks up a chapter by (subject_id, slug); creates it silently if
    missing. Uses the same _slugify() as the manual "Create Chapter"
    admin form, so a bulk-pasted chapter name resolves to the
    identical slug a human typing that name into the chapter form
    would get — no duplicate chapters from casing/whitespace diffs.
    """
    slug = _slugify(chapter_name)

    existing = (
        supabase_admin.table("chapters")
        .select("id")
        .eq("subject_id", subject_id)
        .eq("slug", slug)
        .execute()
        .data
    )
    if existing:
        return existing[0]["id"]

    created = (
        supabase_admin.table("chapters")
        .insert({"subject_id": subject_id, "name": chapter_name.strip(), "slug": slug})
        .execute()
    )
    return created.data[0]["id"]


def _validate_bulk_question(raw, difficulty_ids, chapter_id):
    """
    Validates + normalizes one question dict from the bulk-paste JSON
    array into a payload ready for insert() into `questions`.

    Enforces the 2-level / 4-combination rule:
      Level 1 - question_type: 'mcq' or 'pyq'. 'pyq' REQUIRES pyq_year.
      Level 2 - topic_name: if present/non-empty -> Topic-wise;
                if absent/blank -> Random, stored as "General".

    Returns (payload_dict, None) on success, or (None, error_string)
    on failure. Never raises — every failure mode is caught and
    turned into a message so one bad row doesn't take down the batch.
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

    # --- Level 1: MCQ vs PYQ ---
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
        pyq_year = None  # MCQ never carries a pyq_year, even if one was pasted

    # --- Level 2: Topic-wise vs Random ---
    topic_name = str(raw.get("topic_name", "")).strip() or "General"

    image_url = str(raw["image_url"]).strip() if raw.get("image_url") else None
    # has_image: explicit flag from the JSON if given; otherwise infer
    # from image_url already being set (e.g. a re-paste of previously
    # exported data). This is what drives the per-question "Upload
    # Image" prompt on the admin questions page for any row where
    # has_image is true but image_url is still empty.
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
        "explanation": (str(raw["explanation"]).strip() if raw.get("explanation") else None),
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
    Parses a pasted JSON array of question objects and inserts all
    valid ones into `questions` in a single batch insert() call.

    Takes subject_id + a free-text chapter_name (not a pre-picked
    chapter_id) — the chapter is looked up-or-created silently via
    _get_or_create_chapter().
    """
    stream_id = request.form.get("stream_id")
    subject_id = request.form.get("subject_id")
    chapter_name = request.form.get("chapter_name", "").strip()
    raw_json = request.form.get("bulk_json", "").strip()

    redirect_target = lambda: redirect(url_for(  # noqa: E731
        "admin.questions_list", stream_id=stream_id, subject_id=subject_id
    ))

    if not subject_id or not chapter_name:
        flash("Select a Stream + Subject and enter a Chapter name before bulk uploading.", "error")
        return redirect_target()

    if not raw_json:
        flash("Paste a JSON array of questions before submitting.", "error")
        return redirect_target()

    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        flash(f"Invalid JSON — could not parse: {exc}", "error")
        return redirect_target()

    if not isinstance(parsed, list):
        flash("Bulk upload expects a JSON array of question objects, e.g. [{...}, {...}].", "error")
        return redirect_target()

    if not parsed:
        flash("The pasted JSON array is empty — nothing to upload.", "error")
        return redirect_target()

    try:
        chapter_id = _get_or_create_chapter(subject_id, chapter_name)
    except Exception as exc:
        flash(f"Could not create/find chapter '{chapter_name}': {exc}", "error")
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

    if valid_payloads:
        try:
            supabase_admin.table("questions").insert(valid_payloads).execute()
        except Exception as exc:
            flash(f"Upload failed at the database level: {exc}", "error")
            return redirect(url_for(
                "admin.questions_list", stream_id=stream_id, subject_id=subject_id, chapter_id=chapter_id
            ))

    if valid_payloads and not errors:
        flash(f"Bulk upload complete — {len(valid_payloads)} question(s) inserted into '{chapter_name}'.", "success")
    elif valid_payloads and errors:
        flash(
            f"{len(valid_payloads)} question(s) inserted into '{chapter_name}', but "
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

    return redirect(url_for(
        "admin.questions_list", stream_id=stream_id, subject_id=subject_id, chapter_id=chapter_id
    ))


@admin_bp.route("/questions/<question_id>/delete", methods=["POST"])
@admin_required
def questions_delete(question_id):
    stream_id = request.args.get("stream_id")
    subject_id = request.args.get("subject_id")
    chapter_id = request.args.get("chapter_id")
    try:
        supabase_admin.table("questions").delete().eq("id", question_id).execute()
        flash("Question deleted.", "success")
    except Exception as exc:
        flash(f"Could not delete question: {exc}", "error")
    return redirect(url_for("admin.questions_list", stream_id=stream_id, subject_id=subject_id, chapter_id=chapter_id))
