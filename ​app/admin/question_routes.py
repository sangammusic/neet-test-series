import json

from flask import render_template, request, redirect, url_for, flash

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.extensions import supabase_admin


# Fields every question in a bulk-upload JSON array must include.
# is_pyq, pyq_year, explanation, image_url are optional — see
# _validate_bulk_question() below for their defaults.
REQUIRED_BULK_FIELDS = (
    "question_text", "option_a", "option_b", "option_c", "option_d",
    "correct_option", "topic_name", "difficulty_id",
)


def _validate_bulk_question(raw, difficulty_ids, chapter_id):
    """
    Validates + normalizes one question dict from the bulk-paste
    JSON array into a payload ready for insert().

    Returns (payload_dict, None) on success, or (None, error_string)
    on failure. Never raises — every failure mode is caught and
    turned into a message, since this runs inside a loop over
    up to ~100 untrusted, AI-generated rows and one uncaught
    exception shouldn't take down the whole batch.
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

    is_pyq = bool(raw.get("is_pyq", False))
    pyq_year = raw.get("pyq_year")
    if is_pyq and pyq_year is not None:
        try:
            pyq_year = int(pyq_year)
        except (TypeError, ValueError):
            return None, f"pyq_year must be an integer, got {pyq_year!r}"
    elif not is_pyq:
        pyq_year = None

    is_premium = bool(raw.get("is_premium", False))

    payload = {
        "chapter_id": chapter_id,
        "difficulty_id": difficulty_id,
        "topic_name": str(raw["topic_name"]).strip(),
        "question_text": str(raw["question_text"]).strip(),
        "option_a": str(raw["option_a"]).strip(),
        "option_b": str(raw["option_b"]).strip(),
        "option_c": str(raw["option_c"]).strip(),
        "option_d": str(raw["option_d"]).strip(),
        "correct_option": correct_option,
        "explanation": (str(raw["explanation"]).strip() if raw.get("explanation") else None),
        "is_pyq": is_pyq,
        "pyq_year": pyq_year,
        "image_url": (str(raw["image_url"]).strip() if raw.get("image_url") else None),
        "is_premium": is_premium,
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
            .select("id, question_text, is_pyq, pyq_year, topic_name, is_premium, difficulty_id")
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


@admin_bp.route("/questions/create", methods=["POST"])
@admin_required
def questions_create():
    chapter_id = request.form.get("chapter_id")
    question_type = request.form.get("question_type")  # 'mcq' or 'pyq'
    is_pyq = (question_type == "pyq")
    pyq_year = request.form.get("pyq_year") or None

    payload = {
        "chapter_id": chapter_id,
        "difficulty_id": int(request.form.get("difficulty_id")),
        "topic_name": request.form.get("topic_name", "").strip() or "General",
        "question_text": request.form.get("question_text", "").strip(),
        "option_a": request.form.get("option_a", "").strip(),
        "option_b": request.form.get("option_b", "").strip(),
        "option_c": request.form.get("option_c", "").strip(),
        "option_d": request.form.get("option_d", "").strip(),
        "correct_option": request.form.get("correct_option"),
        "explanation": request.form.get("explanation", "").strip() or None,
        "is_pyq": is_pyq,
        "pyq_year": int(pyq_year) if (is_pyq and pyq_year) else None,
        "image_url": request.form.get("image_url", "").strip() or None,
        "is_premium": request.form.get("is_premium") == "on",
    }

    try:
        supabase_admin.table("questions").insert(payload).execute()
        flash("Question added.", "success")
    except Exception as exc:
        flash(f"Could not add question: {exc}", "error")

    # keep the admin on the same chapter view after adding
    subject_id = request.form.get("subject_id")
    stream_id = request.form.get("stream_id")
    return redirect(url_for("admin.questions_list", stream_id=stream_id, subject_id=subject_id, chapter_id=chapter_id))


@admin_bp.route("/questions/bulk-upload", methods=["POST"])
@admin_required
def questions_bulk_upload():
    """
    Parses a pasted JSON array of question objects and inserts all
    valid ones into `questions` in a single batch insert() call.

    Each row is validated independently (see _validate_bulk_question
    above) — one bad row in a 100-question paste doesn't block the
    other 99. Results (inserted count + any skipped rows, with the
    reason for each) are reported back via flash so nothing fails
    silently.
    """
    chapter_id = request.form.get("chapter_id")
    stream_id = request.form.get("stream_id")
    subject_id = request.form.get("subject_id")
    raw_json = request.form.get("bulk_json", "").strip()

    redirect_target = lambda: redirect(url_for(  # noqa: E731
        "admin.questions_list", stream_id=stream_id, subject_id=subject_id, chapter_id=chapter_id
    ))

    if not chapter_id:
        flash("Select a Stream, Subject and Chapter before bulk uploading.", "error")
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

    # Pulled once, outside the loop, so validating 100 rows doesn't
    # mean 100 extra queries to the difficulty_levels table.
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
            # The insert can still fail as a whole (e.g. a DB-level
            # constraint none of our checks above cover) — don't
            # claim success if that happens.
            flash(f"Upload failed at the database level: {exc}", "error")
            return redirect_target()

    if valid_payloads and not errors:
        flash(f"Bulk upload complete — {len(valid_payloads)} question(s) inserted.", "success")
    elif valid_payloads and errors:
        # NOTE: base.html's flash rendering only has two visual states —
        # 'error' (red) and everything else (green) — there's no amber/
        # warning style defined. A partial failure is closer to an error
        # than a clean success, so it uses 'error' here rather than a
        # 'warning' category that would silently render green and read
        # as full success. If you want a real three-way amber state,
        # that's a one-line addition to the ternary in base.html.
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
