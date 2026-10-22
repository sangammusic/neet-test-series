# STUDY SANGAM HUB v4.0 — Shared Data Access, NTA Optional Pattern Engine & Zero-Empty-Test Filter
import logging
import re
from app.extensions import supabase_public, supabase_admin, cache

logger = logging.getLogger(__name__)

DEFAULT_PRACTICE_FOLDER = "General / Uncategorized"


def natural_sort_key(text):
    parts = re.split(r"(\d+)", str(text or "").strip())
    return [int(p) if p.isdigit() else p.lower() for p in parts]


# ======================================================================
# NTA NEET OPTIONAL QUESTION ENGINE (50 -> Attempt 45, 100 -> Attempt 90)
# ======================================================================

def compute_nta_subject_cap(question_count: int) -> int:
    """
    Official NTA NEET Pattern rule:
      - Out of 50 questions in a subject (e.g. Physics 50, Chemistry 50, Biology 50),
        a student only attempts at most 45 questions (5 questions are optional free skips
        with zero negative marking).
      - Out of 100 questions in Biology (Botany 50 + Zoology 50), a student only attempts
        at most 90 questions (10 questions are optional free skips).
      - For any 50-question block multiple (count >= 50 and count % 50 == 0),
        attemptable count is (count // 50) * 45.
      - For any other count (e.g. 45, 90, or smaller custom tests), all questions are attemptable.
    """
    c = max(0, int(question_count or 0))
    if c >= 50 and c % 50 == 0:
        return (c // 50) * 45
    return c


def compute_nta_test_meta(subject_counts: dict, stored_total_marks=None, default_mpq: float = 4.0) -> dict:
    """
    Computes complete NTA scoring & attempt metadata for a mock test given
    `subject_counts` = {'Physics': 50, 'Chemistry': 50, 'Biology': 50 (or 100)}.
    """
    clean_counts = {str(k): max(0, int(v or 0)) for k, v in (subject_counts or {}).items() if int(v or 0) > 0}
    total_uploaded = sum(clean_counts.values())
    subject_caps = {subj: compute_nta_subject_cap(cnt) for subj, cnt in clean_counts.items()}
    Subject_skips = {subj: max(0, clean_counts[subj] - subject_caps[subj]) for subj in clean_counts}
    total_attemptable = sum(subject_caps.values())
    optional_skips = max(0, total_uploaded - total_attemptable)

    stored_marks = int(stored_total_marks or 0)
    mpq = float(default_mpq or 4.0)

    if total_attemptable > 0:
        # If optional skips exist and stored_marks was naively set to total_uploaded * 4 (e.g. 600 for 150Q or 800 for 200Q)
        # or stored_marks is 0, normalize to total_attemptable * 4 (e.g. 540 for 150Q->135, 720 for 200Q->180)
        if stored_marks <= 0 or (optional_skips > 0 and stored_marks == total_uploaded * 4):
            effective_marks = int(round(total_attemptable * mpq))
        elif total_uploaded == 150 and total_attemptable == 135 and stored_marks == 720:
            effective_marks = 540
        else:
            effective_marks = stored_marks
        marks_per_question = round(float(effective_marks) / float(total_attemptable), 2)
    else:
        effective_marks = stored_marks if stored_marks > 0 else 720
        marks_per_question = mpq

    subjects_list = [
        {
            "subject": subj,
            "uploaded": clean_counts[subj],
            "attemptable": subject_caps[subj],
            "optional_skips": Subject_skips[subj],
        }
        for subj in clean_counts
    ]
    return {
        "total_uploaded": total_uploaded,
        "total_attemptable": total_attemptable,
        "optional_skips": optional_skips,
        "total_optional_skips": optional_skips,
        "has_optional_pattern": optional_skips > 0,
        "has_optional": optional_skips > 0,
        "effective_total_marks": effective_marks,
        "marks_per_question": marks_per_question,
        "subject_counts": clean_counts,
        "subject_caps": subject_caps,
        "subject_skips": Subject_skips,
        "subjects": subjects_list,
    }


def _normalize_practice_row(row):
    cat = str(row.get("category") or "").strip().lower() or "topic"
    fn = str(row.get("folder_name") or "").strip() or DEFAULT_PRACTICE_FOLDER
    sn = str(row.get("set_name") or row.get("topic_name") or "").strip() or "Practice Set 1"
    return cat, fn, sn


def _fetch_all_chapter_question_rows(chapter_id, is_pyq, columns, page=1000):
    out, start = [], 0
    while True:
        q = (
            supabase_admin.table("questions")
            .select(columns)
            .eq("chapter_id", chapter_id)
            .eq("is_pyq", bool(is_pyq))
            .order("id")
        )
        chunk = q.range(start, start + page - 1).execute().data or []
        out.extend(chunk)
        if len(chunk) < page:
            return out
        start += page


def get_practice_folders_for_chapter(chapter_id, mode, category):
    is_pyq = (mode == "pyq")
    target_cat = str(category or "topic").strip().lower()
    try:
        rows = _fetch_all_chapter_question_rows(
            chapter_id, is_pyq, "folder_name, set_name, topic_name, category"
        )
    except Exception as e:
        logger.error(f"Error fetching practice folders for chapter {chapter_id}: {e}")
        return [], {}

    by_folder = {}
    for r in rows:
        cat, fn, sn = _normalize_practice_row(r)
        if cat != target_cat:
            continue
        info = by_folder.setdefault(fn, {"sets": set(), "question_count": 0})
        info["sets"].add(sn)
        info["question_count"] += 1

    folders = sorted(by_folder.keys(), key=natural_sort_key)
    stats = {
        fn: {"quiz_count": len(info["sets"]), "question_count": info["question_count"]}
        for fn, info in by_folder.items()
    }
    return folders, stats


def get_practice_sets_for_folder(chapter_id, mode, category, folder_name):
    is_pyq = (mode == "pyq")
    target_cat = str(category or "topic").strip().lower()
    target_folder = str(folder_name or "").strip() or DEFAULT_PRACTICE_FOLDER

    cols_with_marks = "folder_name, set_name, topic_name, category, is_premium, marks, negative_marks"
    cols_basic = "folder_name, set_name, topic_name, category, is_premium"
    try:
        try:
            rows = _fetch_all_chapter_question_rows(chapter_id, is_pyq, cols_with_marks)
        except Exception:
            rows = _fetch_all_chapter_question_rows(chapter_id, is_pyq, cols_basic)
    except Exception as e:
        logger.error(f"Error fetching practice sets for chapter {chapter_id}, folder {folder_name}: {e}")
        return []

    set_dict = {}
    for r in rows:
        cat, fn, sn = _normalize_practice_row(r)
        if cat != target_cat or fn != target_folder:
            continue
        if sn not in set_dict:
            set_dict[sn] = {
                "name": sn,
                "count": 0,
                "is_premium": False,
                "marks": r.get("marks") if r.get("marks") is not None else 4,
                "negative_marks": r.get("negative_marks") if r.get("negative_marks") is not None else 1,
            }
        set_dict[sn]["count"] += 1
        if r.get("is_premium"):
            set_dict[sn]["is_premium"] = True

    return sorted(set_dict.values(), key=lambda x: natural_sort_key(x["name"]))


@cache.memoize(timeout=900)
def get_active_streams():
    try:
        res = (
            supabase_public.table("streams")
            .select("id, name, slug, description, icon_url")
            .eq("is_active", True)
            .order("display_order")
            .execute()
        )
        return res.data
    except Exception as e:
        logger.error(f"Error fetching active streams: {e}")
        return []


def get_streams_for_user(user_id: str = None):
    if not user_id:
        return []
    try:
        res = (
            supabase_public.table("user_streams")
            .select("streams(id, name, slug), joined_at")
            .eq("user_id", user_id)
            .order("joined_at", desc=True)
            .execute()
        )
        return [row["streams"] for row in res.data if row.get("streams")]
    except Exception as e:
        logger.error(f"Error fetching streams for user {user_id}: {e}")
        return []


@cache.memoize(timeout=600)
def get_stream_by_slug(slug: str):
    try:
        res = (
            supabase_public.table("streams")
            .select("*")
            .eq("slug", slug)
            .eq("is_active", True)
            .maybe_single()
            .execute()
        )
        return res.data
    except Exception as e:
        logger.error(f"Error fetching stream by slug {slug}: {e}")
        return None


@cache.memoize(timeout=600)
def get_subjects_for_stream(stream_id: str):
    try:
        res = (
            supabase_public.table("subjects")
            .select("id, name, slug")
            .eq("stream_id", stream_id)
            .eq("is_active", True)
            .order("display_order")
            .execute()
        )
        return res.data
    except Exception as e:
        logger.error(f"Error fetching subjects for stream {stream_id}: {e}")
        return []


@cache.memoize(timeout=600)
def get_chapters_for_subject(subject_id: str):
    try:
        res = (
            supabase_public.table("chapters")
            .select("id, name, slug")
            .eq("subject_id", subject_id)
            .eq("is_active", True)
            .order("display_order")
            .execute()
        )
        return res.data
    except Exception as e:
        logger.error(f"Error fetching chapters for subject {subject_id}: {e}")
        return []


@cache.memoize(timeout=900)
def get_test_categories_for_stream(stream_id: str):
    try:
        res = (
            supabase_public.table("test_categories")
            .select("id, name, slug, is_premium")
            .eq("stream_id", stream_id)
            .order("display_order")
            .execute()
        )
        return res.data
    except Exception as e:
        logger.error(f"Error fetching test categories for stream {stream_id}: {e}")
        return []


@cache.memoize(timeout=900)
def get_difficulty_levels():
    try:
        res = (
            supabase_public.table("difficulty_levels")
            .select("id, name")
            .order("display_order")
            .execute()
        )
        return res.data
    except Exception as e:
        logger.error(f"Error fetching difficulty levels: {e}")
        return []


# ---------- High-Speed Mock Test Series & Syllabus Lookups ----------

@cache.memoize(timeout=300)
def get_series_for_stream(stream_id: str, stream_slug: str = ""):
    """
    Fast cached lookup of Mock Test Series folders for a stream.
    """
    try:
        raw_series = (
            supabase_public.table("mock_test_series")
            .select("*")
            .order("created_at", desc=True)
            .execute()
            .data or []
        )
    except Exception as e:
        logger.error(f"Error fetching mock_test_series for stream {stream_id}: {e}")
        return []

    other_slug = "jee" if str(stream_slug).lower() == "neet" else ("neet" if str(stream_slug).lower() == "jee" else "")
    return [
        s for s in raw_series
        if s.get("stream_id") == stream_id
        or (
            s.get("stream_id") is None
            and (not other_slug or other_slug not in (s.get("name") or "").lower())
        )
    ]


@cache.memoize(timeout=300)
def get_series_by_id(series_id: str):
    if not series_id:
        return None
    try:
        return (
            supabase_public.table("mock_test_series")
            .select("*")
            .eq("id", series_id)
            .maybe_single()
            .execute()
            .data
        )
    except Exception as e:
        logger.error(f"Error fetching series {series_id}: {e}")
        return None


def _attach_question_counts_and_filter(tests_list, hide_empty=True):
    """
    Counts mapped questions for each test in `tests_list` from `mock_test_questions`.
    If `hide_empty=True`, filters out any test that has 0 uploaded questions so
    students NEVER see an empty/un-uploaded test inside a Test Series folder!
    """
    if not tests_list:
        return []
    test_ids = [t["id"] for t in tests_list if t.get("id")]
    if not test_ids:
        return []

    counts_by_tid = {tid: 0 for tid in test_ids}
    try:
        for i in range(0, len(test_ids), 50):
            chunk = test_ids[i:i + 50]
            rows = (
                supabase_public.table("mock_test_questions")
                .select("test_id")
                .in_("test_id", chunk)
                .execute()
                .data or []
            )
            for r in rows:
                tid = r.get("test_id")
                if tid in counts_by_tid:
                    counts_by_tid[tid] += 1
    except Exception as e:
        logger.warning(f"Could not count mock_test_questions for tests: {e}")
        return tests_list

    filtered = []
    for t in tests_list:
        row = dict(t)
        q_cnt = counts_by_tid.get(row.get("id"), 0)
        row["question_count"] = q_cnt
        if hide_empty and q_cnt <= 0:
            continue
        # Ensure total_marks reflects NTA attemptable marks if 0 or naive 150*4/200*4
        if q_cnt == 200 and (not row.get("total_marks") or int(row.get("total_marks") or 0) in (0, 800)):
            row["total_marks"] = 720
            row["attemptable_questions"] = 180
        elif q_cnt == 150 and (not row.get("total_marks") or int(row.get("total_marks") or 0) in (0, 600, 720)):
            row["total_marks"] = 540
            row["attemptable_questions"] = 135
        elif q_cnt > 0 and not row.get("total_marks"):
            row["total_marks"] = compute_nta_subject_cap(q_cnt) * 4
            row["attemptable_questions"] = compute_nta_subject_cap(q_cnt)
        else:
            row["attemptable_questions"] = 180 if q_cnt == 200 else (135 if q_cnt == 150 else q_cnt)
        row["total_uploaded"] = q_cnt
        row["total_attemptable"] = row["attemptable_questions"]
        row["has_optional"] = row["total_attemptable"] < q_cnt
        filtered.append(row)
    return filtered


@cache.memoize(timeout=300)
def get_tests_for_series(series_id: str, stream_id: str = None, include_empty: bool = False):
    try:
        q = supabase_public.table("tests").select("*").eq("series_id", series_id)
        if stream_id:
            q = q.eq("stream_id", stream_id)
        raw = q.order("created_at", desc=True).execute().data or []
        return _attach_question_counts_and_filter(raw, hide_empty=not include_empty)
    except Exception as e:
        logger.error(f"Error fetching tests for series {series_id}: {e}")
        return []


def invalidate_mock_series_cache(series_id=None, test_id=None):
    """
    Immediately clears cached series and test lookups after any Admin change.
    """
    try:
        cache.delete_memoized(get_series_for_stream)
        cache.delete_memoized(get_all_tests_for_stream)
        if series_id:
            cache.delete_memoized(get_series_by_id, series_id)
            cache.delete_memoized(get_tests_for_series)
        else:
            cache.delete_memoized(get_series_by_id)
            cache.delete_memoized(get_tests_for_series)
        if test_id:
            cache.delete_memoized(get_test_by_id, test_id)
            cache.delete_memoized(get_test_syllabus, test_id)
        else:
            cache.delete_memoized(get_test_by_id)
            cache.delete_memoized(get_test_syllabus)
    except Exception:
        pass


def get_chapter_by_id(chapter_id):
    try:
        res = (
            supabase_public.table("chapters")
            .select("*, subjects(name, slug, streams(name, slug))")
            .eq("id", chapter_id)
            .maybe_single()
            .execute()
        )
        return res.data
    except Exception as e:
        logger.error(f"Error fetching chapter {chapter_id}: {e}")
        return None


def get_topics_for_chapter(chapter_id, is_pyq=False):
    try:
        res = (
            supabase_public.table("questions")
            .select("topic_name, is_premium")
            .eq("chapter_id", chapter_id)
            .eq("is_pyq", is_pyq)
            .execute()
        )
        topics = {}
        if res.data:
            for row in res.data:
                name = row["topic_name"] or "General"
                entry = topics.setdefault(name, {"name": name, "is_premium": False})
                entry["is_premium"] = entry["is_premium"] or row["is_premium"]
        return list(topics.values())
    except Exception as e:
        logger.error(f"Error fetching topics for chapter {chapter_id}: {e}")
        return []


def get_questions_for_practice(chapter_id, is_pyq=False, topic_name=None):
    try:
        q = supabase_public.table("questions").select("*").eq("chapter_id", chapter_id).eq("is_pyq", is_pyq)
        if topic_name:
            q = q.eq("topic_name", topic_name)
        return q.execute().data
    except Exception as e:
        logger.error(f"Error fetching practice questions for chapter {chapter_id}: {e}")
        return []


@cache.memoize(timeout=300)
def get_all_tests_for_stream(stream_id):
    try:
        res = (
            supabase_public.table("tests")
            .select("id, title, description, duration_minutes, total_marks, is_premium, price_inr")
            .eq("stream_id", stream_id)
            .eq("is_active", True)
            .order("created_at", desc=True)
            .execute()
        )
        return _attach_question_counts_and_filter(res.data or [], hide_empty=True)
    except Exception as e:
        logger.error(f"Error fetching tests for stream {stream_id}: {e}")
        return []


@cache.memoize(timeout=300)
def get_test_by_id(test_id):
    try:
        res = supabase_public.table("tests").select("*").eq("id", test_id).maybe_single().execute()
        return res.data
    except Exception as e:
        logger.error(f"Error fetching test {test_id}: {e}")
        return None


@cache.memoize(timeout=300)
def get_test_syllabus(test_id):
    try:
        res = (
            supabase_public.table("mock_test_questions")
            .select("mock_questions(topic_name, subjects(name))")
            .eq("test_id", test_id)
            .execute()
        )
        syllabus = {}
        if res.data:
            for row in res.data:
                q = row.get("mock_questions")
                if not q:
                    continue
                subj_name = q["subjects"]["name"] if q.get("subjects") else "General"
                topic = q.get("topic_name") or "General"
                syllabus.setdefault(subj_name, set()).add(topic)
        return {subj: sorted(topics) for subj, topics in syllabus.items()}
    except Exception as e:
        logger.error(f"Error fetching syllabus for test {test_id}: {e}")
        return {}


def _visible_image(q):
    url = q.get("image_url")
    if not url or q.get("has_image") is False:
        return None
    return url


@cache.memoize(timeout=300)
def get_mock_questions_for_test(test_id):
    try:
        res = (
            supabase_public.table("mock_test_questions")
            .select(
                "question_order, "
                "mock_questions(id, question_text, option_a, option_b, option_c, "
                "option_d, image_url, has_image, subjects(name))"
            )
            .eq("test_id", test_id)
            .order("question_order")
            .execute()
        )
        grouped = {}
        if res.data:
            for row in res.data:
                q = row.get("mock_questions")
                if not q:
                    continue
                subj_name = q["subjects"]["name"] if q.get("subjects") else "General"
                grouped.setdefault(subj_name, []).append({
                    "id": q["id"],
                    "question_text": q["question_text"],
                    "option_a": q["option_a"],
                    "option_b": q["option_b"],
                    "option_c": q["option_c"],
                    "option_d": q["option_d"],
                    "image_url": _visible_image(q),
                    "subject_name": subj_name,
                    "question_order": row.get("question_order", 0),
                })
        return grouped
    except Exception as e:
        logger.error(f"Error fetching mock questions for test {test_id}: {e}")
        return {}


def get_test_nta_meta(test_id, stored_total_marks=None):
    """
    Returns full NTA question/attempt/skip/marks metadata for `test_id`.
    """
    q_by_subj = get_mock_questions_for_test(test_id) or {}
    subj_counts = {subj: len(qs) for subj, qs in q_by_subj.items()}
    if stored_total_marks is None:
        t = get_test_by_id(test_id) or {}
        stored_total_marks = t.get("total_marks")
    return compute_nta_test_meta(subj_counts, stored_total_marks=stored_total_marks)


@cache.memoize(timeout=300)
def get_mock_question_ids_for_test(test_id):
    try:
        res = (
            supabase_public.table("mock_test_questions")
            .select("mock_question_id")
            .eq("test_id", test_id)
            .execute()
        )
        return [row["mock_question_id"] for row in res.data if row.get("mock_question_id")]
    except Exception as e:
        logger.error(f"Error fetching mock question IDs for test {test_id}: {e}")
        return []


def _discard_unsubmitted_full_attempts(table, filters):
    try:
        q = supabase_admin.table(table).delete().is_("submitted_at", "null").is_("parent_attempt_id", "null")
        for col, val in filters.items():
            q = q.eq(col, val)
        q.execute()
    except Exception as e:
        logger.warning(f"Could not discard abandoned drafts in {table}: {e}")


def create_test_attempt(test_id, user_id=None):
    if not user_id:
        return None
    _discard_unsubmitted_full_attempts("test_attempts", {"test_id": test_id, "user_id": user_id})
    try:
        res = supabase_admin.table("test_attempts").insert(
            {"test_id": test_id, "user_id": user_id, "attempt_kind": "full", "parent_attempt_id": None}
        ).execute()
        return res.data[0]["id"] if res.data else None
    except Exception as e:
        logger.error(f"Error creating test attempt for test {test_id} user {user_id}: {e}")
        return None


def get_attempt_by_id(attempt_id):
    try:
        res = supabase_admin.table("test_attempts").select("*").eq("id", attempt_id).maybe_single().execute()
        return res.data
    except Exception as e:
        logger.error(f"Error fetching attempt {attempt_id}: {e}")
        return None


def save_attempt_progress(attempt_id, mock_question_id, selected_option, status, time_taken_sec):
    try:
        row = {
            "attempt_id": attempt_id,
            "mock_question_id": mock_question_id,
            "selected_option": selected_option,
            "status": status,
            "time_taken_sec": int(time_taken_sec) if time_taken_sec is not None else 0,
        }
        supabase_admin.table("attempt_answers").upsert(
            row, on_conflict="attempt_id,mock_question_id"
        ).execute()
    except Exception as e:
        logger.error(f"Error saving attempt progress for attempt {attempt_id}, question {mock_question_id}: {e}")


def bulk_save_attempt_progress(attempt_id, entries):
    if not entries:
        return
    try:
        rows = [
            {
                "attempt_id": attempt_id,
                "mock_question_id": e["mock_question_id"],
                "selected_option": e.get("selected_option"),
                "status": e.get("status"),
                "time_taken_sec": int(e.get("time_taken_sec") or 0),
            }
            for e in entries
            if e.get("mock_question_id")
        ]
        if rows:
            supabase_admin.table("attempt_answers").upsert(
                rows, on_conflict="attempt_id,mock_question_id"
            ).execute()
    except Exception as e:
        logger.error(f"Error bulk saving attempt progress for attempt {attempt_id}: {e}")


_ANSWER_COLS_BASE = "mock_question_id, selected_option, is_correct, time_taken_sec, status"
_ANSWER_COLS_FULL = _ANSWER_COLS_BASE + ", mistake_note, was_replayed"


def get_attempt_answers_map(attempt_id):
    for cols in (_ANSWER_COLS_FULL, _ANSWER_COLS_BASE):
        try:
            res = (
                supabase_admin.table("attempt_answers")
                .select(cols)
                .eq("attempt_id", attempt_id)
                .execute()
            )
            if not res.data:
                return {}
            return {row["mock_question_id"]: row for row in res.data if row.get("mock_question_id")}
        except Exception as e:
            if cols is _ANSWER_COLS_BASE:
                logger.error(f"Error fetching attempt answers map for attempt {attempt_id}: {e}")
                return {}
            logger.warning(f"attempt_answers new columns unavailable: {e}")
    return {}


def get_mock_questions_for_test_review(test_id, attempt_id):
    try:
        res = (
            supabase_admin.table("mock_test_questions")
            .select(
                "question_order, "
                "mock_questions(id, question_text, option_a, option_b, option_c, "
                "option_d, image_url, has_image, correct_option, subjects(name))"
            )
            .eq("test_id", test_id)
            .order("question_order")
            .execute()
        )
        answers_by_qid = get_attempt_answers_map(attempt_id)

        grouped = {}
        if res.data:
            for row in res.data:
                q = row.get("mock_questions")
                if not q:
                    continue
                subj_name = q["subjects"]["name"] if q.get("subjects") else "General"
                answer = answers_by_qid.get(q["id"], {})
                grouped.setdefault(subj_name, []).append({
                    "id": q["id"],
                    "question_text": q["question_text"],
                    "option_a": q["option_a"],
                    "option_b": q["option_b"],
                    "option_c": q["option_c"],
                    "option_d": q["option_d"],
                    "image_url": _visible_image(q),
                    "correct_option": q["correct_option"],
                    "subject_name": subj_name,
                    "question_order": row.get("question_order", 0),
                    "selected_option": answer.get("selected_option"),
                    "is_correct": answer.get("is_correct"),
                    "time_taken_sec": answer.get("time_taken_sec") or 0,
                })
        return grouped
    except Exception as e:
        logger.error(f"Error fetching review questions for test {test_id}, attempt {attempt_id}: {e}")
        return {}


def submit_test_attempt(attempt_id, test_id, progress_map=None, valid_ids=None, answers=None, time_by_question=None):
    """
    Grades and submits a Mock Test Attempt using the Official NTA NEET Pattern:
      * If a subject has 50 questions, up to 45 attempted questions are evaluated
        (5 optional questions can be skipped with ZERO negative marking).
      * If Biology has 100 questions, up to 90 attempted questions are evaluated
        (10 optional questions can be skipped with ZERO negative marking).
      * Marks per correct question = effective_total_marks / total_attemptable (4.0 by default),
        NEVER diluted by optional questions (e.g., never 720/200 = 3.6).
      * Skipped questions NEVER deduct marks.
    """
    import datetime

    try:
        test = supabase_admin.table("tests").select("negative_marking, total_marks").eq("id", test_id).maybe_single().execute().data
        if not test:
            return None

        mapped = (
            supabase_admin.table("mock_test_questions")
            .select("mock_question_id, question_order, mock_questions(correct_option, subjects(name))")
            .eq("test_id", test_id)
            .order("question_order")
            .execute()
            .data or []
        )

        is_partial_reattempt = valid_ids is not None and len(valid_ids) < len(mapped)
        if valid_ids is not None:
            mapped = [row for row in mapped if row["mock_question_id"] in valid_ids]

        # Even if a test had 0 questions, mark submitted_at so /result never loops back to /attempt
        if not mapped:
            empty_summary = {
                "submitted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "score": 0,
                "total_questions": 0,
                "correct_count": 0,
                "wrong_count": 0,
                "skipped_count": 0,
            }
            supabase_admin.table("test_attempts").update(empty_summary).eq("id", attempt_id).execute()
            empty_summary["attempt_id"] = attempt_id
            return empty_summary

        if progress_map is None:
            progress_map = get_attempt_answers_map(attempt_id)
        answers = answers or {}
        time_by_question = time_by_question or {}

        # Group mapped questions by subject to enforce NTA per-subject attempt caps
        by_subject = {}
        for row in mapped:
            mq = row.get("mock_questions") or {}
            subj_name = (mq.get("subjects") or {}).get("name") or "General"
            by_subject.setdefault(subj_name, []).append(row)

        subj_counts = {s: len(rows) for s, rows in by_subject.items()}
        nta_meta = compute_nta_test_meta(subj_counts, stored_total_marks=test.get("total_marks"))

        total_questions = len(mapped)
        negative_marking = abs(float(test.get("negative_marking") or 0))
        marks_per_question = nta_meta["marks_per_question"]

        # Sync corrected total_marks back to `tests` if it was 0 or naive 150*4 / 200*4
        if not is_partial_reattempt and nta_meta["effective_total_marks"] != int(test.get("total_marks") or 0):
            try:
                supabase_admin.table("tests").update(
                    {"total_marks": nta_meta["effective_total_marks"]}
                ).eq("id", test_id).execute()
                cache.delete_memoized(get_test_by_id, test_id)
            except Exception:
                pass

        correct_count = 0
        wrong_count = 0
        skipped_count = 0
        answer_rows = []

        for subj_name, subj_rows in by_subject.items():
            subj_cap = len(subj_rows) if is_partial_reattempt else nta_meta["subject_caps"].get(subj_name, len(subj_rows))
            attempted_in_subj = 0

            for row in subj_rows:
                qid = row["mock_question_id"]
                correct_option = (row.get("mock_questions") or {}).get("correct_option")

                student_data = progress_map.get(qid, {})
                selected = student_data.get("selected_option") or answers.get(qid)
                if selected not in ("A", "B", "C", "D"):
                    selected = None
                time_taken = student_data.get("time_taken_sec") or time_by_question.get(qid) or 0
                existing_status = student_data.get("status") or "not_visited"

                # Enforce NTA per-subject cap: if more than subj_cap (e.g. >45 out of 50) were selected,
                # only the first subj_cap answered questions count; excess are treated as unattempted skips
                if selected and attempted_in_subj >= subj_cap:
                    selected = None
                    existing_status = "not_answered"

                if not selected:
                    skipped_count += 1
                    if time_taken > 0 or existing_status != "not_visited":
                        answer_rows.append({
                            "attempt_id": attempt_id,
                            "mock_question_id": qid,
                            "selected_option": None,
                            "is_correct": None,
                            "time_taken_sec": time_taken,
                            "status": existing_status,
                        })
                    continue

                attempted_in_subj += 1
                is_correct = (selected == correct_option)
                if is_correct:
                    correct_count += 1
                else:
                    wrong_count += 1

                answer_rows.append({
                    "attempt_id": attempt_id,
                    "mock_question_id": qid,
                    "selected_option": selected,
                    "is_correct": is_correct,
                    "time_taken_sec": time_taken,
                    "status": existing_status,
                })

        if answer_rows:
            supabase_admin.table("attempt_answers").upsert(
                answer_rows, on_conflict="attempt_id,mock_question_id"
            ).execute()

        score = round((correct_count * marks_per_question) - (wrong_count * negative_marking), 2)

        summary = {
            "submitted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "score": score,
            "total_questions": total_questions,
            "correct_count": correct_count,
            "wrong_count": wrong_count,
            "skipped_count": skipped_count,
        }

        supabase_admin.table("test_attempts").update(summary).eq("id", attempt_id).execute()
        summary["attempt_id"] = attempt_id
        summary["attemptable_questions"] = nta_meta["total_attemptable"]
        summary["optional_skips"] = nta_meta["optional_skips"]
        summary["effective_total_marks"] = nta_meta["effective_total_marks"]
        return summary
    except Exception as e:
        logger.error(f"Error submitting test attempt {attempt_id} for test {test_id}: {e}")
        return None


def get_attempts_for_user(user_id):
    try:
        res = (
            supabase_admin.table("test_attempts")
            .select("id, test_id, started_at, submitted_at, score, total_questions, "
                    "correct_count, wrong_count, skipped_count, parent_attempt_id, "
                    "tests(title, total_marks, streams(slug), test_categories(name))")
            .eq("user_id", user_id)
            .is_("parent_attempt_id", "null")
            .order("started_at", desc=True)
            .execute()
        )
        rows = res.data or []
        totals, seen = {}, {}
        for r in rows:
            totals[r["test_id"]] = totals.get(r["test_id"], 0) + 1
        for r in rows:
            seen[r["test_id"]] = seen.get(r["test_id"], 0) + 1
            r["attempt_no"] = totals[r["test_id"]] - seen[r["test_id"]] + 1
            r["is_latest"] = seen[r["test_id"]] == 1
        return rows
    except Exception as e:
        logger.error(f"Error fetching attempts for user {user_id}: {e}")
        return []


def delete_all_attempts_for_user(user_id):
    try:
        supabase_admin.table("practice_attempts").delete().eq("user_id", user_id).execute()
    except Exception as e:
        logger.warning(f"Could not clear practice attempts for user {user_id}: {e}")
    try:
        res = supabase_admin.table("test_attempts").delete().eq("user_id", user_id).execute()
        return res.data
    except Exception as e:
        logger.error(f"Error deleting all attempts for user {user_id}: {e}")
        return None


def get_attempt_time_breakdown(attempt_id, test_id):
    try:
        order_res = (
            supabase_admin.table("mock_test_questions")
            .select("mock_question_id, question_order")
            .eq("test_id", test_id)
            .order("question_order")
            .execute()
        )
        order_by_qid = {row["mock_question_id"]: row.get("question_order", 0) for row in order_res.data} if order_res.data else {}

        questions_res = None
        if order_by_qid:
            questions_res = (
                supabase_admin.table("mock_questions")
                .select("id, question_text, subjects(name)")
                .in_("id", list(order_by_qid.keys()))
                .execute()
            )
        
        question_lookup = {}
        if questions_res and questions_res.data:
            for q in questions_res.data:
                subj_name = q["subjects"]["name"] if q.get("subjects") else "General"
                question_lookup[q["id"]] = {"question_text": q["question_text"], "subject_name": subj_name}

        answers_res = (
            supabase_admin.table("attempt_answers")
            .select("mock_question_id, selected_option, is_correct, time_taken_sec")
            .eq("attempt_id", attempt_id)
            .execute()
        )
        answers_by_qid = {row["mock_question_id"]: row for row in answers_res.data if row.get("mock_question_id")} if answers_res.data else {}

        by_subject = {}
        by_question = []
        for qid, order in order_by_qid.items():
            meta = question_lookup.get(qid, {"question_text": "", "subject_name": "General"})
            answer = answers_by_qid.get(qid, {})
            time_taken = int(answer.get("time_taken_sec") or 0)

            by_subject[meta["subject_name"]] = by_subject.get(meta["subject_name"], 0) + time_taken
            by_question.append({
                "question_order": order,
                "subject_name": meta["subject_name"],
                "question_text": meta["question_text"],
                "time_taken_sec": time_taken,
                "selected_option": answer.get("selected_option"),
                "is_correct": answer.get("is_correct"),
            })

        by_question.sort(key=lambda r: r["question_order"])
        return {"by_subject": by_subject, "by_question": by_question}
    except Exception as e:
        logger.error(f"Error generating time breakdown for attempt {attempt_id}, test {test_id}: {e}")
        return {"by_subject": {}, "by_question": []}


def user_has_access_to_test(test, user_id):
    from app.shared.access import can_access_test
    return can_access_test(test, user_id)


PRACTICE_ATTEMPT_KINDS = ("full", "wrong_only", "marked_and_wrong_only")
_VALID_OPTIONS = ("A", "B", "C", "D")
_VALID_STATUSES = ("answered", "marked", "answered_marked", "not_answered", "not_visited")
MISTAKE_NOTE_MAX_LEN = 2000


def _quiz_filter(query, chapter_id, mode, category, folder_name, set_name):
    return (
        query.eq("chapter_id", chapter_id)
        .eq("is_pyq", mode == "pyq")
        .eq("category", category)
        .eq("folder_name", folder_name)
        .eq("set_name", set_name)
    )


def get_practice_quiz_questions(chapter_id, mode, category, folder_name, set_name, columns="*"):
    try:
        out, start, page = [], 0, 1000
        while True:
            q = supabase_admin.table("questions").select(columns)
            chunk = (_quiz_filter(q, chapter_id, mode, category, folder_name, set_name)
                     .order("id").range(start, start + page - 1).execute().data or [])
            out.extend(chunk)
            if len(chunk) < page:
                break
            start += page
        if out:
            return out

        extra_cols = columns if columns.strip() == "*" else f"{columns}, folder_name, set_name, topic_name, category"
        all_rows = _fetch_all_chapter_question_rows(chapter_id, mode == "pyq", extra_cols)
        target_cat = str(category or "topic").strip().lower()
        target_fn = str(folder_name or "").strip() or DEFAULT_PRACTICE_FOLDER
        target_sn = str(set_name or "").strip()
        matched = []
        wanted_keys = None if columns.strip() == "*" else [c.strip() for c in columns.split(",") if c.strip()]
        for r in all_rows:
            cat, fn, sn = _normalize_practice_row(r)
            if cat == target_cat and fn == target_fn and sn == target_sn:
                if wanted_keys is not None:
                    matched.append({k: r.get(k) for k in wanted_keys})
                else:
                    matched.append(r)
        return matched
    except Exception as e:
        logger.error(f"Error fetching practice quiz questions ({chapter_id}/{mode}/{category}/{folder_name}/{set_name}): {e}")
        return []


PRACTICE_RUNNER_COLUMNS = (
    "id, question_text, option_a, option_b, option_c, option_d, "
    "correct_option, explanation, image_url, has_image"
)


def practice_set_is_premium(chapter_id, mode, category, folder_name, set_name):
    try:
        q = supabase_admin.table("questions").select("id")
        rows = (
            _quiz_filter(q, chapter_id, mode, category, folder_name, set_name)
            .eq("is_premium", True).limit(1).execute().data or []
        )
        if rows:
            return True
        q_any = supabase_admin.table("questions").select("id")
        any_exact = _quiz_filter(q_any, chapter_id, mode, category, folder_name, set_name).limit(1).execute().data or []
        if any_exact:
            return False
        legacy_rows = get_practice_quiz_questions(chapter_id, mode, category, folder_name, set_name, columns="id, is_premium")
        return any(r.get("is_premium") for r in legacy_rows)
    except Exception as e:
        logger.error(f"Error checking premium status of practice set ({chapter_id}/{set_name}): {e}")
        return True


def get_latest_practice_attempt(user_id, chapter_id, mode, category, folder_name, set_name):
    if not user_id:
        return None
    try:
        res = (
            supabase_admin.table("practice_attempts").select("*")
            .eq("user_id", user_id).eq("chapter_id", chapter_id).eq("mode", mode)
            .eq("category", category).eq("folder_name", folder_name).eq("set_name", set_name)
            .is_("parent_attempt_id", "null").order("started_at", desc=True).limit(1).execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Error fetching latest practice attempt for user {user_id}: {e}")
        return None


def get_latest_practice_reattempt_session(parent_attempt_id):
    try:
        res = (
            supabase_admin.table("practice_attempts").select("*")
            .eq("parent_attempt_id", parent_attempt_id).order("started_at", desc=True).limit(1).execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Error fetching reattempt session for parent {parent_attempt_id}: {e}")
        return None


def get_practice_attempt_by_id(attempt_id):
    try:
        res = supabase_admin.table("practice_attempts").select("*").eq("id", attempt_id).maybe_single().execute()
        return res.data
    except Exception as e:
        logger.error(f"Error fetching practice attempt {attempt_id}: {e}")
        return None


def get_practice_answers_map(attempt_id):
    try:
        res = (
            supabase_admin.table("practice_attempt_answers")
            .select("question_id, selected_option, is_correct, time_taken_sec, status, mistake_note, was_replayed")
            .eq("attempt_id", attempt_id)
            .execute()
        )
        return {row["question_id"]: row for row in (res.data or [])}
    except Exception as e:
        logger.error(f"Error fetching practice answers for attempt {attempt_id}: {e}")
        return {}


def _is_marked(status):
    return status in ("marked", "answered_marked")


def _is_wrong_or_unattempted(row):
    if not row:
        return True
    if not row.get("selected_option"):
        return True
    return row.get("is_correct") is False


def select_reattempt_question_ids(all_question_ids, old_answers, kind):
    if kind == "full":
        return list(all_question_ids)
    picked = []
    for qid in all_question_ids:
        row = old_answers.get(qid)
        wrong = _is_wrong_or_unattempted(row)
        marked = _is_marked((row or {}).get("status"))
        if kind == "wrong_only" and wrong:
            picked.append(qid)
        elif kind == "marked_and_wrong_only" and (wrong or marked):
            picked.append(qid)
    return picked


def start_practice_attempt(user_id, chapter_id, mode, category, folder_name, set_name, kind="full"):
    if not user_id:
        return None, "not_logged_in"
    if kind not in PRACTICE_ATTEMPT_KINDS:
        return None, "bad_kind"
    all_ids = [q["id"] for q in get_practice_quiz_questions(chapter_id, mode, category, folder_name, set_name, columns="id")]
    if not all_ids:
        return None, "no_questions"
    base_row = {"user_id": user_id, "chapter_id": chapter_id, "mode": mode, "category": category,
                "folder_name": folder_name, "set_name": set_name}

    if kind == "full":
        _discard_unsubmitted_full_attempts("practice_attempts", base_row)
        try:
            res = supabase_admin.table("practice_attempts").insert(
                {**base_row, "attempt_kind": "full", "parent_attempt_id": None}).execute()
            return (res.data[0]["id"], all_ids) if res.data else (None, "db_error")
        except Exception as e:
            logger.error(f"Error starting full practice attempt for user {user_id}: {e}")
            return None, "db_error"

    parent = get_latest_practice_attempt(user_id, chapter_id, mode, category, folder_name, set_name)
    if not parent or not parent.get("submitted_at"):
        return None, "no_previous_attempt"
    parent_answers = get_practice_answers_map(parent["id"])
    replay_ids = select_reattempt_question_ids(all_ids, parent_answers, kind)
    if not replay_ids:
        return None, "nothing_to_reattempt"

    new_id = None
    try:
        res = supabase_admin.table("practice_attempts").insert(
            {**base_row, "attempt_kind": kind, "parent_attempt_id": parent["id"]}).execute()
        if not res.data:
            raise RuntimeError("insert returned no row")
        new_id = res.data[0]["id"]
        replay_set = set(replay_ids)
        carry_rows = []
        for qid in all_ids:
            prev = parent_answers.get(qid)
            if qid in replay_set or not prev:
                continue
            carry_rows.append({
                "attempt_id": new_id, "question_id": qid, "selected_option": prev.get("selected_option"),
                "is_correct": prev.get("is_correct"), "time_taken_sec": prev.get("time_taken_sec") or 0,
                "status": prev.get("status"), "mistake_note": prev.get("mistake_note"), "was_replayed": False})
        if carry_rows:
            supabase_admin.table("practice_attempt_answers").insert(carry_rows).execute()
        return new_id, replay_ids
    except Exception as e:
        logger.error(f"Error starting practice reattempt session for user {user_id}: {e}")
        if new_id:
            try:
                supabase_admin.table("practice_attempts").delete().eq("id", new_id).execute()
            except Exception:
                pass
        return None, "db_error"


def save_practice_progress(attempt_id, question_id, selected_option, status, time_taken_sec):
    try:
        if selected_option not in _VALID_OPTIONS:
            selected_option = None
        if status not in _VALID_STATUSES:
            status = "answered" if selected_option else "not_answered"
        row = {
            "attempt_id": attempt_id,
            "question_id": question_id,
            "selected_option": selected_option,
            "status": status,
            "time_taken_sec": max(0, int(time_taken_sec or 0)),
            "was_replayed": True,
        }
        supabase_admin.table("practice_attempt_answers").upsert(row, on_conflict="attempt_id,question_id").execute()
        return True
    except Exception as e:
        logger.error(f"Error saving practice progress for attempt {attempt_id}, question {question_id}: {e}")
        return False


def save_practice_mistake_note(attempt_id, question_id, note):
    note = (note or "").strip()
    if len(note) > MISTAKE_NOTE_MAX_LEN:
        note = note[:MISTAKE_NOTE_MAX_LEN]
    try:
        existing = (
            supabase_admin.table("practice_attempt_answers")
            .select("id")
            .eq("attempt_id", attempt_id)
            .eq("question_id", question_id)
            .limit(1)
            .execute()
            .data
        )
        if existing:
            supabase_admin.table("practice_attempt_answers").update({"mistake_note": note or None}).eq("id", existing[0]["id"]).execute()
        else:
            supabase_admin.table("practice_attempt_answers").insert({
                "attempt_id": attempt_id,
                "question_id": question_id,
                "mistake_note": note or None,
                "status": "not_visited",
                "was_replayed": True,
            }).execute()
        return True
    except Exception as e:
        logger.error(f"Error saving mistake note for attempt {attempt_id}, question {question_id}: {e}")
        return False


def submit_practice_attempt(attempt_id, replay_question_ids, submitted_answers=None, total_time_sec=0):
    import datetime
    try:
        attempt = get_practice_attempt_by_id(attempt_id)
        if not attempt:
            return None

        questions = get_practice_quiz_questions(
            attempt["chapter_id"], attempt["mode"], attempt["category"],
            attempt["folder_name"], attempt["set_name"],
            columns="id, correct_option, marks, negative_marks",
        )
        if not questions:
            return None
        by_id = {q["id"]: q for q in questions}
        replay_set = {qid for qid in replay_question_ids if qid in by_id}

        progress = get_practice_answers_map(attempt_id)
        for qid, entry in (submitted_answers or {}).items():
            if qid not in replay_set or not isinstance(entry, dict):
                continue
            sel = entry.get("selected_option")
            sel = sel if sel in _VALID_OPTIONS else None
            st = entry.get("status")
            if st not in _VALID_STATUSES:
                st = "answered" if sel else "not_answered"
            prev = progress.get(qid, {})
            progress[qid] = {
                **prev,
                "selected_option": sel,
                "status": st,
                "time_taken_sec": max(0, int(entry.get("time_taken_sec") or 0)),
            }

        correct = wrong = skipped = 0
        score = 0.0
        upserts = []
        for qid, q in by_id.items():
            row = progress.get(qid) or {}
            selected = row.get("selected_option")
            marks = float(q.get("marks") if q.get("marks") is not None else 4)
            neg = abs(float(q.get("negative_marks") if q.get("negative_marks") is not None else 1))

            if qid in replay_set:
                if not selected:
                    is_correct = None
                else:
                    is_correct = (selected == q["correct_option"])
                upserts.append({
                    "attempt_id": attempt_id,
                    "question_id": qid,
                    "selected_option": selected,
                    "is_correct": is_correct,
                    "time_taken_sec": int(row.get("time_taken_sec") or 0),
                    "status": row.get("status") or ("answered" if selected else "not_visited"),
                    "was_replayed": True,
                })
            else:
                is_correct = row.get("is_correct")
                if selected and is_correct is None:
                    is_correct = (selected == q["correct_option"])

            if not selected:
                skipped += 1
            elif is_correct:
                correct += 1
                score += marks
            else:
                wrong += 1
                score -= neg

        if upserts:
            supabase_admin.table("practice_attempt_answers").upsert(upserts, on_conflict="attempt_id,question_id").execute()

        summary = {
            "submitted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "score": round(score, 2),
            "total_questions": len(by_id),
            "correct_count": correct,
            "wrong_count": wrong,
            "skipped_count": skipped,
            "total_time_sec": max(0, int(total_time_sec or 0)),
        }
        supabase_admin.table("practice_attempts").update(summary).eq("id", attempt_id).execute()
        summary["attempt_id"] = attempt_id
        return summary
    except Exception as e:
        logger.error(f"Error submitting practice attempt {attempt_id}: {e}")
        return None


def get_practice_review_items(attempt_id, filter_kind="full"):
    attempt = get_practice_attempt_by_id(attempt_id)
    if not attempt:
        return []
    questions = get_practice_quiz_questions(
        attempt["chapter_id"], attempt["mode"], attempt["category"],
        attempt["folder_name"], attempt["set_name"],
    )
    answers = get_practice_answers_map(attempt_id)

    items = []
    for idx, q in enumerate(questions, start=1):
        a = answers.get(q["id"])
        is_wrong = _is_wrong_or_unattempted(a)
        a = a or {}
        is_marked = _is_marked(a.get("status"))
        if filter_kind == "wrong" and not is_wrong:
            continue
        if filter_kind == "marked" and not is_marked:
            continue
        items.append({
            "number": idx,
            "id": q["id"],
            "question_text": q.get("question_text"),
            "option_a": q.get("option_a"), "option_b": q.get("option_b"),
            "option_c": q.get("option_c"), "option_d": q.get("option_d"),
            "image_url": _visible_image(q),
            "correct_option": q.get("correct_option"),
            "explanation": q.get("explanation"),
            "selected_option": a.get("selected_option"),
            "is_correct": a.get("is_correct"),
            "is_marked": is_marked,
            "time_taken_sec": a.get("time_taken_sec") or 0,
            "mistake_note": a.get("mistake_note") or "",
        })
    return items


def get_practice_analyse_counts(attempt_id):
    attempt = get_practice_attempt_by_id(attempt_id)
    if not attempt:
        return {"wrong": 0, "marked": 0, "wrong_or_marked": 0, "full": 0}
    questions = get_practice_quiz_questions(
        attempt["chapter_id"], attempt["mode"], attempt["category"],
        attempt["folder_name"], attempt["set_name"], columns="id",
    )
    answers = get_practice_answers_map(attempt_id)
    wrong = marked = either = 0
    for q in questions:
        a = answers.get(q["id"])
        w = _is_wrong_or_unattempted(a)
        m = _is_marked((a or {}).get("status"))
        wrong += w
        marked += m
        either += (w or m)
    return {"wrong": wrong, "marked": marked, "wrong_or_marked": either, "full": len(questions)}


def get_practice_time_breakdown(attempt_id):
    attempt = get_practice_attempt_by_id(attempt_id)
    if not attempt:
        return {"total_seconds": 0, "by_question": []}
    questions = get_practice_quiz_questions(
        attempt["chapter_id"], attempt["mode"], attempt["category"],
        attempt["folder_name"], attempt["set_name"],
    )
    answers = get_practice_answers_map(attempt_id)

    by_question = []
    total_seconds = 0
    for idx, q in enumerate(questions, start=1):
        a = answers.get(q["id"]) or {}
        time_taken = int(a.get("time_taken_sec") or 0)
        total_seconds += time_taken
        by_question.append({
            "question_order": idx,
            "question_text": q.get("question_text"),
            "time_taken_sec": time_taken,
            "selected_option": a.get("selected_option"),
            "is_correct": a.get("is_correct"),
        })
    stored_total = attempt.get("total_time_sec")
    if stored_total:
        total_seconds = int(stored_total)
    return {"total_seconds": total_seconds, "by_question": by_question}


def get_mock_review_items(attempt_id, test_id, filter_kind="full"):
    try:
        res = (
            supabase_admin.table("mock_test_questions")
            .select(
                "question_order, "
                "mock_questions(id, question_text, option_a, option_b, option_c, option_d, "
                "image_url, has_image, correct_option, explanation, subjects(name))"
            )
            .eq("test_id", test_id)
            .order("question_order")
            .execute()
        )
        answers = {}
        ans_res = (
            supabase_admin.table("attempt_answers")
            .select("mock_question_id, selected_option, is_correct, time_taken_sec, status, mistake_note, was_replayed")
            .eq("attempt_id", attempt_id)
            .execute()
        )
        for row in (ans_res.data or []):
            if row.get("mock_question_id"):
                answers[row["mock_question_id"]] = row

        items = []
        number = 0
        for row in (res.data or []):
            q = row.get("mock_questions")
            if not q:
                continue
            number += 1
            a = answers.get(q["id"])
            is_wrong = _is_wrong_or_unattempted(a)
            a = a or {}
            is_marked = _is_marked(a.get("status"))
            if filter_kind == "wrong" and not is_wrong:
                continue
            if filter_kind == "marked" and not is_marked:
                continue
            items.append({
                "number": number,
                "id": q["id"],
                "subject_name": (q.get("subjects") or {}).get("name") or "General",
                "question_text": q.get("question_text"),
                "option_a": q.get("option_a"), "option_b": q.get("option_b"),
                "option_c": q.get("option_c"), "option_d": q.get("option_d"),
                "image_url": _visible_image(q),
                "correct_option": q.get("correct_option"),
                "explanation": q.get("explanation"),
                "selected_option": a.get("selected_option"),
                "is_correct": a.get("is_correct"),
                "is_marked": is_marked,
                "time_taken_sec": a.get("time_taken_sec") or 0,
                "mistake_note": a.get("mistake_note") or "",
            })
        return items
    except Exception as e:
        logger.error(f"Error building mock review items for attempt {attempt_id}: {e}")
        return []


def get_mock_analyse_counts(attempt_id, test_id):
    items = get_mock_review_items(attempt_id, test_id, "full")
    def _w(i):
        return (not i["selected_option"]) or i["is_correct"] is False
    wrong = sum(1 for i in items if _w(i))
    marked = sum(1 for i in items if i["is_marked"])
    either = sum(1 for i in items if _w(i) or i["is_marked"])
    return {"wrong": wrong, "marked": marked, "wrong_or_marked": either, "full": len(items)}


def save_mock_mistake_note(attempt_id, mock_question_id, note):
    note = (note or "").strip()[:MISTAKE_NOTE_MAX_LEN]
    try:
        existing = (
            supabase_admin.table("attempt_answers")
            .select("id")
            .eq("attempt_id", attempt_id)
            .eq("mock_question_id", mock_question_id)
            .limit(1)
            .execute()
            .data
        )
        if existing:
            supabase_admin.table("attempt_answers").update({"mistake_note": note or None}).eq("id", existing[0]["id"]).execute()
        else:
            supabase_admin.table("attempt_answers").insert({
                "attempt_id": attempt_id,
                "mock_question_id": mock_question_id,
                "mistake_note": note or None,
                "status": "not_visited",
            }).execute()
        return True
    except Exception as e:
        logger.error(f"Error saving mock mistake note for attempt {attempt_id}, question {mock_question_id}: {e}")
        return False


def get_latest_full_mock_attempt(test_id, user_id):
    if not user_id:
        return None
    try:
        rows = (supabase_admin.table("test_attempts").select("*")
                .eq("test_id", test_id).eq("user_id", user_id).is_("parent_attempt_id", "null")
                .order("started_at", desc=True).limit(1).execute().data)
        return rows[0] if rows else None
    except Exception as e:
        logger.error(f"Error reading latest full attempt for test {test_id}, user {user_id}: {e}")
        return None


def get_latest_mock_reattempt_session(parent_attempt_id):
    try:
        rows = (supabase_admin.table("test_attempts").select("*")
                .eq("parent_attempt_id", parent_attempt_id).order("started_at", desc=True).limit(1).execute().data)
        return rows[0] if rows else None
    except Exception as e:
        logger.error(f"Error reading reattempt session for parent {parent_attempt_id}: {e}")
        return None


def start_mock_reattempt(test_id, user_id, kind="full"):
    if not user_id:
        return None, "not_logged_in"
    if kind not in PRACTICE_ATTEMPT_KINDS:
        return None, "bad_kind"
    all_ids = list(get_mock_question_ids_for_test(test_id) or [])
    if not all_ids:
        return None, "no_questions"
    if kind == "full":
        new_id = create_test_attempt(test_id, user_id=user_id)
        return (new_id, all_ids) if new_id else (None, "db_error")

    parent = get_latest_full_mock_attempt(test_id, user_id)
    if not parent or not parent.get("submitted_at"):
        return None, "no_previous_attempt"
    try:
        rows = (supabase_admin.table("attempt_answers")
                .select("mock_question_id, selected_option, is_correct, time_taken_sec, status, mistake_note")
                .eq("attempt_id", parent["id"]).execute().data or [])
        parent_answers = {r["mock_question_id"]: r for r in rows if r.get("mock_question_id")}
    except Exception as e:
        logger.error(f"Error reading parent answers for attempt {parent['id']}: {e}")
        return None, "db_error"
    replay_ids = select_reattempt_question_ids(all_ids, parent_answers, kind)
    if not replay_ids:
        return None, "nothing_to_reattempt"

    new_id = None
    try:
        res = supabase_admin.table("test_attempts").insert(
            {"test_id": test_id, "user_id": user_id, "attempt_kind": kind, "parent_attempt_id": parent["id"]}).execute()
        if not res.data:
            raise RuntimeError("insert returned no row")
        new_id = res.data[0]["id"]
        replay_set = set(replay_ids)
        carry = []
        for qid in all_ids:
            prev = parent_answers.get(qid)
            if qid in replay_set or not prev:
                continue
            carry.append({
                "attempt_id": new_id, "mock_question_id": qid, "selected_option": prev.get("selected_option"),
                "is_correct": prev.get("is_correct"), "time_taken_sec": prev.get("time_taken_sec") or 0,
                "status": prev.get("status"), "mistake_note": prev.get("mistake_note"), "was_replayed": False})
        if carry:
            supabase_admin.table("attempt_answers").insert(carry).execute()
        return new_id, replay_ids
    except Exception as e:
        logger.error(f"Error starting mock reattempt for test {test_id}, user {user_id}: {e}")
        if new_id:
            try:
                supabase_admin.table("test_attempts").delete().eq("id", new_id).execute()
            except Exception:
                pass
        return None, "db_error"
