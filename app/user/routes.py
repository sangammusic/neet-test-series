from flask import render_template, request, redirect, url_for, session, jsonify, abort

from app.user import user_bp
from app.extensions import supabase_admin
from app.shared.models import (
    get_active_streams, get_streams_for_user_or_guest, get_stream_by_slug,
    get_subjects_for_stream, get_chapters_for_subject, get_chapter_by_id,
    get_topics_for_chapter, get_questions_for_practice,
    get_all_tests_for_stream, get_test_by_id, get_test_syllabus,
    user_has_access_to_test, get_mock_questions_for_test,
    get_mock_question_ids_for_test, create_test_attempt, get_attempt_by_id,
    submit_test_attempt, get_attempts_for_user, get_attempt_time_breakdown,
    save_attempt_progress, bulk_save_attempt_progress, get_attempt_answers_map,
    get_mock_questions_for_test_review,
)
from app.shared.utils import GUEST_COOKIE_NAME, get_or_create_guest_id, set_guest_cookie, is_logged_in, current_user_id


@user_bp.route("/")
def landing():
    if is_logged_in():
        existing = get_streams_for_user_or_guest(user_id=current_user_id())
        if existing:
            return redirect(url_for("user.stream_dashboard", slug=existing[0]["slug"]))
        return redirect(url_for("user.stream_select"))

    # Guests: only check streams for an EXISTING guest cookie — don't
    # create a new guest_id here just to look something up. A brand
    # new visitor with no cookie yet has no saved streams by
    # definition, so there's nothing to check.
    guest_id = request.cookies.get(GUEST_COOKIE_NAME)
    if guest_id:
        existing = get_streams_for_user_or_guest(guest_id=guest_id)
        if existing:
            return redirect(url_for("user.stream_dashboard", slug=existing[0]["slug"]))

    return render_template("landing.html")


@user_bp.route("/streams", methods=["GET", "POST"])
def stream_select():
    streams = get_active_streams()

    if request.method == "GET":
        # ?force=1 is set by switch_stream() below — it means the
        # person deliberately asked to change their stream, so the
        # form must show even though they already have one saved.
        # Without this check, "Switch Stream" would be a dead button:
        # it redirects here, this route would see existing streams,
        # and bounce them straight back to the dashboard they just
        # tried to leave.
        force = request.args.get("force") == "1"

        if not force:
            if is_logged_in():
                existing = get_streams_for_user_or_guest(user_id=current_user_id())
            else:
                guest_id = request.cookies.get(GUEST_COOKIE_NAME)
                existing = get_streams_for_user_or_guest(guest_id=guest_id) if guest_id else []

            if existing:
                return redirect(url_for("user.stream_dashboard", slug=existing[0]["slug"]))

        return render_template("stream_select.html", streams=streams)

    selected_ids = request.form.getlist("stream_ids")
    if not selected_ids:
        return render_template("stream_select.html", streams=streams, error="Pick at least one stream.")

    if is_logged_in():
        user_id = current_user_id()
        rows = [{"user_id": user_id, "stream_id": sid} for sid in selected_ids]
        supabase_admin.table("user_streams").upsert(rows).execute()
    else:
        guest_id, is_new = get_or_create_guest_id()
        if is_new:
            supabase_admin.table("guests").insert({"guest_id": guest_id}).execute()
        rows = [{"guest_id": guest_id, "stream_id": sid} for sid in selected_ids]
        supabase_admin.table("guest_streams").upsert(rows).execute()

    session["active_stream_ids"] = selected_ids
    first_stream = next((s for s in streams if s["id"] == selected_ids[0]), None)
    response = redirect(url_for("user.stream_dashboard", slug=first_stream["slug"]))
    if not is_logged_in() and is_new:
        # New guest created in this request — the cookie needs to be
        # set on the response, same as guest_start() does in
        # app/auth/routes.py. Without this, the guest_id used above
        # only exists in this one request; the next page load would
        # generate a DIFFERENT random guest_id (no cookie to read it
        # back from) and the just-saved streams would look like they
        # never happened.
        response = set_guest_cookie(response, guest_id)
    return response


@user_bp.route("/streams/<slug>")
def stream_dashboard(slug):
    stream = get_stream_by_slug(slug)
    if not stream:
        return render_template("shared/404.html"), 404
    return render_template("dashboard.html", stream=stream)


@user_bp.route("/switch-stream")
def switch_stream():
    return redirect(url_for("user.stream_select", force="1"))


@user_bp.route("/dashboard")
def my_dashboard():
    """
    BUGFIX: the nav bar's "My Dashboard" link used to point straight at
    /profile, which only shows past test attempts (often empty for a
    new user) and has no way back to the actual stream dashboard (the
    "Chapter-wise Practice" / "Mock Test Series" cards) — that page was
    only ever reachable via /streams/<slug>, which nothing in the nav
    linked to. This route re-derives the user's (or guest's) first
    saved stream, same lookup as landing(), and sends them to the real
    dashboard. If they haven't picked a stream yet, send them to pick
    one instead of 404ing.
    """
    if is_logged_in():
        existing = get_streams_for_user_or_guest(user_id=current_user_id())
    else:
        guest_id = request.cookies.get(GUEST_COOKIE_NAME)
        existing = get_streams_for_user_or_guest(guest_id=guest_id) if guest_id else []

    if existing:
        return redirect(url_for("user.stream_dashboard", slug=existing[0]["slug"]))
    return redirect(url_for("user.stream_select"))


# ---------- Menu 1: Chapter-wise MCQ Practice ----------

@user_bp.route("/streams/<slug>/practice")
def practice_subjects(slug):
    stream = get_stream_by_slug(slug)
    subjects = get_subjects_for_stream(stream["id"])
    return render_template("practice_subjects.html", stream=stream, subjects=subjects)


@user_bp.route("/streams/<slug>/practice/subject/<subject_id>")
def practice_chapters(slug, subject_id):
    stream = get_stream_by_slug(slug)
    chapters = get_chapters_for_subject(subject_id)
    return render_template("practice_chapters.html", stream=stream, chapters=chapters)


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>")
def practice_chapter_detail(slug, chapter_id):
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    return render_template("practice_chapter_detail.html", stream=stream, chapter=chapter)


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>")
def practice_mode_select(slug, chapter_id, mode):
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    return render_template("practice_mode_select.html", stream=stream, chapter=chapter, mode=mode)


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/topics")
def practice_topics(slug, chapter_id, mode):
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    topics = get_topics_for_chapter(chapter_id, is_pyq=(mode == "pyq"))
    return render_template("practice_topics.html", stream=stream, chapter=chapter, mode=mode, topics=topics)


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/run")
def practice_run(slug, chapter_id, mode):
    topic_name = request.args.get("topic")
    questions = get_questions_for_practice(chapter_id, is_pyq=(mode == "pyq"), topic_name=topic_name)

    if any(q["is_premium"] for q in questions) and not is_logged_in():
        return render_template("practice_locked.html")

    return render_template("practice_run.html", questions=questions, topic_name=topic_name)


# ---------- Menu 2: Mock Test Series ----------

@user_bp.route("/streams/<slug>/tests")
def tests_feed(slug):
    stream = get_stream_by_slug(slug)
    tests = get_all_tests_for_stream(stream["id"])
    return render_template("tests_feed.html", stream=stream, tests=tests)


@user_bp.route("/streams/<slug>/tests/<test_id>")
def test_overview(slug, test_id):
    stream = get_stream_by_slug(slug)
    test = get_test_by_id(test_id)
    syllabus = get_test_syllabus(test_id)
    has_access = user_has_access_to_test(test, current_user_id())
    return render_template("test_overview.html", stream=stream, test=test, syllabus=syllabus, has_access=has_access)


@user_bp.route("/streams/<slug>/tests/<test_id>/start", methods=["POST"])
def test_start(slug, test_id):
    """
    Creates a fresh test_attempts row and redirects into the attempt
    screen. A POST (not GET) on purpose — starting an attempt is a
    side-effecting action (it writes a row), so it shouldn't happen
    on a plain link click/page reload/prefetch.
    """
    test = get_test_by_id(test_id)
    if not test:
        abort(404)

    if not user_has_access_to_test(test, current_user_id()):
        return redirect(url_for("user.test_overview", slug=slug, test_id=test_id))

    if is_logged_in():
        attempt_id = create_test_attempt(test_id, user_id=current_user_id())
        response = redirect(url_for("user.test_attempt", slug=slug, test_id=test_id, attempt_id=attempt_id))
    else:
        guest_id, is_new = get_or_create_guest_id()
        if is_new:
            supabase_admin.table("guests").insert({"guest_id": guest_id}).execute()
        attempt_id = create_test_attempt(test_id, guest_id=guest_id)
        response = redirect(url_for("user.test_attempt", slug=slug, test_id=test_id, attempt_id=attempt_id))
        if is_new:
            response = set_guest_cookie(response, guest_id)

    if not attempt_id:
        return redirect(url_for("user.test_overview", slug=slug, test_id=test_id))

    return response


@user_bp.route("/streams/<slug>/tests/<test_id>/attempt/<attempt_id>")
def test_attempt(slug, test_id, attempt_id):
    """
    The NTA-style attempt screen: questions grouped by subject for
    tab-switching, with a client-side timer and a question-status
    palette. All answer-saving happens via AJAX to
    test_attempt_save_answer() below.
    """
    attempt = get_attempt_by_id(attempt_id)
    if not attempt or attempt["test_id"] != test_id:
        abort(404)
    if attempt.get("submitted_at"):
        return redirect(url_for("user.test_result", slug=slug, test_id=test_id, attempt_id=attempt_id))

    stream = get_stream_by_slug(slug)
    test = get_test_by_id(test_id)
    questions_by_subject = get_mock_questions_for_test(test_id)
    
    # HYDRATE PROGRESS: Pull from DB directly so page refresh doesn't lose state.
    saved_progress = get_attempt_answers_map(attempt_id)

    return render_template(
        "test_attempt.html",
        stream=stream,
        test=test,
        attempt=attempt,
        questions_by_subject=questions_by_subject,
        saved_progress=saved_progress,
    )


@user_bp.route("/streams/<slug>/tests/<test_id>/attempt/<attempt_id>/answer", methods=["POST"])
def test_attempt_save_answer(slug, test_id, attempt_id):
    """
    AJAX endpoint the attempt screen calls every time the student
    picks/changes/clears an option. Upserts directly into attempt_answers
    instead of writing into the cookie-backed Flask session. No cookie limits, no data loss.
    """
    attempt = get_attempt_by_id(attempt_id)
    if not attempt or attempt["test_id"] != test_id or attempt.get("submitted_at"):
        return jsonify({"ok": False, "error": "attempt not active"}), 400

    payload = request.get_json(silent=True) or {}
    question_id = payload.get("question_id")
    selected_option = payload.get("selected_option")  # "A"/"B"/"C"/"D"/None (None = clear)
    status = payload.get("status")  # "answered" / "marked" / "answered_marked" / "not_answered"
    time_taken_sec = payload.get("time_taken_sec")  # running total

    if not question_id:
        return jsonify({"ok": False, "error": "question_id required"}), 400

    try:
        time_taken_sec = int(time_taken_sec) if time_taken_sec is not None else 0
    except (TypeError, ValueError):
        time_taken_sec = 0

    # UPSERT directly to DB (Replaces the broken session[session_key] = progress code)
    save_attempt_progress(attempt_id, question_id, selected_option, status, time_taken_sec)

    return jsonify({"ok": True})


@user_bp.route("/streams/<slug>/tests/<test_id>/attempt/<attempt_id>/submit", methods=["POST"])
def test_attempt_submit(slug, test_id, attempt_id):
    """
    Finalizes the attempt. The client sends one final bulk payload
    of everything held in its Alpine state right before this call
    (JSON body). This guarantees no missed clicks if the network dropped.
    """
    attempt = get_attempt_by_id(attempt_id)
    if not attempt or attempt["test_id"] != test_id:
        abort(404)
    if attempt.get("submitted_at"):
        return jsonify({
            "ok": True,
            "redirect": url_for("user.test_result", slug=slug, test_id=test_id, attempt_id=attempt_id)
        })

    valid_ids = set(get_mock_question_ids_for_test(test_id))

    payload = request.get_json(silent=True) or {}
    final_answers = payload.get("answers") or {}
    
    # 1. Bulk sync whatever frontend sent us right before submitting
    bulk_entries = [
        {
            "mock_question_id": qid,
            "selected_option": entry.get("selected_option"),
            "status": entry.get("status"),
            "time_taken_sec": entry.get("time_taken_sec"),
        }
        for qid, entry in final_answers.items()
        if qid in valid_ids
    ]
    bulk_save_attempt_progress(attempt_id, bulk_entries)

    # 2. Read the guaranteed truth from DB and proceed with scoring
    progress = get_attempt_answers_map(attempt_id)
    answers = {
        qid: entry["selected_option"]
        for qid, entry in progress.items()
        if entry.get("selected_option") and qid in valid_ids
    }
    time_by_question = {
        qid: entry.get("time_taken_sec") or 0
        for qid, entry in progress.items()
        if qid in valid_ids
    }

    submit_test_attempt(attempt_id, test_id, answers, time_by_question=time_by_question)

    return jsonify({
        "ok": True,
        "redirect": url_for("user.test_result", slug=slug, test_id=test_id, attempt_id=attempt_id),
    })


@user_bp.route("/streams/<slug>/tests/<test_id>/attempt/<attempt_id>/result")
def test_result(slug, test_id, attempt_id):
    attempt = get_attempt_by_id(attempt_id)
    if not attempt or attempt["test_id"] != test_id:
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
    """
    Read-only review mode: reuses test_attempt.html with
    is_review_mode=True. Only reachable once the attempt is submitted.
    """
    attempt = get_attempt_by_id(attempt_id)
    if not attempt or attempt["test_id"] != test_id:
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


# ---------- Profile / Dashboard ----------

@user_bp.route("/profile")
def profile():
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    attempts = get_attempts_for_user(current_user_id())
    return render_template("profile.html", attempts=attempts)
