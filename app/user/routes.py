import re
from flask import render_template, request, redirect, url_for, session, jsonify, abort, flash
from supabase import create_client

from app.user import user_bp
from app.extensions import supabase_admin, supabase_public, SUPABASE_URL, SUPABASE_ANON_KEY
from app.shared.models import (
    get_active_streams, get_streams_for_user, get_stream_by_slug,
    get_subjects_for_stream, get_chapters_for_subject, get_chapter_by_id,
    get_all_tests_for_stream, get_test_by_id, get_test_syllabus,
    user_has_access_to_test, get_mock_questions_for_test,
    get_mock_question_ids_for_test, create_test_attempt, get_attempt_by_id,
    submit_test_attempt, get_attempts_for_user, get_attempt_time_breakdown,
    save_attempt_progress, bulk_save_attempt_progress, get_attempt_answers_map,
    get_mock_questions_for_test_review, delete_all_attempts_for_user,
    PRACTICE_ATTEMPT_KINDS, get_practice_quiz_questions, get_latest_practice_attempt,
    get_practice_attempt_by_id, get_practice_answers_map, start_practice_attempt,
    save_practice_progress, save_practice_mistake_note, submit_practice_attempt,
    get_practice_review_items, get_practice_analyse_counts, select_reattempt_question_ids,
    get_mock_review_items, get_mock_analyse_counts, save_mock_mistake_note, start_mock_reattempt,
)
from app.shared.utils import is_logged_in, current_user_id


# ==========================================
# ENTRY / AUTH-GATED FLOW
#
# Guest mode has been permanently removed. Every route below that
# used to branch on "logged in vs guest_id cookie" now simply
# requires login -- an anonymous visitor is redirected straight to
# auth.login. There is no more "continue without an account" path.
# ==========================================

@user_bp.route("/")
def landing():
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    existing = get_streams_for_user(user_id=current_user_id())
    if existing:
        return redirect(url_for("user.stream_dashboard", slug=existing[0]["slug"]))
    return redirect(url_for("user.start_learning"))


@user_bp.route("/start")
def start_learning():
    """
    The page a logged-in user lands on right after login/registration:
    a simple welcome screen with a single 'Start Learning' button that
    leads into board/stream selection.
    """
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    existing = get_streams_for_user(user_id=current_user_id())
    if existing:
        return redirect(url_for("user.stream_dashboard", slug=existing[0]["slug"]))

    return render_template("start_learning.html")


@user_bp.route("/streams", methods=["GET", "POST"])
def stream_select():
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    streams = get_active_streams()
    user_id = current_user_id()

    if request.method == "GET":
        force = request.args.get("force") == "1"

        if not force:
            existing = get_streams_for_user(user_id=user_id)
            if existing:
                return redirect(url_for("user.stream_dashboard", slug=existing[0]["slug"]))

        return render_template("stream_select.html", streams=streams)

    selected_ids = request.form.getlist("stream_ids")
    if not selected_ids:
        return render_template("stream_select.html", streams=streams, error="Pick at least one stream.")

    rows = [{"user_id": user_id, "stream_id": sid} for sid in selected_ids]
    supabase_admin.table("user_streams").upsert(rows).execute()

    session["active_stream_ids"] = selected_ids
    first_stream = next((s for s in streams if s["id"] == selected_ids[0]), None)
    return redirect(url_for("user.stream_dashboard", slug=first_stream["slug"]))


@user_bp.route("/streams/<slug>")
def stream_dashboard(slug):
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    stream = get_stream_by_slug(slug)
    if not stream:
        return render_template("shared/404.html"), 404
    return render_template("dashboard.html", stream=stream)


@user_bp.route("/switch-stream")
def switch_stream():
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    return redirect(url_for("user.stream_select", force="1"))


@user_bp.route("/dashboard")
def my_dashboard():
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    existing = get_streams_for_user(user_id=current_user_id())
    if existing:
        return redirect(url_for("user.stream_dashboard", slug=existing[0]["slug"]))
    return redirect(url_for("user.stream_select"))


# ==========================================
# MENU 1: CHAPTER-WISE MCQ/PYQ PRACTICE (8-LEVEL SANGAM FLOW)
# ==========================================

@user_bp.route("/streams/<slug>/practice")
def practice_subjects(slug):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    subjects = get_subjects_for_stream(stream["id"])
    return render_template("practice_subjects.html", stream=stream, subjects=subjects)


@user_bp.route("/streams/<slug>/practice/subject/<subject_id>")
def practice_chapters(slug, subject_id):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    chapters = get_chapters_for_subject(subject_id)
    return render_template("practice_chapters.html", stream=stream, chapters=chapters)


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>")
def practice_chapter_detail(slug, chapter_id):
    """Level 4: Select MCQ or PYQ"""
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    return render_template("practice_chapter_detail.html", stream=stream, chapter=chapter)


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>")
def practice_category(slug, chapter_id, mode):
    """Level 5: Select Topic-wise or Random"""
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    return render_template("practice_category.html", stream=stream, chapter=chapter, mode=mode)


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>")
def practice_folders(slug, chapter_id, mode, category):
    """Level 6: Show Folders for the specific category"""
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    is_pyq = (mode == "pyq")
    
    rows = supabase_public.table("questions").select("folder_name").eq("chapter_id", chapter_id).eq("is_pyq", is_pyq).eq("category", category).execute().data
    folders = sorted(list(set([r["folder_name"] for r in rows if r.get("folder_name")])))
    
    return render_template("practice_folders.html", stream=stream, chapter=chapter, mode=mode, category=category, folders=folders)


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<folder>")
def practice_sets(slug, chapter_id, mode, category, folder):
    """Level 7: Show Quizzes inside the Folder"""
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    is_pyq = (mode == "pyq")
    
    rows = supabase_public.table("questions").select("set_name, is_premium").eq("chapter_id", chapter_id).eq("is_pyq", is_pyq).eq("category", category).eq("folder_name", folder).execute().data

    set_dict = {}
    for r in rows:
        sn = r["set_name"]
        if not sn: continue
        if sn not in set_dict:
            set_dict[sn] = {"name": sn, "count": 0, "is_premium": False}
        set_dict[sn]["count"] += 1
        if r.get("is_premium"):
            set_dict[sn]["is_premium"] = True

    sets = sorted(list(set_dict.values()), key=lambda x: x["name"])
    return render_template("practice_sets.html", stream=stream, chapter=chapter, mode=mode, category=category, folder=folder, sets=sets)


# ------------------------------------------------------------------
# PRACTICE: persistent attempts, Analyse and Reattempt
# ------------------------------------------------------------------
_PRACTICE_MODES = ("mcq", "pyq")

# Human-readable messages for start_practice_attempt() error codes.
_REATTEMPT_ERRORS = {
    "no_previous_attempt": "Finish the quiz once before reattempting only your wrong / marked questions.",
    "nothing_to_reattempt": "Nothing to reattempt in that category — great job!",
    "no_questions": "This quiz has no questions.",
    "bad_kind": "Unknown reattempt type.",
    "db_error": "Could not start the attempt. Please try again.",
    "not_logged_in": "Please log in first.",
}


def _practice_ctx(slug, chapter_id, mode):
    """Common lookups. Aborts 404 on anything that does not exist."""
    if mode not in _PRACTICE_MODES:
        abort(404)
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    if not stream or not chapter:
        abort(404)
    return stream, chapter


def _own_practice_attempt_or_404(attempt_id, chapter_id, mode, category, folder, set_name):
    """Loads an attempt and makes sure it belongs to THIS user and THIS quiz."""
    attempt = get_practice_attempt_by_id(attempt_id)
    if (
        not attempt
        or attempt.get("user_id") != current_user_id()
        or attempt["chapter_id"] != chapter_id
        or attempt["mode"] != mode
        or attempt["category"] != category
        or attempt["folder_name"] != folder
        or attempt["set_name"] != set_name
    ):
        abort(404)
    return attempt


def _replay_ids_for_attempt(attempt, all_ids):
    """Which questions an existing attempt (re)plays -- derived from its kind + carried rows."""
    answers = get_practice_answers_map(attempt["id"])
    carried = {qid for qid, a in answers.items() if a.get("was_replayed") is False}
    return [qid for qid in all_ids if qid not in carried]


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<folder>/<set_name>")
def practice_overview(slug, chapter_id, mode, category, folder, set_name):
    """Level 8: quiz landing page. Shows Initialize / Resume / Analyse+Reattempt by attempt state."""
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    stream, chapter = _practice_ctx(slug, chapter_id, mode)
    is_pyq = (mode == "pyq")

    questions = supabase_public.table("questions").select("id, is_premium, marks").eq("chapter_id", chapter_id).eq("is_pyq", is_pyq).eq("category", category).eq("folder_name", folder).eq("set_name", set_name).execute().data

    if not questions:
        abort(404)

    is_premium = any(q.get("is_premium") for q in questions)
    total_questions = len(questions)
    total_marks = sum((q.get("marks") if q.get("marks") is not None else 4) for q in questions)

    attempt = get_latest_practice_attempt(current_user_id(), chapter_id, mode, category, folder, set_name)
    if attempt is None:
        attempt_state = "none"
    elif attempt.get("submitted_at"):
        attempt_state = "submitted"
    else:
        attempt_state = "in_progress"

    return render_template(
        "practice_overview.html", stream=stream, chapter=chapter, mode=mode, category=category,
        folder=folder, set_name=set_name, total_questions=total_questions, total_marks=total_marks,
        is_premium=is_premium, attempt=attempt, attempt_state=attempt_state,
    )


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<folder>/<set_name>/start", methods=["POST"])
def practice_start(slug, chapter_id, mode, category, folder, set_name):
    """Creates the new latest attempt (kind = full | wrong_only | marked_and_wrong_only) and opens the runner."""
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    _practice_ctx(slug, chapter_id, mode)

    kind = (request.form.get("kind") or "full").strip()
    overview = url_for("user.practice_overview", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name)

    attempt_id, result = start_practice_attempt(current_user_id(), chapter_id, mode, category, folder, set_name, kind)
    if not attempt_id:
        flash(_REATTEMPT_ERRORS.get(result, "Could not start the attempt."), "error")
        return redirect(overview)
    return redirect(url_for("user.practice_run", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name, attempt_id=attempt_id))


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<folder>/<set_name>/run")
def practice_run(slug, chapter_id, mode, category, folder, set_name):
    """Level 9: the runner. Requires a persisted attempt (?attempt_id=...); resumes saved progress."""
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    stream, chapter = _practice_ctx(slug, chapter_id, mode)
    overview = url_for("user.practice_overview", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name)

    attempt_id = request.args.get("attempt_id")
    if not attempt_id:
        # Old bookmarks / links without an attempt: start (or resume) properly instead of a stateless run.
        latest = get_latest_practice_attempt(current_user_id(), chapter_id, mode, category, folder, set_name)
        if latest and not latest.get("submitted_at"):
            return redirect(url_for("user.practice_run", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name, attempt_id=latest["id"]))
        return redirect(overview)

    attempt = _own_practice_attempt_or_404(attempt_id, chapter_id, mode, category, folder, set_name)
    if attempt.get("submitted_at"):
        return redirect(url_for("user.practice_analyse", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name))

    all_questions = get_practice_quiz_questions(chapter_id, mode, category, folder, set_name)
    if not all_questions:
        abort(404)
    replay_ids = _replay_ids_for_attempt(attempt, [q["id"] for q in all_questions])
    replay_set = set(replay_ids)
    questions = [q for q in all_questions if q["id"] in replay_set]
    if not questions:
        return redirect(overview)

    saved = get_practice_answers_map(attempt["id"])
    saved_for_runner = {
        qid: {
            "selected_option": a.get("selected_option"),
            "status": a.get("status"),
            "time_taken_sec": a.get("time_taken_sec") or 0,
            "mistake_note": a.get("mistake_note") or "",
        }
        for qid, a in saved.items() if qid in replay_set
    }

    return render_template(
        "practice_runner.html", stream=stream, chapter=chapter, mode=mode, category=category, folder=folder,
        set_name=set_name, questions=questions, attempt=attempt, saved_progress=saved_for_runner,
        attempt_kind=attempt.get("attempt_kind") or "full",
    )


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<folder>/<set_name>/attempt/<attempt_id>/answer", methods=["POST"])
def practice_save_answer(slug, chapter_id, mode, category, folder, set_name, attempt_id):
    """Durable per-question save from the runner (answer / mark toggle / time)."""
    if not is_logged_in():
        return jsonify({"ok": False, "error": "not logged in"}), 401
    attempt = get_practice_attempt_by_id(attempt_id)
    if (
        not attempt or attempt.get("user_id") != current_user_id() or attempt.get("submitted_at")
        or (attempt["chapter_id"], attempt["mode"], attempt["category"], attempt["folder_name"], attempt["set_name"])
        != (chapter_id, mode, category, folder, set_name)
    ):
        return jsonify({"ok": False, "error": "attempt not active"}), 400

    payload = request.get_json(silent=True) or {}
    question_id = payload.get("question_id")
    if not question_id:
        return jsonify({"ok": False, "error": "question_id required"}), 400

    # Only questions that belong to this quiz AND are being replayed in this attempt.
    all_ids = [q["id"] for q in get_practice_quiz_questions(chapter_id, mode, category, folder, set_name, columns="id")]
    if question_id not in set(_replay_ids_for_attempt(attempt, all_ids)):
        return jsonify({"ok": False, "error": "question not part of this attempt"}), 400

    ok = save_practice_progress(attempt_id, question_id, payload.get("selected_option"), payload.get("status"), payload.get("time_taken_sec"))
    return jsonify({"ok": bool(ok)})


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<folder>/<set_name>/attempt/<attempt_id>/note", methods=["POST"])
def practice_save_note(slug, chapter_id, mode, category, folder, set_name, attempt_id):
    """Saves the 'what mistake did I make' note for one question (works during the run and on Analyse)."""
    if not is_logged_in():
        return jsonify({"ok": False, "error": "not logged in"}), 401
    attempt = get_practice_attempt_by_id(attempt_id)
    if (
        not attempt or attempt.get("user_id") != current_user_id()
        or (attempt["chapter_id"], attempt["mode"], attempt["category"], attempt["folder_name"], attempt["set_name"])
        != (chapter_id, mode, category, folder, set_name)
    ):
        return jsonify({"ok": False, "error": "attempt not found"}), 404

    payload = request.get_json(silent=True) or {}
    question_id = payload.get("question_id")
    note = payload.get("note", "")
    if not question_id or not isinstance(note, str):
        return jsonify({"ok": False, "error": "question_id and note required"}), 400

    all_ids = {q["id"] for q in get_practice_quiz_questions(chapter_id, mode, category, folder, set_name, columns="id")}
    if question_id not in all_ids:
        return jsonify({"ok": False, "error": "question not part of this quiz"}), 400

    ok = save_practice_mistake_note(attempt_id, question_id, note)
    return jsonify({"ok": bool(ok)})


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<folder>/<set_name>/attempt/<attempt_id>/submit", methods=["POST"])
def practice_submit(slug, chapter_id, mode, category, folder, set_name, attempt_id):
    """Grades on the server, stores the summary, returns where to go next."""
    if not is_logged_in():
        return jsonify({"ok": False, "error": "not logged in"}), 401
    attempt = get_practice_attempt_by_id(attempt_id)
    if (
        not attempt or attempt.get("user_id") != current_user_id()
        or (attempt["chapter_id"], attempt["mode"], attempt["category"], attempt["folder_name"], attempt["set_name"])
        != (chapter_id, mode, category, folder, set_name)
    ):
        return jsonify({"ok": False, "error": "attempt not found"}), 404

    analyse_url = url_for("user.practice_analyse", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name)
    if attempt.get("submitted_at"):
        return jsonify({"ok": True, "redirect": analyse_url, "already_submitted": True})

    all_ids = [q["id"] for q in get_practice_quiz_questions(chapter_id, mode, category, folder, set_name, columns="id")]
    replay_ids = _replay_ids_for_attempt(attempt, all_ids)

    payload = request.get_json(silent=True) or {}
    summary = submit_practice_attempt(
        attempt_id, replay_ids,
        submitted_answers=payload.get("answers") or {},
        total_time_sec=payload.get("total_time_sec") or 0,
    )
    if not summary:
        return jsonify({"ok": False, "error": "Could not submit. Please try again."}), 500
    return jsonify({"ok": True, "redirect": analyse_url, "summary": summary})


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<folder>/<set_name>/analyse")
def practice_analyse(slug, chapter_id, mode, category, folder, set_name):
    """'Analyse your last quiz': chooser (wrong / marked / full) + reattempt options."""
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream, chapter = _practice_ctx(slug, chapter_id, mode)

    attempt = get_latest_practice_attempt(current_user_id(), chapter_id, mode, category, folder, set_name)
    overview = url_for("user.practice_overview", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name)
    if not attempt:
        return redirect(overview)
    if not attempt.get("submitted_at"):
        return redirect(url_for("user.practice_run", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name, attempt_id=attempt["id"]))

    counts = get_practice_analyse_counts(attempt["id"])
    return render_template(
        "practice_analyse.html", stream=stream, chapter=chapter, mode=mode, category=category, folder=folder,
        set_name=set_name, attempt=attempt, counts=counts,
    )


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<folder>/<set_name>/analyse/<view>")
def practice_analyse_view(slug, chapter_id, mode, category, folder, set_name, view):
    """Review list: view = wrong | marked | full. Each question has an editable mistake note."""
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    if view not in ("wrong", "marked", "full"):
        abort(404)
    stream, chapter = _practice_ctx(slug, chapter_id, mode)

    attempt = get_latest_practice_attempt(current_user_id(), chapter_id, mode, category, folder, set_name)
    if not attempt or not attempt.get("submitted_at"):
        return redirect(url_for("user.practice_overview", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name))

    items = get_practice_review_items(attempt["id"], view)
    return render_template(
        "practice_review.html", stream=stream, chapter=chapter, mode=mode, category=category, folder=folder,
        set_name=set_name, attempt=attempt, items=items, view=view,
    )


# ==========================================
# MENU 2: MOCK TEST SERIES (PHASE 4: STRICT ISOLATION)
# ==========================================

@user_bp.route("/streams/<slug>/tests")
def tests_feed(slug):
    """
    PHASE 4: Shows Folders (Test Series) instead of flat bikhre hue tests.
    """
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    if not stream:
        abort(404)
    # Fetch all VIP Folders created by admin
    series_list = (
        supabase_public.table("mock_test_series")
        .select("*")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    return render_template("tests_feed.html", stream=stream, series_list=series_list)


@user_bp.route("/streams/<slug>/series/<series_id>/tests")
def series_tests(slug, series_id):
    """
    PHASE 4: Strict Folder Hierarchy. 
    Only shows tests belonging to this specific Folder AND this Student's Stream.
    Zero Test Leaking.
    """
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    if not stream:
        abort(404)
    # BUGFIX: .single() throws a hard 500 the moment series_id doesn't
    # match any row (deleted/bad link). .maybe_single() makes "no row"
    # a normal None instead of a crash, matching the pattern used
    # everywhere else in this app (see shared/models.py SANGAM FIXes).
    series = (
        supabase_public.table("mock_test_series")
        .select("*")
        .eq("id", series_id)
        .maybe_single()
        .execute()
        .data
    )
    if not series:
        abort(404)

    # Strict isolation: series_id + stream_id
    tests = (
        supabase_public.table("tests")
        .select("*")
        .eq("series_id", series_id)
        .eq("stream_id", stream["id"])
        .order("created_at", desc=True)
        .execute()
        .data
    )
    return render_template("user_series_tests.html", stream=stream, series=series, tests=tests)


@user_bp.route("/streams/<slug>/tests/<test_id>")
def test_overview(slug, test_id):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    test = get_test_by_id(test_id)
    if not test:
        abort(404)
    syllabus = get_test_syllabus(test_id)
    has_access = user_has_access_to_test(test, current_user_id())

    attempt = _latest_mock_attempt(test_id) if has_access else None
    if attempt is None:
        attempt_state = "none"
    elif attempt.get("submitted_at"):
        attempt_state = "submitted"
    else:
        attempt_state = "in_progress"

    return render_template(
        "test_overview.html", stream=stream, test=test, syllabus=syllabus, has_access=has_access,
        attempt=attempt, attempt_state=attempt_state,
    )


@user_bp.route("/streams/<slug>/tests/<test_id>/start", methods=["POST"])
def test_start(slug, test_id):
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    test = get_test_by_id(test_id)
    if not test:
        abort(404)

    if not user_has_access_to_test(test, current_user_id()):
        return redirect(url_for("user.test_overview", slug=slug, test_id=test_id))

    kind = (request.form.get("kind") or "full").strip()
    if kind == "full":
        # Unchanged legacy path: replaces the previous attempt with a clean one.
        attempt_id = create_test_attempt(test_id, user_id=current_user_id())
        result = None
    else:
        attempt_id, result = start_mock_reattempt(test_id, current_user_id(), kind)

    if not attempt_id:
        if result:
            flash(_REATTEMPT_ERRORS.get(result, "Could not start the attempt."), "error")
            return redirect(url_for("user.test_analyse", slug=slug, test_id=test_id))
        return redirect(url_for("user.test_overview", slug=slug, test_id=test_id))

    return redirect(url_for("user.test_attempt", slug=slug, test_id=test_id, attempt_id=attempt_id))


@user_bp.route("/streams/<slug>/tests/<test_id>/attempt/<attempt_id>")
def test_attempt(slug, test_id, attempt_id):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    attempt = get_attempt_by_id(attempt_id)
    if not attempt or attempt["test_id"] != test_id or attempt.get("user_id") != current_user_id():
        abort(404)
    if attempt.get("submitted_at"):
        return redirect(url_for("user.test_result", slug=slug, test_id=test_id, attempt_id=attempt_id))

    stream = get_stream_by_slug(slug)
    test = get_test_by_id(test_id)
    questions_by_subject = get_mock_questions_for_test(test_id)
    saved_progress = get_attempt_answers_map(attempt_id)

    # Partial reattempt: the runner only shows the questions being replayed.
    # Carried-forward rows (was_replayed = false) keep their old result.
    replay_ids = _mock_replay_ids(attempt)
    if replay_ids is not None:
        keep = set(replay_ids)
        questions_by_subject = {
            subj: [q for q in qs if q["id"] in keep] for subj, qs in questions_by_subject.items()
        }
        questions_by_subject = {subj: qs for subj, qs in questions_by_subject.items() if qs}
        saved_progress = {qid: v for qid, v in saved_progress.items() if qid in keep}

    # Time allowed: the full test duration for a normal run; for a partial reattempt it is
    # scaled to the share of questions being replayed (never less than 1 minute).
    full_seconds = int((test.get("duration_minutes") or 60) * 60)
    if replay_ids is not None:
        total_q = max(1, len(get_mock_question_ids_for_test(test_id) or []))
        duration_seconds = max(60, round(full_seconds * len(replay_ids) / total_q))
    else:
        duration_seconds = full_seconds

    return render_template(
        "test_attempt.html",
        stream=stream,
        test=test,
        attempt=attempt,
        questions_by_subject=questions_by_subject,
        saved_progress=saved_progress,
        duration_seconds=duration_seconds,
    )


@user_bp.route("/streams/<slug>/tests/<test_id>/attempt/<attempt_id>/answer", methods=["POST"])
def test_attempt_save_answer(slug, test_id, attempt_id):
    if not is_logged_in():
        return jsonify({"ok": False, "error": "not logged in"}), 401
    attempt = get_attempt_by_id(attempt_id)
    if (
        not attempt
        or attempt["test_id"] != test_id
        or attempt.get("user_id") != current_user_id()
        or attempt.get("submitted_at")
    ):
        return jsonify({"ok": False, "error": "attempt not active"}), 400

    payload = request.get_json(silent=True) or {}
    question_id = payload.get("question_id")
    selected_option = payload.get("selected_option")
    status = payload.get("status")
    time_taken_sec = payload.get("time_taken_sec")

    if not question_id:
        return jsonify({"ok": False, "error": "question_id required"}), 400

    replay_ids = _mock_replay_ids(attempt)
    if replay_ids is not None and question_id not in set(replay_ids):
        return jsonify({"ok": False, "error": "question not part of this reattempt"}), 400

    try:
        time_taken_sec = int(time_taken_sec) if time_taken_sec is not None else 0
    except (TypeError, ValueError):
        time_taken_sec = 0

    save_attempt_progress(attempt_id, question_id, selected_option, status, time_taken_sec)
    return jsonify({"ok": True})


@user_bp.route("/streams/<slug>/tests/<test_id>/attempt/<attempt_id>/submit", methods=["POST"])
def test_attempt_submit(slug, test_id, attempt_id):
    if not is_logged_in():
        return jsonify({"ok": False, "error": "not logged in"}), 401
    attempt = get_attempt_by_id(attempt_id)
    if not attempt or attempt["test_id"] != test_id or attempt.get("user_id") != current_user_id():
        abort(404)

    if attempt.get("submitted_at"):
        return jsonify({
            "ok": True,
            "redirect": url_for("user.test_result", slug=slug, test_id=test_id, attempt_id=attempt_id)
        })

    all_valid_ids = set(get_mock_question_ids_for_test(test_id))
    replay_ids = _mock_replay_ids(attempt)
    # Only replayed questions may be (re)written by the browser; carried-forward
    # answers keep their stored values. Scoring below still covers the WHOLE test.
    writable_ids = set(replay_ids) if replay_ids is not None else all_valid_ids
    valid_ids = all_valid_ids

    payload = request.get_json(silent=True) or {}
    final_answers = payload.get("answers") or {}
    
    bulk_entries = [
        {
            "mock_question_id": qid,
            "selected_option": entry.get("selected_option"),
            "status": entry.get("status"),
            "time_taken_sec": entry.get("time_taken_sec"),
        }
        for qid, entry in final_answers.items()
        if qid in writable_ids
    ]
    bulk_save_attempt_progress(attempt_id, bulk_entries)

    # PERF FIX: read the just-saved progress ONCE here and hand the raw
    # map straight to submit_test_attempt(), instead of the old flow
    # (read here -> derive answers/time_by_question -> submit_test_attempt
    # reads attempt_answers AGAIN internally via get_attempt_answers_map).
    # That was two full round-trips to Supabase for the same rows on
    # every single submission -- exactly the kind of extra network hop
    # that turns into 502s when hundreds of students submit at once.
    progress = get_attempt_answers_map(attempt_id)
    submit_test_attempt(attempt_id, test_id, progress_map=progress, valid_ids=valid_ids)

    return jsonify({
        "ok": True,
        "redirect": url_for("user.test_result", slug=slug, test_id=test_id, attempt_id=attempt_id),
    })


@user_bp.route("/streams/<slug>/tests/<test_id>/attempt/<attempt_id>/result")
def test_result(slug, test_id, attempt_id):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    attempt = get_attempt_by_id(attempt_id)
    if not attempt or attempt["test_id"] != test_id or attempt.get("user_id") != current_user_id():
        abort(404)
    if not attempt.get("submitted_at"):
        return redirect(url_for("user.test_attempt", slug=slug, test_id=test_id, attempt_id=attempt_id))

    stream = get_stream_by_slug(slug)
    test = get_test_by_id(test_id)
    time_breakdown = get_attempt_time_breakdown(attempt_id, test_id)
    return render_template(
        "test_result.html",
        stream=stream, test=test, attempt=attempt,
        time_by_subject=time_breakdown["by_subject"],
        time_by_question=time_breakdown["by_question"],
    )


@user_bp.route("/streams/<slug>/tests/<test_id>/attempt/<attempt_id>/review")
def test_attempt_review(slug, test_id, attempt_id):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    attempt = get_attempt_by_id(attempt_id)
    if not attempt or attempt["test_id"] != test_id or attempt.get("user_id") != current_user_id():
        abort(404)
    if not attempt.get("submitted_at"):
        return redirect(url_for("user.test_attempt", slug=slug, test_id=test_id, attempt_id=attempt_id))

    stream = get_stream_by_slug(slug)
    test = get_test_by_id(test_id)
    questions_by_subject = get_mock_questions_for_test_review(test_id, attempt_id)

    return render_template(
        "test_attempt.html",
        stream=stream,
        test=test,
        attempt=attempt,
        questions_by_subject=questions_by_subject,
        saved_progress={},
        is_review_mode=True,
    )


def _mock_replay_ids(attempt):
    """
    None  -> the attempt is a normal full run (all questions are live).
    list  -> the attempt is a partial reattempt; only these question ids are live
             (every question that was NOT carried forward from the previous attempt).
    """
    if (attempt.get("attempt_kind") or "full") == "full":
        return None
    all_ids = list(get_mock_question_ids_for_test(attempt["test_id"]) or [])
    rows = get_attempt_answers_map(attempt["id"])
    carried = {qid for qid, row in rows.items() if row.get("was_replayed") is False}
    return [qid for qid in all_ids if qid not in carried]


@user_bp.route("/streams/<slug>/tests/<test_id>/analyse")
def test_analyse(slug, test_id):
    """Mock 'Analyse your last test' chooser + reattempt options (mirrors practice_analyse)."""
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    test = get_test_by_id(test_id)
    if not stream or not test:
        abort(404)

    attempt = _latest_mock_attempt(test_id)
    if not attempt:
        return redirect(url_for("user.test_overview", slug=slug, test_id=test_id))
    if not attempt.get("submitted_at"):
        return redirect(url_for("user.test_attempt", slug=slug, test_id=test_id, attempt_id=attempt["id"]))

    counts = get_mock_analyse_counts(attempt["id"], test_id)
    return render_template("test_analyse.html", stream=stream, test=test, attempt=attempt, counts=counts)


@user_bp.route("/streams/<slug>/tests/<test_id>/analyse/<view>")
def test_analyse_view(slug, test_id, view):
    """Mock review list (wrong | marked | full) with editable mistake notes, grouped by subject."""
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    if view not in ("wrong", "marked", "full"):
        abort(404)
    stream = get_stream_by_slug(slug)
    test = get_test_by_id(test_id)
    if not stream or not test:
        abort(404)

    attempt = _latest_mock_attempt(test_id)
    if not attempt or not attempt.get("submitted_at"):
        return redirect(url_for("user.test_overview", slug=slug, test_id=test_id))

    items = get_mock_review_items(attempt["id"], test_id, view)
    return render_template("test_review.html", stream=stream, test=test, attempt=attempt, items=items, view=view)


@user_bp.route("/streams/<slug>/tests/<test_id>/attempt/<attempt_id>/note", methods=["POST"])
def test_save_note(slug, test_id, attempt_id):
    """Saves the mistake note for one mock question of the user's own attempt."""
    if not is_logged_in():
        return jsonify({"ok": False, "error": "not logged in"}), 401
    attempt = get_attempt_by_id(attempt_id)
    if not attempt or attempt["test_id"] != test_id or attempt.get("user_id") != current_user_id():
        return jsonify({"ok": False, "error": "attempt not found"}), 404

    payload = request.get_json(silent=True) or {}
    question_id = payload.get("question_id")
    note = payload.get("note", "")
    if not question_id or not isinstance(note, str):
        return jsonify({"ok": False, "error": "question_id and note required"}), 400
    if question_id not in set(get_mock_question_ids_for_test(test_id) or []):
        return jsonify({"ok": False, "error": "question not part of this test"}), 400

    return jsonify({"ok": bool(save_mock_mistake_note(attempt_id, question_id, note))})


def _latest_mock_attempt(test_id):
    """The user's single stored attempt for this test (or None)."""
    try:
        rows = (
            supabase_admin.table("test_attempts").select("*")
            .eq("test_id", test_id).eq("user_id", current_user_id()).limit(1).execute().data
        )
        return rows[0] if rows else None
    except Exception:
        return None


# ---------- Profile & Account Settings ----------

@user_bp.route("/profile")
def profile():
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    attempts = get_attempts_for_user(current_user_id())
    return render_template("profile.html", attempts=attempts)


@user_bp.route("/profile/clear-history", methods=["POST"])
def clear_history():
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    
    delete_all_attempts_for_user(current_user_id())
    
    flash("All your test history and attempts have been permanently deleted.", "success")
    return redirect(url_for("user.profile"))


USERNAME_MIN_LEN = 3
USERNAME_MAX_LEN = 20
USERNAME_TAKEN_MESSAGE = "That username is already taken. Please choose another one."


def _validate_username_format(username):
    """
    Returns an error message string if the username is invalid, else None.
    Mirrors app.auth.routes.is_valid_username but with length bounds and
    user-facing messages, since this is surfaced directly in the settings UI.
    """
    if not username:
        return "Username cannot be empty."
    if len(username) < USERNAME_MIN_LEN:
        return f"Username must be at least {USERNAME_MIN_LEN} characters."
    if len(username) > USERNAME_MAX_LEN:
        return f"Username must be at most {USERNAME_MAX_LEN} characters."
    if not re.match(r"^[a-zA-Z0-9_]+$", username):
        return "Only letters, numbers, and underscores are allowed."
    return None


def _username_taken(username, exclude_user_id=None):
    """
    Checks whether `username` is already in use by someone else. This is a
    pre-check for a fast, friendly UI response — the database UNIQUE
    constraint (via the dummy-email trick, see app/auth/routes.py) remains
    the real source of truth and is what actually prevents a race condition
    where two people grab the same username in the same instant.
    """
    query = supabase_admin.table("profiles").select("id").eq("username", username)
    rows = query.execute().data or []
    if exclude_user_id:
        rows = [r for r in rows if r["id"] != exclude_user_id]
    return len(rows) > 0


@user_bp.route("/settings", methods=["GET"])
def settings():
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    user_id = current_user_id()
    profile_res = supabase_admin.table("profiles").select("username, full_name").eq("id", user_id).execute()
    profile = profile_res.data[0] if profile_res.data else None
    return render_template("settings.html", profile=profile)


@user_bp.route("/settings/check-username", methods=["GET"])
def check_username_availability():
    """
    AJAX endpoint backing the live availability check in the settings UI.
    Always returns JSON, never redirects — this is called from JS, not a form.
    """
    if not is_logged_in():
        return jsonify({"ok": False, "error": "Not logged in."}), 401

    candidate = (request.args.get("username") or "").strip().lower()
    user_id = current_user_id()

    format_error = _validate_username_format(candidate)
    if format_error:
        return jsonify({"ok": True, "available": False, "reason": format_error})

    profile_res = supabase_admin.table("profiles").select("username").eq("id", user_id).execute()
    current_username = (profile_res.data[0].get("username") if profile_res.data else None) or ""

    if candidate == current_username.lower():
        return jsonify({"ok": True, "available": False, "reason": "This is already your current username."})

    if _username_taken(candidate, exclude_user_id=user_id):
        return jsonify({"ok": True, "available": False, "reason": USERNAME_TAKEN_MESSAGE})

    return jsonify({"ok": True, "available": True, "reason": "Username is available."})


@user_bp.route("/settings/username", methods=["POST"])
def change_username():
    """
    Changes the logged-in user's username. Returns JSON so the settings
    page can show an inline success/error state without a full reload.
    """
    if not is_logged_in():
        return jsonify({"ok": False, "error": "Not logged in."}), 401

    user_id = current_user_id()
    new_username = (request.form.get("username") or "").strip().lower()

    format_error = _validate_username_format(new_username)
    if format_error:
        return jsonify({"ok": False, "error": format_error}), 400

    profile_res = supabase_admin.table("profiles").select("username").eq("id", user_id).execute()
    current_username = (profile_res.data[0].get("username") if profile_res.data else None) or ""

    if new_username == current_username.lower():
        return jsonify({"ok": False, "error": "That's already your current username."}), 400

    # Fast pre-check for a friendly error message. The final guarantee of
    # uniqueness is the UNIQUE constraint on profiles.username / the dummy
    # email's uniqueness in Supabase Auth (see except block below) — this
    # pre-check just avoids making the user wait for that round trip in the
    # common case.
    if _username_taken(new_username, exclude_user_id=user_id):
        return jsonify({"ok": False, "error": USERNAME_TAKEN_MESSAGE}), 409

    dummy_email = f"{new_username}@sangam.local"

    try:
        # Change the Auth-side identity first. If this fails (e.g. a
        # concurrent request just took this username between our
        # pre-check and now), nothing has been written to profiles yet.
        supabase_admin.auth.admin.update_user_by_id(user_id, {"email": dummy_email})
    except Exception as exc:
        error_msg = str(exc).lower()
        if "already" in error_msg or "unique" in error_msg or "exists" in error_msg:
            return jsonify({"ok": False, "error": USERNAME_TAKEN_MESSAGE}), 409
        return jsonify({"ok": False, "error": "Something went wrong while updating your username. Please try again."}), 500

    try:
        supabase_admin.table("profiles").update({"username": new_username}).eq("id", user_id).execute()
    except Exception:
        # Auth side succeeded but the profiles row failed to update (should
        # be rare — same UNIQUE constraint applies there). Roll the Auth
        # email back so the two stores don't drift out of sync.
        try:
            supabase_admin.auth.admin.update_user_by_id(user_id, {"email": f"{current_username}@sangam.local"})
        except Exception:
            pass
        return jsonify({"ok": False, "error": USERNAME_TAKEN_MESSAGE}), 409

    return jsonify({"ok": True, "username": new_username, "message": "Username updated successfully."})


def _verify_current_password(user_id, current_password):
    """
    Returns True if `current_password` is correct for the given user,
    False otherwise. Always verifies via a real sign-in attempt against
    a brand-new, isolated Supabase client — see change_password's
    original comment for why: the shared `supabase_public` client's
    internal session state is mutated by sign_in_with_password, and
    since that client is a single global object shared by every
    concurrent request, two sign-ins happening at the same time (e.g.
    another user logging in right as this check runs) could clobber
    each other and make a correct password look wrong. A fresh,
    throwaway client has its own isolated session and can't be affected
    by, or affect, any other request.

    BUGFIX: this used to take a `username` argument and build the
    dummy email as f"{username}@sangam.local" from it, where the
    caller read `username` out of profiles.username. For any account
    created BEFORE the username-column migration was applied,
    profiles.username can be NULL/empty (the migration adds the
    column but doesn't backfill old rows), which silently produced
    the dummy email "@sangam.local" — always wrong, so the real
    password always looked "incorrect" even when typed correctly.
    Fixed by reading the actual login email straight from
    auth.users via the admin API instead of reconstructing it from a
    column that can be stale or empty — auth.users.email is the true
    source of truth for what sign_in_with_password needs.
    """
    try:
        user_res = supabase_admin.auth.admin.get_user_by_id(user_id)
        real_email = user_res.user.email if user_res and user_res.user else None
        if not real_email:
            return False

        verify_client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
        verify_result = verify_client.auth.sign_in_with_password(
            {"email": real_email, "password": current_password}
        )
        return bool(verify_result and verify_result.session)
    except Exception:
        return False


@user_bp.route("/settings/verify-password", methods=["POST"])
def verify_password():
    """
    Checks the current password WITHOUT changing anything. Backs the
    "unlock" step in the settings UI: the New Password field only
    appears after this returns ok=True, so a user can never even see
    the new-password form without first proving they know the current
    one.
    """
    if not is_logged_in():
        return jsonify({"ok": False, "error": "Not logged in."}), 401

    user_id = current_user_id()
    current_password = request.form.get("current_password", "")

    if not current_password:
        return jsonify({"ok": False, "error": "Please enter your current password."}), 400

    if not _verify_current_password(user_id, current_password):
        return jsonify({"ok": False, "error": "Incorrect password. Please try again."}), 401

    return jsonify({"ok": True})


@user_bp.route("/settings/password", methods=["POST"])
def change_password():
    """
    Changes the logged-in user's password. Requires the current password
    (re-verified server-side via a real sign-in attempt, never trusted from
    the client) plus a new password entered twice for confirmation.
    """
    if not is_logged_in():
        return jsonify({"ok": False, "error": "Not logged in."}), 401

    user_id = current_user_id()
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not current_password:
        return jsonify({"ok": False, "error": "Please enter your current password."}), 400

    if not new_password or len(new_password) < 6:
        return jsonify({"ok": False, "error": "New password must be at least 6 characters."}), 400

    if new_password != confirm_password:
        return jsonify({"ok": False, "error": "New password and confirmation do not match."}), 400

    if new_password == current_password:
        return jsonify({"ok": False, "error": "New password must be different from your current password."}), 400

    if not _verify_current_password(user_id, current_password):
        return jsonify({"ok": False, "error": "Your current password is incorrect."}), 401

    try:
        supabase_admin.auth.admin.update_user_by_id(user_id, {"password": new_password})
    except Exception:
        return jsonify({"ok": False, "error": "Something went wrong while updating your password. Please try again."}), 500

    return jsonify({"ok": True, "message": "Password updated successfully."})


@user_bp.route("/settings/delete-account", methods=["POST"])
def delete_account():
    """
    Permanently deletes the logged-in user's account.

    Requires the current password, re-verified the same way as
    change_password (a real sign-in attempt against a fresh, isolated
    client — never trusted from the client). This is a destructive,
    irreversible action, so we do not accept "yes I confirmed on the
    frontend" as proof; the password is checked again here regardless
    of what the UI already showed the user.

    Deleting the Supabase Auth user cascades (via `profiles.id
    references auth.users(id) on delete cascade` and the various
    `... references profiles(id) on delete cascade` foreign keys in
    schema.sql) to remove: the profile row, their test_attempts,
    attempt_answers, transactions, test_access_grants, and
    user_streams rows. Nothing about other users' data is touched.

    Note: if this account ever created content as an admin (chapters,
    questions, tests, or mock_questions via their `created_by` column),
    Postgres will refuse the delete with a foreign key violation, since
    those `created_by` columns have no ON DELETE behavior defined. We
    catch that and return a clear explanation instead of a raw database
    error — self-service delete should never be able to silently orphan
    or destroy content other users depend on.
    """
    if not is_logged_in():
        return jsonify({"ok": False, "error": "Not logged in."}), 401

    user_id = current_user_id()
    current_password = request.form.get("current_password", "")

    if not current_password:
        return jsonify({"ok": False, "error": "Please enter your current password to confirm."}), 400

    if not _verify_current_password(user_id, current_password):
        return jsonify({"ok": False, "error": "Your current password is incorrect."}), 401

    try:
        supabase_admin.auth.admin.delete_user(user_id)
    except Exception as exc:
        error_msg = str(exc).lower()
        if "foreign key" in error_msg or "violates" in error_msg:
            return jsonify({
                "ok": False,
                "error": "This account can't be deleted because it has created content (tests, "
                         "questions, or streams) that other users depend on. Please contact an "
                         "administrator to transfer or remove that content first."
            }), 409
        return jsonify({"ok": False, "error": "Something went wrong while deleting your account. Please try again."}), 500

    # Account is gone — clear the session so nothing about this browser
    # still thinks the (now-deleted) user is logged in.
    session.clear()
    return jsonify({"ok": True, "message": "Your account has been permanently deleted.", "redirect": url_for("user.landing")})
