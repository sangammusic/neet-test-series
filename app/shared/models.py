"""
Thin data-access functions shared by user and admin blueprints.

Rule of thumb: if BOTH admin and user need to *read* the same data
shape (e.g. "list active streams"), it belongs here. Write operations
that only admin performs (create/edit/delete streams etc.) live in
app/admin/*_routes.py instead, using supabase_admin directly — no
need to funnel every admin write through this shared file.
"""
import logging
from app.extensions import supabase_public, supabase_admin, cache

logger = logging.getLogger(__name__)


@cache.memoize(timeout=900)  # 15 min — see app/extensions.py for cache setup
def get_active_streams():
    """Public catalog read — used on the stream-selection page."""
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
    """
    Returns the list of streams (id, name, slug) a logged-in user has
    already selected, by reading user_streams. Returns [] if no
    user_id is given, or if the user hasn't picked any stream yet.
    """
    if not user_id:
        return []

    try:
        res = (
            supabase_public.table("user_streams")
            .select("streams(id, name, slug)")
            .eq("user_id", user_id)
            .execute()
        )
        return [row["streams"] for row in res.data if row.get("streams")]
    except Exception as e:
        logger.error(f"Error fetching streams for user {user_id}: {e}")
        return []


def get_stream_by_slug(slug: str):
    try:
        # SANGAM FIX: Changed .single() to .maybe_single() to prevent 500 crash on bad slug
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


# ---------- Added in Phase 3: chapter-wise practice + mock test feed ----------

def get_chapter_by_id(chapter_id):
    try:
        # SANGAM FIX: maybe_single() to prevent 500 crash
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
        return res.data
    except Exception as e:
        logger.error(f"Error fetching tests for stream {stream_id}: {e}")
        return []


def get_test_by_id(test_id):
    try:
        # SANGAM FIX: maybe_single() to prevent 500 crash
        res = supabase_public.table("tests").select("*").eq("id", test_id).maybe_single().execute()
        return res.data
    except Exception as e:
        logger.error(f"Error fetching test {test_id}: {e}")
        return None


def get_test_syllabus(test_id):
    """
    Mock tests are mapped via mock_test_questions -> mock_questions.
    Reads topic_name (mock_questions has no chapter_id) grouped by subject.
    """
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


@cache.memoize(timeout=300)  # 5 min: this is the heaviest per-question JOIN
# query in the whole app and every student attempting/reloading a test
# hits it, so it's the single highest-value cache target for surviving
# a mock-test rush. 5 min (not 15, like the smaller catalog lookups
# above) because an admin editing a live test's questions should not
# stay stale for too long.
def get_mock_questions_for_test(test_id):
    """
    Fetches every mock_questions row mapped to this test via mock_test_questions.
    Stripped of correct_option/explanation to prevent browser answer leaking.
    """
    try:
        res = (
            supabase_public.table("mock_test_questions")
            .select(
                "question_order, "
                "mock_questions(id, question_text, option_a, option_b, option_c, "
                "option_d, image_url, subjects(name))"
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
                    "image_url": q.get("image_url"),
                    "subject_name": subj_name,
                    "question_order": row.get("question_order", 0),
                })
        return grouped
    except Exception as e:
        logger.error(f"Error fetching mock questions for test {test_id}: {e}")
        return {}


@cache.memoize(timeout=300)
def get_mock_question_ids_for_test(test_id):
    """Lightweight helper: just the mock_question_id list for a test, for validating a submission."""
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


def create_test_attempt(test_id, user_id=None):
    """
    Starts a new attempt row. Wipes out any existing attempts for this test by this user
    to save storage and guarantee only the latest attempt is maintained.
    """
    if not user_id:
        return None

    try:
        # 1. DELETE existing attempts for this test + user to save storage
        supabase_admin.table("test_attempts").delete().eq("test_id", test_id).eq("user_id", user_id).execute()

        # 2. INSERT the fresh new attempt
        payload = {"test_id": test_id, "user_id": user_id}
        res = supabase_admin.table("test_attempts").insert(payload).execute()
        
        if not res.data:
            return None
        return res.data[0]["id"]
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


# =====================================================================
# NEW FOUNDATION FUNCTIONS FOR ROBUST DATA SYNC & REVIEW MODE
# =====================================================================

def save_attempt_progress(attempt_id, mock_question_id, selected_option, status, time_taken_sec):
    """
    Durable per-question progress save. Upserts directly into attempt_answers.
    """
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
    """
    Foolproof bulk sync called right before grading.
    """
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


def get_attempt_answers_map(attempt_id):
    """
    Reads back every attempt_answers row for this attempt. Used to hydrate
    Alpine state on page reload, and to pull exact time/options for Review Mode.
    """
    try:
        res = (
            supabase_admin.table("attempt_answers")
            .select("mock_question_id, selected_option, is_correct, time_taken_sec, status")
            .eq("attempt_id", attempt_id)
            .execute()
        )
        if not res.data:
            return {}
        return {row["mock_question_id"]: row for row in res.data if row.get("mock_question_id")}
    except Exception as e:
        logger.error(f"Error fetching attempt answers map for attempt {attempt_id}: {e}")
        return {}


def get_mock_questions_for_test_review(test_id, attempt_id):
    """
    Review-mode variant. INCLUDES correct_option safely, and merges user saved answers.
    """
    try:
        res = (
            supabase_admin.table("mock_test_questions")
            .select(
                "question_order, "
                "mock_questions(id, question_text, option_a, option_b, option_c, "
                "option_d, image_url, correct_option, subjects(name))"
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
                    "image_url": q.get("image_url"),
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
    Scores and finalizes an attempt using the database as the absolute source of truth.

    PERF NOTE: pass `progress_map` (the dict returned by
    get_attempt_answers_map(attempt_id)) when the caller has already
    fetched it -- e.g. right after bulk_save_attempt_progress(), which
    is the normal submit flow. This function used to always re-fetch
    attempt_answers itself even when the caller had just read the exact
    same rows a moment earlier, costing one extra Supabase round-trip on
    every single test submission. If progress_map isn't supplied (e.g.
    called from elsewhere without a pre-fetched map), it's fetched here
    as before so this stays a safe drop-in.

    `answers` / `time_by_question` (legacy dict-per-field shape) are
    still accepted for backward compatibility but are ignored once
    progress_map is available, since progress_map already carries both.
    """
    import datetime

    try:
        # SANGAM FIX: maybe_single() protects against crash on invalid test_id
        test = supabase_admin.table("tests").select("negative_marking, total_marks").eq("id", test_id).maybe_single().execute().data
        if not test:
            return None

        mapped = (
            supabase_admin.table("mock_test_questions")
            .select("mock_question_id, mock_questions(correct_option)")
            .eq("test_id", test_id)
            .execute()
            .data
        )
        if not mapped:
            return None

        if valid_ids is not None:
            mapped = [row for row in mapped if row["mock_question_id"] in valid_ids]
            if not mapped:
                return None

        if progress_map is None:
            progress_map = get_attempt_answers_map(attempt_id)
        answers = answers or {}
        time_by_question = time_by_question or {}

        total_questions = len(mapped)
        total_marks = int(test.get("total_marks") or total_questions)
        negative_marking = abs(float(test.get("negative_marking") or 0)) 
        
        marks_per_question = float(total_marks) / total_questions if total_questions > 0 else 1.0

        correct_count = 0
        wrong_count = 0
        skipped_count = 0
        answer_rows = []

        for row in mapped:
            qid = row["mock_question_id"]
            correct_option = (row.get("mock_questions") or {}).get("correct_option")
            
            student_data = progress_map.get(qid, {})
            selected = student_data.get("selected_option") or answers.get(qid)
            time_taken = student_data.get("time_taken_sec") or time_by_question.get(qid) or 0
            existing_status = student_data.get("status") or "not_visited"

            if not selected:
                skipped_count += 1
                if time_taken > 0 or existing_status != "not_visited":
                    answer_rows.append({
                        "attempt_id": attempt_id,
                        "mock_question_id": qid,
                        "selected_option": None,
                        "is_correct": None,
                        "time_taken_sec": time_taken,
                        "status": existing_status
                    })
                continue

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
                "status": existing_status
            })

        if answer_rows:
            supabase_admin.table("attempt_answers").upsert(
                answer_rows, on_conflict="attempt_id,mock_question_id"
            ).execute()

        score = (correct_count * marks_per_question) - (wrong_count * negative_marking)

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
        return summary
    except Exception as e:
        logger.error(f"Error submitting test attempt {attempt_id} for test {test_id}: {e}")
        return None


def get_attempts_for_user(user_id):
    try:
        res = (
            supabase_admin.table("test_attempts")
            .select("id, test_id, started_at, submitted_at, score, total_questions, "
                    "correct_count, wrong_count, skipped_count, "
                    "tests(title, total_marks, test_categories(name))")
            .eq("user_id", user_id)
            .order("started_at", desc=True)
            .execute()
        )
        return res.data
    except Exception as e:
        logger.error(f"Error fetching attempts for user {user_id}: {e}")
        return []


def delete_all_attempts_for_user(user_id):
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
    # BUGFIX: a bad/deleted test_id means get_test_by_id() returns None,
    # and test.get(...) on None used to crash the whole request with a
    # 500 instead of the caller's normal 404 handling. Treat "no test"
    # as "no access" so callers can decide what to show.
    if not test:
        return False
    if not test.get("is_premium"):
        return True
    if not user_id:
        return False
    try:
        res = (
            supabase_public.table("test_access_grants")
            .select("test_id")
            .eq("test_id", test["id"])
            .eq("user_id", user_id)
            .execute()
        )
        return len(res.data) > 0 if res.data else False
    except Exception as e:
        logger.error(f"Error checking access for user {user_id}, test {test.get('id')}: {e}")
        return False
