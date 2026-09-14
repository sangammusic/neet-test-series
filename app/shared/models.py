"""
Thin data-access functions shared by user and admin blueprints.

Rule of thumb: if BOTH admin and user need to *read* the same data
shape (e.g. "list active streams"), it belongs here. Write operations
that only admin performs (create/edit/delete streams etc.) live in
app/admin/*_routes.py instead, using supabase_admin directly — no
need to funnel every admin write through this shared file.
"""
from app.extensions import supabase_public


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


def get_streams_for_user_or_guest(user_id: str = None, guest_id: str = None):
    """
    Returns the list of streams (id, name, slug) a logged-in user or
    a guest has already selected, by reading user_streams /
    guest_streams. Returns [] if neither id is given, or if the
    person hasn't picked any stream yet.

    Pass exactly one of user_id / guest_id — this mirrors how
    stream_select()'s POST handler already branches on is_logged_in()
    in app/user/routes.py, just for reading instead of writing.

    Used to decide whether to show the stream-selection page again
    or skip straight to the dashboard — see landing() and
    stream_select() in app/user/routes.py.
    """
    if user_id:
        res = (
            supabase_public.table("user_streams")
            .select("streams(id, name, slug)")
            .eq("user_id", user_id)
            .execute()
        )
    elif guest_id:
        res = (
            supabase_public.table("guest_streams")
            .select("streams(id, name, slug)")
            .eq("guest_id", guest_id)
            .execute()
        )
    else:
        return []

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
    Mock tests are mapped via mock_test_questions -> mock_questions,
    not the old chapter-wise test_questions -> questions path. Reads
    topic_name (mock_questions has no chapter_id) grouped by subject.
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


def create_test_attempt(test_id, user_id=None, guest_id=None):
    """
    Starts a new attempt row (started_at defaults to now() in the DB).
    Exactly one of user_id/guest_id must be given — mirrors the
    attempt_owner_check constraint on test_attempts.
    Returns the new attempt's id, or None on failure.
    """
    from app.extensions import supabase_admin  # write op — use the admin client

    payload = {"test_id": test_id}
    if user_id:
        payload["user_id"] = user_id
    elif guest_id:
        payload["guest_id"] = guest_id
    else:
        return None

    res = supabase_admin.table("test_attempts").insert(payload).execute()
    if not res.data:
        return None
    return res.data[0]["id"]


def get_attempt_by_id(attempt_id):
    res = supabase_public.table("test_attempts").select("*").eq("id", attempt_id).single().execute()
    return res.data


def submit_test_attempt(attempt_id, test_id, answers, time_by_question=None):
    """
    Scores and finalizes an attempt.

    answers: dict of {mock_question_id: "A"|"B"|"C"|"D"} for every
    question the student selected an option for (unanswered
    questions simply aren't in this dict — they count as skipped).

    time_by_question: optional dict of {mock_question_id: seconds}
    — the per-question stopwatch total tracked client-side. A row is
    still written to attempt_answers for a SKIPPED question if time
    was spent on it (selected_option stays null, is_correct stays
    null), so "visited but didn't answer" time isn't lost — this is
    what powers the per-question/per-subject time breakdown on the
    result page.

    Looks up the real correct_option + is_premium-safe fields for
    every mapped question directly from mock_questions (server-side,
    so a tampered client payload can't self-report a fake score),
    applies the test's negative_marking, writes attempt_answers rows,
    and updates the test_attempts row with the final score/counts.

    Returns the summary dict that was written to test_attempts, or
    None if the test/attempt couldn't be loaded.
    """
    from app.extensions import supabase_admin

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

    negative_marking = float(test.get("negative_marking") or 0)
    total_questions = len(mapped)
    correct_count = 0
    wrong_count = 0
    skipped_count = 0
    answer_rows = []

    for row in mapped:
        qid = row["mock_question_id"]
        correct_option = (row.get("mock_questions") or {}).get("correct_option")
        selected = answers.get(qid)
        time_taken = int(time_by_question.get(qid) or 0)

        if not selected:
            skipped_count += 1
            # Still record time spent on a skipped-but-visited question,
            # so it shows up in the result page's time breakdown.
            if time_taken > 0:
                answer_rows.append({
                    "attempt_id": attempt_id,
                    "mock_question_id": qid,
                    "selected_option": None,
                    "is_correct": None,
                    "time_taken_sec": time_taken,
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
        })

    if answer_rows:
        supabase_admin.table("attempt_answers").insert(answer_rows).execute()

    score = correct_count - (wrong_count * negative_marking)

    summary = {
        "submitted_at": "now()",
        "score": score,
        "total_questions": total_questions,
        "correct_count": correct_count,
        "wrong_count": wrong_count,
        "skipped_count": skipped_count,
    }
    # Supabase python client doesn't accept the literal string "now()"
    # as a value — use an actual ISO timestamp instead.
    import datetime
    summary["submitted_at"] = datetime.datetime.utcnow().isoformat()

    supabase_admin.table("test_attempts").update(summary).eq("id", attempt_id).execute()
    summary["attempt_id"] = attempt_id
    return summary


def get_attempts_for_user(user_id):
    """All of this user's attempts, most recent first — for the profile/dashboard page."""
    res = (
        supabase_public.table("test_attempts")
        .select("id, test_id, started_at, submitted_at, score, total_questions, "
                "correct_count, wrong_count, skipped_count, tests(title, total_marks)")
        .eq("user_id", user_id)
        .order("started_at", desc=True)
        .execute()
    )
    return res.data


def get_attempt_time_breakdown(attempt_id, test_id):
    """
    Per-question and per-subject time spent, for the result page.

    Reads attempt_answers (joined through mock_questions -> subjects)
    for this attempt and returns:
        {
          "by_subject": {"Physics": 734, "Chemistry": 610, "Biology": 512},  # seconds
          "by_question": [
            {"question_order": 1, "subject_name": "Physics",
             "question_text": "...", "time_taken_sec": 45,
             "selected_option": "C", "is_correct": True},
            ...
          ],
        }
    question_order comes from mock_test_questions so the breakdown
    can be shown in the same order the student saw the questions in.
    Questions with no attempt_answers row at all (never visited) are
    included with time_taken_sec = 0 so every mapped question shows
    up in the breakdown, not just the ones the student touched.
    test_id is passed in by the caller (test_result() already has it
    from the URL) rather than looked up again here.
    """
    order_res = (
        supabase_public.table("mock_test_questions")
        .select("mock_question_id, question_order")
        .eq("test_id", test_id)
        .order("question_order")
        .execute()
    )
    order_by_qid = {row["mock_question_id"]: row.get("question_order", 0) for row in order_res.data}

    questions_res = (
        supabase_public.table("mock_questions")
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
        supabase_public.table("attempt_answers")
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
