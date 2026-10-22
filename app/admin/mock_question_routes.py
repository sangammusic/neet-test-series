# STUDY SANGAM HUB v4.0 — Smart & Forgiving Mock Question Validator (2026-10-22)
"""
Shared validation for Mock Test question bulk-paste uploads.

Key improvements:
  * `topic_name` is NO LONGER mandatory in pasted JSON — if omitted (e.g. when pasting
    questions formatted like Chapter Practice sets), it defaults cleanly to
    `topic` / `chapter_name` / `set_name` / `subject_name` / 'General'.
  * Supports both `option_a..option_d` AND `options: ["...", "...", "...", "..."]` or
    `options: {"A": "...", "B": "...", "C": "...", "D": "..."}`.
  * Supports `correct_option`, `answer`, `correct_answer`, `ans` in formats like
    'A'..'D', 'a'..'d', 'Option A', or 1..4.
  * Forgiving `difficulty_id`: accepts 1/2/3, 'Easy'/'Medium'/'Hard', or omitted, and
    never fails if `difficulty_levels` table is missing an ID.
  * Smart subject resolution: matches exact name, case-insensitive, or prefix
    ('phys' -> Physics, 'chem' -> Chemistry, 'bio'/'bot'/'zoo' -> Biology, 'math' -> Mathematics).
"""
import re


REQUIRED_BULK_FIELDS = (
    "question_text", "option_a", "option_b", "option_c", "option_d", "correct_option",
)

_DIFF_NAME_MAP = {
    "easy": 1,
    "medium": 2,
    "moderate": 2,
    "hard": 3,
    "difficult": 3,
}


def _normalize_options(raw):
    """Extracts option_a..option_d from explicit fields or an `options` list/dict."""
    out = {}
    opts_raw = raw.get("options")
    for idx, letter in enumerate(("a", "b", "c", "d")):
        key = f"option_{letter}"
        val = raw.get(key)
        if val is None or str(val).strip() == "":
            val = raw.get(letter.upper()) or raw.get(letter)
        if (val is None or str(val).strip() == "") and isinstance(opts_raw, (list, tuple)) and len(opts_raw) > idx:
            val = opts_raw[idx]
        if (val is None or str(val).strip() == "") and isinstance(opts_raw, dict):
            val = opts_raw.get(letter.upper()) or opts_raw.get(letter) or opts_raw.get(str(idx + 1))
        out[key] = str(val).strip() if val is not None else ""
    return out


def _normalize_correct_option(raw):
    """Normalizes correct_option / answer / correct_answer into 'A', 'B', 'C', or 'D'."""
    val = (
        raw.get("correct_option")
        if raw.get("correct_option") not in (None, "")
        else (
            raw.get("correct_answer")
            if raw.get("correct_answer") not in (None, "")
            else (raw.get("answer") if raw.get("answer") not in (None, "") else raw.get("ans"))
        )
    )
    if val is None:
        return ""
    s = str(val).strip().upper()
    if s in ("A", "B", "C", "D"):
        return s
    # Handle "OPTION A", "(A)", "A.", etc.
    m = re.match(r"^(?:OPTION\s*)?\(?([ABCD])\)?[.\s]*$", s)
    if m:
        return m.group(1)
    # Handle 1-based numeric options 1, 2, 3, 4
    if s in ("1", "2", "3", "4"):
        return ("A", "B", "C", "D")[int(s) - 1]
    return s


def _resolve_subject_id(subject_name_raw, subject_name_to_id):
    """Matches subject_name against stream subjects with prefix/synonym fallback."""
    if not subject_name_to_id:
        return None
    s_low = str(subject_name_raw or "").strip().lower()
    if not s_low:
        return None
    if s_low in subject_name_to_id:
        return subject_name_to_id[s_low]

    # Synonym & prefix matching
    for db_name, sid in subject_name_to_id.items():
        if s_low.startswith("phys") and "phys" in db_name:
            return sid
        if s_low.startswith("chem") and "chem" in db_name:
            return sid
        if (s_low.startswith("bio") or "botan" in s_low or "zool" in s_low) and (
            "bio" in db_name or "botan" in db_name or "zool" in db_name
        ):
            return sid
        if s_low.startswith("math") and "math" in db_name:
            return sid
        if s_low in db_name or db_name in s_low:
            return sid
    return None


def _validate_mock_question(raw, subject_name_to_id, difficulty_ids, stream_id, default_subject_name=None):
    """
    Validates + normalizes one question dict from the bulk-paste JSON
    array into a payload ready for insert() into mock_questions.

    Returns (payload_dict, error_or_None). Never raises.
    """
    if not isinstance(raw, dict):
        return None, "not a JSON object"

    q_text = str(
        raw.get("question_text")
        or raw.get("question")
        or raw.get("text")
        or raw.get("stem")
        or ""
    ).strip()
    opts = _normalize_options(raw)
    correct_option = _normalize_correct_option(raw)

    normalized_check = {
        "question_text": q_text,
        **opts,
        "correct_option": correct_option,
    }
    missing = [f for f in REQUIRED_BULK_FIELDS if not normalized_check.get(f)]
    if missing:
        return None, f"missing required field(s): {', '.join(missing)}"

    if correct_option not in ("A", "B", "C", "D"):
        return None, f"correct_option must be A/B/C/D, got {raw.get('correct_option')!r}"

    # Forgiving difficulty_id resolution
    difficulty_id = None
    raw_diff = raw.get("difficulty_id") if raw.get("difficulty_id") not in (None, "") else raw.get("difficulty")
    if raw_diff not in (None, ""):
        if isinstance(raw_diff, str) and raw_diff.strip().lower() in _DIFF_NAME_MAP:
            difficulty_id = _DIFF_NAME_MAP[raw_diff.strip().lower()]
        else:
            try:
                difficulty_id = int(raw_diff)
            except (TypeError, ValueError):
                difficulty_id = 1
        if difficulty_ids:
            if difficulty_id not in difficulty_ids:
                difficulty_id = 1 if 1 in difficulty_ids else sorted(difficulty_ids)[0]
        else:
            difficulty_id = None
    elif difficulty_ids:
        difficulty_id = 1 if 1 in difficulty_ids else None

    is_pyq = bool(raw.get("is_pyq", False)) or (str(raw.get("question_type") or "").strip().lower() == "pyq")
    pyq_year = raw.get("pyq_year")
    if pyq_year not in (None, ""):
        try:
            pyq_year = int(pyq_year)
            is_pyq = True
        except (TypeError, ValueError):
            pyq_year = None
    else:
        pyq_year = None

    subject_name_raw = str(raw.get("subject_name") or raw.get("subject") or default_subject_name or "").strip()
    if not subject_name_raw:
        return None, "missing subject_name"

    subject_id = _resolve_subject_id(subject_name_raw, subject_name_to_id)
    if not subject_id:
        valid_names = ", ".join(sorted({n.title() for n in subject_name_to_id})) or "none configured"
        return None, f"subject_name {subject_name_raw!r} does not match a subject for this stream (valid: {valid_names})"

    topic_name = str(
        raw.get("topic_name")
        or raw.get("topic")
        or raw.get("chapter_name")
        or raw.get("chapter")
        or raw.get("set_name")
        or subject_name_raw.title()
        or "General"
    ).strip() or "General"

    image_url_raw = str(raw.get("image_url") or "").strip()
    image_url = image_url_raw if (image_url_raw and re.match(r"^https?://", image_url_raw, re.I)) else None
    has_image = bool(raw.get("has_image", bool(image_url)))

    explanation_raw = str(raw.get("explanation") or raw.get("solution") or "").strip()

    payload = {
        "stream_id": stream_id,
        "subject_id": subject_id,
        "topic_name": topic_name,
        "question_text": q_text,
        "option_a": opts["option_a"],
        "option_b": opts["option_b"],
        "option_c": opts["option_c"],
        "option_d": opts["option_d"],
        "correct_option": correct_option,
        "explanation": explanation_raw if explanation_raw else None,
        "difficulty_id": difficulty_id,
        "is_pyq": is_pyq,
        "pyq_year": pyq_year,
        "image_url": image_url,
        "has_image": has_image,
        "is_premium": bool(raw.get("is_premium", False)),
    }
    return payload, None
