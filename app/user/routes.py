from flask import render_template, request, redirect, url_for, session, jsonify, abort, flash

from app.user import user_bp
from app.extensions import supabase_admin, supabase_public
from app.shared.models import (
    get_active_streams, get_streams_for_user_or_guest, get_stream_by_slug,
    get_subjects_for_stream, get_chapters_for_subject, get_chapter_by_id,
    get_all_tests_for_stream, get_test_by_id, get_test_syllabus,
    user_has_access_to_test, get_mock_questions_for_test,
    get_mock_question_ids_for_test, create_test_attempt, get_attempt_by_id,
    submit_test_attempt, get_attempts_for_user, get_attempt_time_breakdown,
    save_attempt_progress, bulk_save_attempt_progress, get_attempt_answers_map,
    get_mock_questions_for_test_review, delete_all_attempts_for_user
)
from app.shared.utils import GUEST_COOKIE_NAME, get_or_create_guest_id, set_guest_cookie, is_logged_in, current_user_id


@user_bp.route("/")
def landing():
    if is_logged_in():
        existing = get_streams_for_user_or_guest(user_id=current_user_id())
        if existing:
            return redirect(url_for("user.stream_dashboard", slug=existing[0]["slug"]))
        return redirect(url_for("user.stream_select"))

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
    if is_logged_in():
        existing = get_streams_for_user_or_guest(user_id=current_user_id())
    else:
        guest_id = request.cookies.get(GUEST_COOKIE_NAME)
        existing = get_streams_for_user_or_guest(guest_id=guest_id) if guest_id else []

    if existing:
        return redirect(url_for("user.stream_dashboard", slug=existing[0]["slug"]))
    return redirect(url_for("user.stream_select"))


# ==========================================
# MENU 1: CHAPTER-WISE MCQ/PYQ PRACTICE (8-LEVEL SANGAM FLOW)
# ==========================================

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
def practice_type(slug, chapter_id):
    """Level 4: Select MCQ or PYQ"""
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    return render_template("practice_type.html", stream=stream, chapter=chapter)


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>")
def practice_category(slug, chapter_id, mode):
    """Level 5: Select Topic-wise or Random"""
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    return render_template("practice_category.html", stream=stream, chapter=chapter, mode=mode)


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>")
def practice_folders(slug, chapter_id, mode, category):
    """Level 6: Show Folders for the specific category"""
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    is_pyq = (mode == "pyq")
    
    rows = supabase_public.table("questions").select("folder_name").eq("chapter_id", chapter_id).eq("is_pyq", is_pyq).eq("category", category).execute().data
    folders = sorted(list(set([r["folder_name"] for r in rows if r.get("folder_name")])))
    
    return render_template("practice_folders.html", stream=stream, chapter=chapter, mode=mode, category=category, folders=folders)


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<folder>")
def practice_sets(slug, chapter_id, mode, category, folder):
    """Level 7: Show Quizzes inside the Folder"""
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


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<folder>/<set_name>")
def practice_overview(slug, chapter_id, mode, category, folder, set_name):
    """Level 8: Test Overview Page (Prevents answer leak, provides 'Start' button)"""
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    is_pyq = (mode == "pyq")

    questions = supabase_public.table("questions").select("id, is_premium, marks").eq("chapter_id", chapter_id).eq("is_pyq", is_pyq).eq("category", category).eq("folder_name", folder).eq("set_name", set_name).execute().data

    if not questions:
        abort(404)

    is_premium = any(q.get("is_premium") for q in questions)
    if is_premium and not is_logged_in():
        return render_template("practice_locked.html")

    total_questions = len(questions)
    total_marks = sum(q.get("marks", 4) for q in questions)

    return render_template("practice_overview.html", stream=stream, chapter=chapter, mode=mode, category=category, folder=folder, set_name=set_name, total_questions=total_questions, total_marks=total_marks, is_premium=is_premium)


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<folder>/<set_name>/run")
def practice_run(slug, chapter_id, mode, category, folder, set_name):
    """Level 9: Actual Test Runner for Practice (No DB Saves)"""
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    is_pyq = (mode == "pyq")

    questions = supabase_public.table("questions").select("*").eq("chapter_id", chapter_id).eq("is_pyq", is_pyq).eq("category", category).eq("folder_name", folder).eq("set_name", set_name).order("id").execute().data

    if not questions:
        abort(404)

    if any(q.get("is_premium") for q in questions) and not is_logged_in():
        return render_template("practice_locked.html")

    return render_template("practice_runner.html", stream=stream, chapter=chapter, mode=mode, category=category, folder=folder, set_name=set_name, questions=questions)


# ==========================================
# MENU 2: MOCK TEST SERIES (PHASE 4: STRICT ISOLATION)
# ==========================================

@user_bp.route("/streams/<slug>/tests")
def tests_feed(slug):
    """
    PHASE 4: Shows Folders (Test Series) instead of flat bikhre hue tests.
    """
    stream = get_stream_by_slug(slug)
    # Fetch all VIP Folders created by admin
    series_list = (
        supabase_admin.table("mock_test_series")
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
    stream = get_stream_by_slug(slug)
    series = supabase_admin.table("mock_test_series").select("*").eq("id", series_id).single().execute().data
    if not series:
        abort(404)
    
    # Strict isolation: series_id + stream_id
    tests = (
        supabase_admin.table("tests")
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
    stream = get_stream_by_slug(slug)
    test = get_test_by_id(test_id)
    syllabus = get_test_syllabus(test_id)
    has_access = user_has_access_to_test(test, current_user_id())
    return render_template("test_overview.html", stream=stream, test=test, syllabus=syllabus, has_access=has_access)


@user_bp.route("/streams/<slug>/tests/<test_id>/start", methods=["POST"])
def test_start(slug, test_id):
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
    attempt = get_attempt_by_id(attempt_id)
    if not attempt or attempt["test_id"] != test_id:
        abort(404)
    if attempt.get("submitted_at"):
        return redirect(url_for("user.test_result", slug=slug, test_id=test_id, attempt_id=attempt_id))

    stream = get_stream_by_slug(slug)
    test = get_test_by_id(test_id)
    questions_by_subject = get_mock_questions_for_test(test_id)
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
    attempt = get_attempt_by_id(attempt_id)
    if not attempt or attempt["test_id"] != test_id or attempt.get("submitted_at"):
        return jsonify({"ok": False, "error": "attempt not active"}), 400

    payload = request.get_json(silent=True) or {}
    question_id = payload.get("question_id")
    selected_option = payload.get("selected_option")
    status = payload.get("status")
    time_taken_sec = payload.get("time_taken_sec")

    if not question_id:
        return jsonify({"ok": False, "error": "question_id required"}), 400

    try:
        time_taken_sec = int(time_taken_sec) if time_taken_sec is not None else 0
    except (TypeError, ValueError):
        time_taken_sec = 0

    save_attempt_progress(attempt_id, question_id, selected_option, status, time_taken_sec)
    return jsonify({"ok": True})


@user_bp.route("/streams/<slug>/tests/<test_id>/attempt/<attempt_id>/submit", methods=["POST"])
def test_attempt_submit(slug, test_id, attempt_id):
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


@user_bp.route("/profile/clear-history", methods=["POST"])
def clear_history():
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    
    delete_all_attempts_for_user(current_user_id())
    
    flash("All your test history and attempts have been permanently deleted.", "success")
    return redirect(url_for("user.profile"))
