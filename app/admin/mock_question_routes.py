"""
Shared validation for Mock Test question bulk-paste uploads.

Each question object in the pasted JSON array must carry an explicit
"subject_name" field (e.g. "Physics", "Chemistry", "Biology"). This
module resolves that name to the correct subject_id for the stream
being uploaded into. If subject_name doesn't match any real subject
for that stream, the row is rejected with a validation error — there
is no keyword guessing and no needs_review fallback.

NOTE: the standalone "/admin/mock-questions" bulk-paste page (its own
routes + admin_mock_questions.html) that used to live in this file has
been removed — it was a legacy, unreachable-from-the-nav duplicate of
the live Folder -> Test -> Upload flow in test_routes.py, and having
two independent copies of this validation logic around is exactly the
kind of drift risk that's worse than deleting the dead one. The
validator below is still the live, shared code: test_routes.py's
tests_bulk_map_questions() imports and calls it directly.
"""


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
    from that stream's rows only) — a name valid for a different
    stream still fails here, which enforces stream-locking.
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

    # --- Resolve subject_name -> subject_id for this stream (no guessing) ---
    subject_name_raw = str(raw["subject_name"]).strip()
    subject_id = subject_name_to_id.get(subject_name_raw.lower())
    if not subject_id:
        valid_names = ", ".join(sorted({n.title() for n in subject_name_to_id})) or "none configured"
        return None, f"subject_name {subject_name_raw!r} does not match a subject for this stream (valid: {valid_names})"

    topic_name = str(raw["topic_name"]).strip()

    image_url = str(raw["image_url"]).strip() if raw.get("image_url") else None
    has_image = bool(raw.get("has_image", bool(image_url)))

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
        "image_url": image_url,
        "has_image": has_image,
        "is_premium": bool(raw.get("is_premium", False)),
    }
    return payload, None

