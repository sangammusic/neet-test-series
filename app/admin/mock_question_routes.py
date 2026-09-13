"""
Bulk-paste upload for the Mock Test question pool (`mock_questions`).

Each question object in the pasted JSON array must carry an explicit
"subject_name" field (e.g. "Physics", "Chemistry", "Biology"). This
route resolves that name to the correct subject_id for the stream
being uploaded into — this is what keeps mock_questions.subject_id
correctly stream-locked. If subject_name doesn't match any real
subject for that stream, the row is rejected with a validation error
rather than guessed at.
"""
import json

from flask import render_template, request, redirect, url_for, flash

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.extensions import supabase_admin


REQUIRED_BULK_FIELDS = (
    "question_text", "option_a", "option_b", "option_c", "option_d",
    "correct_option", "topic_name", "subject_name",
)


def _validate_mock_question(raw, subject_name_to_id, difficulty_ids, stream_id):
    """
    Validates + normalizes one question dict from the bulk-paste JSON
    array into a payload ready for insert() into mock_questions.

    Returns (payload_dict, error_or_None). Never raises — every
    failure is caught and reported per-row so one bad row in a large
    paste doesn't abort the whole batch.

    subject_name is matched case-insensitively against the subjects
    that exist for the target stream (subject_name_to_id is built
    from that stream's rows only) — a name that's valid in general
    but belongs to a different stream's subject list still fails here,
    which is exactly the stream-locking this table exists to enforce.
    """
    if not isinstance(raw, dict):
        return None, "not a JSON object"

    missing = [f for f in REQUIRED_BULK_FIELDS if not str(raw.get(f, "")).strip()]
    if missing:
        return None, f"missing required field(s): {', '.join(missing)}"

    correct_option = str(raw.get("correct_option", "")).strip().upper()
    if correct_option not in ("A", "B", "C", "D"):
        return None, f"correct_option must be A/B/C/D, got {raw.get('correct_option')!r}"

    difficulty_id = None
    if raw.get("difficulty_id") not in (None, ""):
        try:
            difficulty_id = int(raw.get("difficulty_id"))
        except (TypeError, ValueError):
            return None, f"difficulty_id must be an integer, got {raw.get('difficulty_id')!r}"
        if difficulty_id not in difficulty_ids:
            return None, f"difficulty_id {difficulty_id} does not match any known difficulty level"

    is_pyq = bool(raw.get("is_pyq", False))
    pyq_year = raw.get("pyq_year")
    if is_pyq and pyq_year not in (None, ""):
        try:
            pyq_year = int(pyq_year)
        except (TypeError, ValueError):
            return None, f"pyq_year must be an integer, got {pyq_year!r}"
    else:
        pyq_year = None

    # --- Resolve subject_name -> subject_id for this stream ---
    subject_name_raw = str(raw["subject_name"]).strip()
    subject_id = subject_name_to_id.get(subject_name_raw.lower())
    if not subject_id:
        valid_names = ", ".join(sorted({n.title() for n in subject_name_to_id})) or "none configured"
        return None, f"subject_name {subject_name_raw!r} does not match a subject for this stream (valid: {valid_names})"

    topic_name = str(raw["topic_name"]).strip()

    payload = {
        "stream_id": stream_id,
        "subject_id": subject_id,
        "topic_name": topic_name,
        "question_text": str(raw["question_text"]).strip(),
        "option_a": str(raw["option_a"]).strip(),
        "option_b": str(raw["option_b"]).strip(),
        "option_c": str(raw["option_c"]).strip(),
        "option_d": str(raw["option_d"]).strip(),
        "correct_option": correct_option,
        "explanation": (str(raw["explanation"]).strip() if raw.get("explanation") else None),
        "difficulty_id": difficulty_id,
        "is_pyq": is_pyq,
        "pyq_year": pyq_year,
        "image_url": (str(raw["image_url"]).strip() if raw.get("image_url") else None),
        "is_premium": bool(raw.get("is_premium", False)),
    }
    return payload, None


@admin_bp.route("/mock-questions")
@admin_required
def mock_questions_list():
    streams = supabase_admin.table("streams").select("id, name").order("display_order").execute().data

    stream_id = request.args.get("stream_id")
    subject_id = request.args.get("subject_id")

    subjects = []
    questions = []

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

        query = (
            supabase_admin.table("mock_questions")
            .select("id, question_text, topic_name, subject_id, is_pyq, pyq_year, subjects(name)")
            .eq("stream_id", stream_id)
        )
        if subject_id:
            query = query.eq("subject_id", subject_id)

        questions = query.order("created_at", desc=True).execute().data

    difficulty_levels = supabase_admin.table("difficulty_levels").select("id, name").order("display_order").execute().data

    return render_template(
        "admin_mock_questions.html",
        streams=streams, subjects=subjects, questions=questions, difficulty_levels=difficulty_levels,
        selected_stream_id=stream_id, selected_subject_id=subject_id,
    )


@admin_bp.route("/mock-questions/bulk-upload", methods=["POST"])
@admin_required
def mock_questions_bulk_upload():
    """
    Parses a pasted JSON array of mock-test question objects and
    inserts all valid ones into `mock_questions` in a single batch
    insert() call.

    Each row's subject_name is resolved to that subject's id for the
    selected stream (see _validate_mock_question). Rows whose
    subject_name doesn't match a real subject for this stream are
    rejected outright — there's no needs_review fallback anymore,
    so a typo'd subject name means that row is skipped and reported,
    not silently guessed at.
    """
    stream_id = request.form.get("stream_id")
    raw_json = request.form.get("bulk_json", "").strip()

    redirect_target = lambda: redirect(url_for(  # noqa: E731
        "admin.mock_questions_list", stream_id=stream_id
    ))

    if not stream_id:
        flash("Select a Stream before bulk uploading mock questions.", "error")
        return redirect(url_for("admin.mock_questions_list"))

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

    # Pulled once, outside the loop: subject_name_to_id maps this
    # stream's subject names (lowercased, for case-insensitive
    # matching) to their subject_id — this is what enforces
    # stream-locking, since a name that's only valid for a
    # different stream simply won't be in this dict.
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

    if valid_payloads:
        try:
            supabase_admin.table("mock_questions").insert(valid_payloads).execute()
        except Exception as exc:
            # A DB-level failure (e.g. bad stream_id FK) — don't
            # report success if the batch never actually landed.
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
        # as full success.
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


@admin_bp.route("/mock-questions/<question_id>/delete", methods=["POST"])
@admin_required
def mock_questions_delete(question_id):
    stream_id = request.args.get("stream_id")
    subject_id = request.args.get("subject_id")
    try:
        supabase_admin.table("mock_questions").delete().eq("id", question_id).execute()
        flash("Mock question deleted.", "success")
    except Exception as exc:
        flash(f"Could not delete question: {exc}", "error")
    return redirect(url_for("admin.mock_questions_list", stream_id=stream_id, subject_id=subject_id))
