"""
Thin data-access functions shared by user and admin blueprints.

Rule of thumb: if BOTH admin and user need to *read* the same data
shape (e.g. "list active streams"), it belongs here. Write operations
that only admin performs (create/edit/delete streams etc.) live in
app/admin/*_routes.py instead, using supabase_admin directly — no
need to funnel every admin write through this shared file.
"""
from app.extensions import supabase_public, supabase_admin


def get_active_streams():
    """Public catalog read — used on the stream-selection page."""
    res = (
        supabase_public.table("streams")
        .select("id, name, slug, description, icon_url")
        .eq("is_active", True)
        .order("display_order")
        .execute()
    )
    return res.data


def get_streams_for_user(user_id: str = None):
    """
    Returns the list of streams (id, name, slug) a logged-in user has
    already selected, by reading user_streams. Returns [] if no
    user_id is given, or if the user hasn't picked any stream yet.

    Used to decide whether to show the stream-selection page again
    or skip straight to the dashboard — see landing() and
    stream_select() in app/user/routes.py.

    NOTE: this used to also handle guest_id / guest_streams for
    anonymous "guest mode" visitors. Guest mode has been permanently
    removed from the app (every route now requires a real logged-in
    account), so that branch was deleted along with the guests /
    guest_streams tables. If you're reading old code or an old DB
    dump that still references guest_id here, that's expected — this
    function no longer accepts it.
    """
    if not user_id:
        return []

    res = (
        supabase_public.table("user_streams")
        .select("streams(id, name, slug)")
        .eq("user_id", user_id)
        .execute()
    )

    # Each row looks like {"streams": {"id": ..., "name": ..., "slug": ...}}
    # because of the FK-join select syntax above — unwrap it into a
    # flat list of stream dicts.
    return [row["streams"] for row in res.data if row.get("streams")]


def get_stream_by_slug(slug: str):
    res = (
        supabase_public.table("streams")
        .select("*")
        .eq("slug", slug)
        .eq("is_active", True)
        .single()
        .execute()
    )
    return res.data


def get_subjects_for_stream(stream_id: str):
    res = (
        supabase_public.table("subjects")
        .select("id, name, slug")
        .eq("stream_id", stream_id)
        .eq("is_active", True)
        .order("display_order")
        .execute()
    )
    return res.data


def get_chapters_for_subject(subject_id: str):
    res = (
        supabase_public.table("chapters")
        .select("id, name, slug")
        .eq("subject_id", subject_id)
        .eq("is_active", True)
        .order("display_order")
        .execute()
    )
    return res.data


def get_test_categories_for_stream(stream_id: str):
    res = (
        supabase_public.table("test_categories")
        .select("id, name, slug, is_premium")
        .eq("stream_id", stream_id)
        .order("display_order")
        .execute()
    )
    return res.data


def get_difficulty_levels():
    res = (
        supabase_public.table("difficulty_levels")
        .select("id, name")
        .order("display_order")
        .execute()
    )
    return res.data


# ---------- Added in Phase 3: chapter-wise practice + mock test feed ----------

def get_chapter_by_id(chapter_id):
    res = (
        supabase_public.table("chapters")
        .select("*, subjects(name, slug, streams(name, slug))")
        .eq("id", chapter_id).single().execute()
    )
    return res.data


def get_topics_for_chapter(chapter_id, is_pyq=False):
    res = (
        supabase_public.table("questions")
        .select("topic_name, is_premium")
        .eq("chapter_id", chapter_id)
        .eq("is_pyq", is_pyq)
        .execute()
    )
    topics = {}
    for row in res.data:
        name = row["topic_name"] or "General"
        entry = topics.setdefault(name, {"name": name, "is_premium": False})
        entry["is_premium"] = entry["is_premium"] or row["is_premium"]
    return list(topics.values())


def get_questions_for_practice(chapter_id, is_pyq=False, topic_name=None):
    q = supabase_public.table("questions").select("*").eq("chapter_id", chapter_id).eq("is_pyq", is_pyq)
    if topic_name:
        q = q.eq("topic_name", topic_name)
    return q.execute().data


def get_all_tests_for_stream(stream_id):
    res = (
        supabase_public.table("tests")
        .select("id, title, description, duration_minutes, total_marks, is_premium, price_inr")
        .eq("stream_id", stream_id)
        .eq("is_active", True)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data


def get_test_by_id(test_id):
    res = supabase_public.table("tests").select("*").eq("id", test_id).single().execute()
    return res.data


def get_test_syllabus(test_id):
    """
    Mock tests are mapped via mock_test_questions -> mock_questions.
    Reads topic_name (mock_questions has no chapter_id) grouped by subject.
    """
    res = (
        supabase_public.table("mock_test_questions")
        .select("mock_questions(topic_name, subjects(name))")
        .eq("test_id", test_id)
        .execute()
    )
    syllabus = {}
    for row in res.data:
        q = row.get("mock_questions")
        if not q:
            continue
        subj_name = q["subjects"]["name"] if q.get("subjects") else "General"
        topic = q.get("topic_name") or "General"
        syllabus.setdefault(subj_name, set()).add(topic)
    return {subj: sorted(topics) for subj, topics in syllabus.items()}


def get_mock_questions_for_test(test_id):
    """
    Fetches every mock_questions row mapped to this test via
    mock_test_questions, ordered by question_order, and groups them
    by subject name — ready for an NTA-style subject-tabbed attempt
    screen. Returns:
        {
          "Physics": [ {id, question_text, option_a.., ...}, ... ],
          "Chemistry": [...],
          "Biology": [...],
        }
    Each question dict includes "subject_name" and "question_order"
    for convenience, and never includes correct_option/explanation
    (those are stripped here — the attempt screen must not leak the
    answer key to the browser; scoring happens server-side in
    submit_test_attempt()).
    """
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


def get_mock_question_ids_for_test(test_id):
    """Lightweight helper: just the mock_question_id list for a test, for validating a submission."""
    res = (
        supabase_public.table("mock_test_questions")
        .select("mock_question_id")
        .eq("test_id", test_id)
        .execute()
    )
    return [row["mock_question_id"] for row in res.data]


def create_test_attempt(test_id, user_id=None):
    """
    Starts a new attempt row (started_at defaults to now() in the DB).
    user_id is required — guest attempts (guest_id) are no longer
    supported anywhere in the app, since guest mode was permanently
    removed.

    FEATURE: Auto-Overwrite (Storage Saver).
    Before creating a new attempt, this completely wipes out any existing 
    attempts (and via DB cascade, their answers) for THIS test by THIS user.
    Ensures only the absolute latest attempt exists in the DB.
    """
    from app.extensions import supabase_admin

    if not user_id:
        return None

    # 1. DELETE existing attempts for this test + user to save storage
    (
        supabase_admin.table("test_attempts")
        .delete()
        .eq("test_id", test_id)
        .eq("user_id", user_id)
        .execute()
    )

    # 2. INSERT the fresh new attempt
    payload = {"test_id": test_id, "user_id": user_id}

    res = supabase_admin.table("test_attempts").insert(payload).execute()
    if not res.data:
        return None
    return res.data[0]["id"]


def get_attempt_by_id(attempt_id):
    """
    Reads with the admin client, not supabase_public. test_attempts
    has RLS enabled but no SELECT policy defined (see schema.sql —
    the RLS section was left incomplete), so the public/anon client
    can insert nothing and read nothing on this table by default.
    Ownership is already enforced at the route level (test_id match +
    session user_id), so reading here via the admin client is
    safe and is what makes the freshly-created attempt visible at all.
    """
    from app.extensions import supabase_admin
    res = supabase_admin.table("test_attempts").select("*").eq("id", attempt_id).execute()
    if not res.data:
        return None
    return res.data[0]


# =====================================================================
# NEW FOUNDATION FUNCTIONS FOR ROBUST DATA SYNC & REVIEW MODE
# =====================================================================

def save_attempt_progress(attempt_id, mock_question_id, selected_option, status, time_taken_sec):
    """
    Durable per-question progress save. Upserts directly into attempt_answers
    bypassing Flask cookies entirely. This guarantees progress never drops due
    to size limits.
    """
    from app.extensions import supabase_admin

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


def bulk_save_attempt_progress(attempt_id, entries):
    """
    Foolproof bulk sync called right before grading. It batch-upserts the
    entire Alpine.js state so even if a student lost internet momentarily,
    all answers hit the DB in one shot.
    """
    from app.extensions import supabase_admin

    if not entries:
        return
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


def get_attempt_answers_map(attempt_id):
    """
    Reads back every attempt_answers row for this attempt. Used to hydrate
    Alpine state on page reload, and to pull exact time/options for Review Mode.
    """
    from app.extensions import supabase_admin

    res = (
        supabase_admin.table("attempt_answers")
        .select("mock_question_id, selected_option, is_correct, time_taken_sec, status")
        .eq("attempt_id", attempt_id)
        .execute()
    )
    return {row["mock_question_id"]: row for row in res.data if row.get("mock_question_id")}


def get_mock_questions_for_test_review(test_id, attempt_id):
    """
    Review-mode variant of get_mock_questions_for_test().
    Unlike the live attempt version, this DOES include correct_option safely,
    and merges the user's saved answers directly into the output for UI highlighting.
    """
    from app.extensions import supabase_admin

    # Use supabase_admin here too to bypass RLS blocks and fetch mapped data securely for review.
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


def submit_test_attempt(attempt_id, test_id, answers=None, time_by_question=None):
    """
    Scores and finalizes an attempt using the database as the absolute source of truth.
    """
    from app.extensions import supabase_admin
    import datetime

    # Kept in signature for backwards compatibility with route calls, but default to empty.
    answers = answers or {}
    time_by_question = time_by_question or {}

    test = supabase_admin.table("tests").select("negative_marking, total_marks").eq("id", test_id).single().execute().data
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

    # CRITICAL BUGFIX: We fetch the existing, durable map from the DB directly.
    progress_map = get_attempt_answers_map(attempt_id)

    total_questions = len(mapped)
    total_marks = int(test.get("total_marks") or total_questions)
    negative_marking = abs(float(test.get("negative_marking") or 0)) 
    
    # Calculate exactly how much each question is worth (e.g. 120 marks / 30 Qs = 4 marks per correct Q)
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

    # Dynamic Scoring logic properly applies Marks_per_Question
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


def get_attempts_for_user(user_id):
    """
    All of this user's attempts, most recent first — for the profile/dashboard page.
    FEATURE: Now pulls test_categories(name) so the frontend can group attempts by Series.
    """
    from app.extensions import supabase_admin

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


def delete_all_attempts_for_user(user_id):
    """
    Nuclear Option: Instantly wipes all test attempts and answers for a given user.
    Used by the "Clear All History" feature in the dashboard.
    """
    from app.extensions import supabase_admin
    
    res = supabase_admin.table("test_attempts").delete().eq("user_id", user_id).execute()
    return res.data


def get_attempt_time_breakdown(attempt_id, test_id):
    """
    Per-question and per-subject time spent, for the result page.
    """
    from app.extensions import supabase_admin

    order_res = (
        supabase_admin.table("mock_test_questions")
        .select("mock_question_id, question_order")
        .eq("test_id", test_id)
        .order("question_order")
        .execute()
    )
    order_by_qid = {row["mock_question_id"]: row.get("question_order", 0) for row in order_res.data}

    questions_res = (
        supabase_admin.table("mock_questions")
        .select("id, question_text, subjects(name)")
        .in_("id", list(order_by_qid.keys()))
        .execute()
    ) if order_by_qid else None
    
    question_lookup = {}
    if questions_res:
        for q in questions_res.data:
            subj_name = q["subjects"]["name"] if q.get("subjects") else "General"
            question_lookup[q["id"]] = {"question_text": q["question_text"], "subject_name": subj_name}

    answers_res = (
        supabase_admin.table("attempt_answers")
        .select("mock_question_id, selected_option, is_correct, time_taken_sec")
        .eq("attempt_id", attempt_id)
        .execute()
    )
    answers_by_qid = {row["mock_question_id"]: row for row in answers_res.data if row.get("mock_question_id")}

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


def user_has_access_to_test(test, user_id):
    if not test.get("is_premium"):
        return True
    if not user_id:
        return False
    res = (
        supabase_public.table("test_access_grants")
        .select("test_id")
        .eq("test_id", test["id"])
        .eq("user_id", user_id)
        .execute()
    )
    return len(res.data) > 0
