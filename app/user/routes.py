# STUDY SANGAM HUB v5.0 — User Routes (Contextual My Attempts, NTA 180Q Compulsory Lock & 200Q Optional Pattern)
import re
from flask import render_template, render_template_string, request, redirect, url_for, session, jsonify, abort, flash
from jinja2 import TemplateNotFound

from app.user import user_bp
from app.extensions import supabase_admin, supabase_public, new_auth_client
from app.shared import payments_core as pay
from app.shared.models import (
    get_active_streams, get_streams_for_user, get_stream_by_slug,
    get_subjects_for_stream, get_chapters_for_subject, get_chapter_by_id,
    get_all_tests_for_stream, get_test_by_id, get_test_syllabus,
    get_series_for_stream, get_series_by_id, get_tests_for_series,
    user_has_access_to_test, get_mock_questions_for_test,
    get_mock_question_ids_for_test, create_test_attempt, get_attempt_by_id,
    submit_test_attempt, get_attempts_for_user, get_series_attempts_for_user,
    get_chapter_practice_attempts_for_user, get_attempt_time_breakdown,
    save_attempt_progress, bulk_save_attempt_progress, get_attempt_answers_map,
    get_mock_questions_for_test_review, delete_all_attempts_for_user,
    PRACTICE_ATTEMPT_KINDS, get_practice_quiz_questions, get_latest_practice_attempt,
    get_practice_attempt_by_id, get_practice_answers_map, start_practice_attempt,
    save_practice_progress, save_practice_mistake_note, submit_practice_attempt,
    get_practice_review_items, get_practice_analyse_counts, get_practice_time_breakdown, select_reattempt_question_ids,
    get_mock_review_items, get_mock_analyse_counts, save_mock_mistake_note, start_mock_reattempt,
    get_latest_full_mock_attempt, get_latest_mock_reattempt_session,
    get_latest_practice_reattempt_session, _visible_image,
    practice_set_is_premium, PRACTICE_RUNNER_COLUMNS,
    get_practice_folders_for_chapter, get_practice_sets_for_folder,
    get_test_nta_meta, compute_nta_subject_cap,
    lock_compulsory_questions_by_subject, get_locked_compulsory_question_ids,
    set_attempt_exam_pattern, get_attempt_exam_pattern,
)
from app.shared.access import (
    enrich_series_list, get_series_pricing, user_has_premium, user_has_series_access,
)
from app.shared.utils import is_logged_in, current_user_id


_BOOKS_MATERIALS_FALLBACK_HTML = '{% extends "shared/base.html" %}\n{% block title %}Books & Study Material — {{ stream.name }}{% endblock %}\n{% block content %}\n<section class="max-w-xl mx-auto px-4 pt-8 pb-20 relative z-10">\n  <div class="mb-5 flex items-center justify-between gap-3">\n    <a href="{{ url_for(\'user.stream_dashboard\', slug=stream.slug) }}" data-back-url="{{ url_for(\'user.stream_dashboard\', slug=stream.slug) }}" class="btn-cute-soft">&larr; Back to Dashboard</a>\n    <span class="text-[10px] font-black tracking-widest uppercase text-gold border border-gold/30 bg-gold/10 px-2.5 py-0.5 rounded">{{ stream.name }}</span>\n  </div>\n  <div class="pw-tile p-6 sm:p-8 text-center rounded-2xl">\n    <h1 class="font-display text-xl sm:text-2xl font-bold text-white mb-2">Join Our Telegram Channel for Books &amp; Study Material</h1>\n    <p class="text-xs sm:text-sm text-gray-300 max-w-md mx-auto leading-relaxed mb-6">All {{ stream.name }} reference books, highlighted NCERT PDFs, short notes, mindmaps, and study modules are uploaded directly on our official Telegram channel.</p>\n    <a href="https://t.me/neetjeesangam" target="_blank" rel="noopener noreferrer" class="btn-cute !px-6 !py-2.5">Join Our Telegram Channel &rarr;</a>\n  </div>\n</section>\n{% endblock %}\n'


def _resolve_active_stream_slug(existing):
    preferred = session.get("active_stream_slug")
    if preferred and any(s.get("slug") == preferred for s in (existing or [])):
        return preferred
    if existing:
        return existing[0]["slug"]
    return None


@user_bp.route("/")
def landing():
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    return redirect(url_for("user.start_learning"))


@user_bp.route("/start")
def start_learning():
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    existing = get_streams_for_user(user_id=current_user_id())
    slug = _resolve_active_stream_slug(existing)
    next_url = url_for("user.stream_dashboard", slug=slug) if slug else url_for("user.stream_select")
    return render_template("start_learning.html", next_url=next_url, active_stream_slug=slug)


@user_bp.route("/workspace")
def enter_workspace():
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    existing = get_streams_for_user(user_id=current_user_id())
    slug = _resolve_active_stream_slug(existing)
    if slug:
        return redirect(url_for("user.stream_dashboard", slug=slug))
    return redirect(url_for("user.stream_select"))


@user_bp.route("/streams", methods=["GET", "POST"])
def stream_select():
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    streams = get_active_streams()
    user_id = current_user_id()

    if request.method == "GET":
        force = request.args.get("force") == "1"
        existing = get_streams_for_user(user_id=user_id)
        current_stream_id = existing[0]["id"] if existing else None

        if not force and existing:
            return redirect(url_for("user.stream_dashboard", slug=_resolve_active_stream_slug(existing)))

        return render_template("stream_select.html", streams=streams, current_stream_id=current_stream_id)

    raw_ids = request.form.getlist("stream_ids") or ([request.form.get("stream_id")] if request.form.get("stream_id") else [])
    selected_ids = [sid for sid in raw_ids if sid]
    if not selected_ids:
        return render_template("stream_select.html", streams=streams, error="Pick at least one stream.")

    chosen_id = selected_ids[0]
    first_stream = next((s for s in streams if s["id"] == chosen_id), None)
    if not first_stream:
        return render_template("stream_select.html", streams=streams, error="Pick a valid stream.")

    try:
        supabase_admin.table("user_streams").delete().eq("user_id", user_id).execute()
    except Exception:
        pass
    supabase_admin.table("user_streams").upsert([{"user_id": user_id, "stream_id": chosen_id}]).execute()

    session["active_stream_ids"] = [chosen_id]
    session["active_stream_slug"] = first_stream["slug"]
    return redirect(url_for("user.stream_dashboard", slug=first_stream["slug"]))


@user_bp.route("/streams/<slug>")
def stream_dashboard(slug):
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    stream = get_stream_by_slug(slug)
    if not stream:
        return render_template("shared/404.html"), 404
    session["active_stream_slug"] = stream["slug"]
    return render_template("dashboard.html", stream=stream)


@user_bp.route("/streams/<slug>/materials")
def books_materials(slug):
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    stream = get_stream_by_slug(slug)
    if not stream:
        return render_template("shared/404.html"), 404
    for tpl in ("books_materials.html", "user/books_materials.html"):
        try:
            return render_template(tpl, stream=stream)
        except TemplateNotFound:
            continue
    return render_template_string(_BOOKS_MATERIALS_FALLBACK_HTML, stream=stream)


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
    slug = _resolve_active_stream_slug(existing)
    if slug:
        return redirect(url_for("user.stream_dashboard", slug=slug))
    return redirect(url_for("user.start_learning"))


# ==========================================
# MENU 1: CHAPTER-WISE MCQ/PYQ PRACTICE (WITH CONTEXTUAL MY ATTEMPTS)
# ==========================================

def _chapter_matches_stream(chapter, stream):
    if not chapter or not stream:
        return False
    ch_stream_slug = (((chapter.get("subjects") or {}).get("streams") or {}).get("slug"))
    if ch_stream_slug and ch_stream_slug != stream.get("slug"):
        return False
    return True


@user_bp.route("/streams/<slug>/practice")
def practice_subjects(slug):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    if not stream:
        abort(404)
    subjects = get_subjects_for_stream(stream["id"])
    return render_template("practice_subjects.html", stream=stream, subjects=subjects)


@user_bp.route("/streams/<slug>/practice/subject/<subject_id>")
def practice_chapters(slug, subject_id):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    if not stream:
        abort(404)
    valid_subject_ids = {s["id"] for s in (get_subjects_for_stream(stream["id"]) or [])}
    if valid_subject_ids and subject_id not in valid_subject_ids:
        return redirect(url_for("user.practice_subjects", slug=stream["slug"]))
    chapters = get_chapters_for_subject(subject_id)
    return render_template("practice_chapters.html", stream=stream, chapters=chapters)


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>")
def practice_chapter_detail(slug, chapter_id):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    if not stream or not chapter:
        abort(404)
    if not _chapter_matches_stream(chapter, stream):
        return redirect(url_for("user.practice_subjects", slug=stream["slug"]))
    chapter_attempts = get_chapter_practice_attempts_for_user(current_user_id(), chapter_id)
    return render_template(
        "practice_chapter_detail.html",
        stream=stream,
        chapter=chapter,
        chapter_attempts=chapter_attempts,
    )


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>")
def practice_category(slug, chapter_id, mode):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    if not stream or not chapter:
        abort(404)
    if not _chapter_matches_stream(chapter, stream):
        return redirect(url_for("user.practice_subjects", slug=stream["slug"]))
    return render_template("practice_category.html", stream=stream, chapter=chapter, mode=mode)


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>")
def practice_folders(slug, chapter_id, mode, category):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    if not stream or not chapter:
        abort(404)
    if not _chapter_matches_stream(chapter, stream):
        return redirect(url_for("user.practice_subjects", slug=stream["slug"]))
    folders, folder_stats = get_practice_folders_for_chapter(chapter_id, mode, category)
    return render_template(
        "practice_folders.html",
        stream=stream, chapter=chapter, mode=mode, category=category,
        folders=folders, folder_stats=folder_stats,
    )


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<name:folder>")
def practice_sets(slug, chapter_id, mode, category, folder):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    if not stream or not chapter:
        abort(404)
    if not _chapter_matches_stream(chapter, stream):
        return redirect(url_for("user.practice_subjects", slug=stream["slug"]))
    sets = get_practice_sets_for_folder(chapter_id, mode, category, folder)
    folder_attempts = get_chapter_practice_attempts_for_user(
        current_user_id(), chapter_id, mode=mode, category=category, folder_name=folder
    )
    attempts_by_set = {}
    for a in folder_attempts:
        sn = a.get("set_name")
        if sn and sn not in attempts_by_set:
            attempts_by_set[sn] = a
    return render_template(
        "practice_sets.html",
        stream=stream,
        chapter=chapter,
        mode=mode,
        category=category,
        folder=folder,
        sets=sets,
        folder_attempts=folder_attempts,
        attempts_by_set=attempts_by_set,
    )


_PRACTICE_MODES = ("mcq", "pyq")

_REATTEMPT_ERRORS = {
    "no_previous_attempt": "Finish the quiz once before reattempting only your wrong / marked questions.",
    "nothing_to_reattempt": "Nothing to reattempt in that category — great job!",
    "no_questions": "This quiz has no questions.",
    "bad_kind": "Unknown reattempt type.",
    "db_error": "Could not start the attempt. Please try again.",
    "not_logged_in": "Please log in first.",
}


def _practice_ctx(slug, chapter_id, mode):
    if mode not in _PRACTICE_MODES:
        abort(404)
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    if not stream or not chapter:
        abort(404)
    return stream, chapter


def _premium_gate(slug, chapter_id, mode, category, folder, set_name):
    if not practice_set_is_premium(chapter_id, mode, category, folder, set_name):
        return None
    if user_has_premium(current_user_id()):
        return None
    flash("This is a premium practice set.", "error")
    return redirect(url_for(
        "user.practice_overview", slug=slug, chapter_id=chapter_id, mode=mode,
        category=category, folder=folder, set_name=set_name,
    ))


def _own_practice_attempt_or_404(attempt_id, chapter_id, mode, category, folder, set_name):
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
    answers = get_practice_answers_map(attempt["id"])
    carried = {qid for qid, a in answers.items() if a.get("was_replayed") is False}
    return [qid for qid in all_ids if qid not in carried]


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<name:folder>/<name:set_name>")
def practice_overview(slug, chapter_id, mode, category, folder, set_name):
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    stream, chapter = _practice_ctx(slug, chapter_id, mode)
    questions = get_practice_quiz_questions(chapter_id, mode, category, folder, set_name, columns="id, is_premium, marks, negative_marks")

    if not questions:
        abort(404)

    is_premium = any(q.get("is_premium") for q in questions)
    locked = is_premium and not user_has_premium(current_user_id())
    total_questions = len(questions)
    total_marks = sum((q.get("marks") if q.get("marks") is not None else 4) for q in questions)
    sample_marks = questions[0].get("marks") if questions[0].get("marks") is not None else 4
    sample_neg = questions[0].get("negative_marks") if questions[0].get("negative_marks") is not None else 1

    attempt = get_latest_practice_attempt(current_user_id(), chapter_id, mode, category, folder, set_name)
    if attempt is None:
        attempt_state = "none"
    elif not attempt.get("submitted_at"):
        attempt_state = "in_progress"
    else:
        pending = get_latest_practice_reattempt_session(attempt["id"])
        if pending and not pending.get("submitted_at"):
            attempt_state, attempt = "in_progress", pending
        else:
            attempt_state = "submitted"

    return render_template(
        "practice_overview.html", stream=stream, chapter=chapter, mode=mode, category=category,
        folder=folder, set_name=set_name, total_questions=total_questions, total_marks=total_marks,
        marks_per_q=sample_marks, neg_marks=sample_neg,
        is_premium=is_premium, locked=locked, attempt=attempt, attempt_state=attempt_state,
    )


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<name:folder>/<name:set_name>/start", methods=["POST"])
def practice_start(slug, chapter_id, mode, category, folder, set_name):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    _practice_ctx(slug, chapter_id, mode)

    gate = _premium_gate(slug, chapter_id, mode, category, folder, set_name)
    if gate:
        return gate

    kind = (request.form.get("kind") or "full").strip()
    overview = url_for("user.practice_overview", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name)

    attempt_id, result = start_practice_attempt(current_user_id(), chapter_id, mode, category, folder, set_name, kind)
    if not attempt_id:
        flash(_REATTEMPT_ERRORS.get(result, "Could not start the attempt."), "error")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "redirect": overview})
        return redirect(overview)
    run_url = url_for("user.practice_run", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name, attempt_id=attempt_id)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True, "redirect": run_url})
    return redirect(run_url)


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<name:folder>/<name:set_name>/run")
def practice_run(slug, chapter_id, mode, category, folder, set_name):
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    stream, chapter = _practice_ctx(slug, chapter_id, mode)
    overview = url_for("user.practice_overview", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name)

    gate = _premium_gate(slug, chapter_id, mode, category, folder, set_name)
    if gate:
        return gate

    attempt_id = request.args.get("attempt_id")
    if not attempt_id:
        latest = get_latest_practice_attempt(current_user_id(), chapter_id, mode, category, folder, set_name)
        if latest and not latest.get("submitted_at"):
            return redirect(url_for("user.practice_run", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name, attempt_id=latest["id"]))
        if latest:
            pending = get_latest_practice_reattempt_session(latest["id"])
            if pending and not pending.get("submitted_at"):
                return redirect(url_for("user.practice_run", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name, attempt_id=pending["id"]))
        return redirect(overview)

    attempt = _own_practice_attempt_or_404(attempt_id, chapter_id, mode, category, folder, set_name)
    if attempt.get("submitted_at"):
        return redirect(url_for("user.practice_analyse", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name))

    all_questions = get_practice_quiz_questions(chapter_id, mode, category, folder, set_name, columns=PRACTICE_RUNNER_COLUMNS)
    if not all_questions:
        abort(404)
    replay_ids = _replay_ids_for_attempt(attempt, [q["id"] for q in all_questions])
    replay_set = set(replay_ids)
    questions = [q for q in all_questions if q["id"] in replay_set]
    if not questions:
        return redirect(overview)
    for q in questions:
        q["image_url"] = _visible_image(q)

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


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<name:folder>/<name:set_name>/attempt/<attempt_id>/answer", methods=["POST"])
def practice_save_answer(slug, chapter_id, mode, category, folder, set_name, attempt_id):
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

    all_ids = [q["id"] for q in get_practice_quiz_questions(chapter_id, mode, category, folder, set_name, columns="id")]
    if question_id not in set(_replay_ids_for_attempt(attempt, all_ids)):
        return jsonify({"ok": False, "error": "question not part of this attempt"}), 400

    ok = save_practice_progress(attempt_id, question_id, payload.get("selected_option"), payload.get("status"), payload.get("time_taken_sec"))
    return jsonify({"ok": bool(ok)})


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<name:folder>/<name:set_name>/attempt/<attempt_id>/note", methods=["POST"])
def practice_save_note(slug, chapter_id, mode, category, folder, set_name, attempt_id):
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


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<name:folder>/<name:set_name>/attempt/<attempt_id>/submit", methods=["POST"])
def practice_submit(slug, chapter_id, mode, category, folder, set_name, attempt_id):
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


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<name:folder>/<name:set_name>/analyse")
def practice_analyse(slug, chapter_id, mode, category, folder, set_name):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream, chapter = _practice_ctx(slug, chapter_id, mode)

    attempt = get_latest_practice_attempt(current_user_id(), chapter_id, mode, category, folder, set_name)
    overview = url_for("user.practice_overview", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name)
    if not attempt:
        return redirect(overview)
    if not attempt.get("submitted_at"):
        return redirect(url_for("user.practice_run", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name, attempt_id=attempt["id"]))

    pending = get_latest_practice_reattempt_session(attempt["id"])
    if pending and not pending.get("submitted_at"):
        return redirect(url_for("user.practice_run", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name, attempt_id=pending["id"]))

    counts = get_practice_analyse_counts(attempt["id"])
    time_breakdown = get_practice_time_breakdown(attempt["id"])
    return render_template(
        "practice_analyse.html", stream=stream, chapter=chapter, mode=mode, category=category, folder=folder,
        set_name=set_name, attempt=attempt, counts=counts, reattempt_session=pending,
        total_seconds=time_breakdown["total_seconds"], time_by_question=time_breakdown["by_question"],
    )


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<name:folder>/<name:set_name>/analyse/<view>")
def practice_analyse_view(slug, chapter_id, mode, category, folder, set_name, view):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    if view not in ("wrong", "marked", "full"):
        abort(404)
    stream, chapter = _practice_ctx(slug, chapter_id, mode)

    gate = _premium_gate(slug, chapter_id, mode, category, folder, set_name)
    if gate:
        return gate

    attempt = get_latest_practice_attempt(current_user_id(), chapter_id, mode, category, folder, set_name)
    if not attempt or not attempt.get("submitted_at"):
        return redirect(url_for("user.practice_overview", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name))

    items = get_practice_review_items(attempt["id"], view)
    return render_template(
        "practice_review.html", stream=stream, chapter=chapter, mode=mode, category=category, folder=folder,
        set_name=set_name, attempt=attempt, items=items, view=view,
    )


# ==========================================
# MENU 2: MOCK TEST SERIES (WITH FOLDER-SCOPED MY ATTEMPTS & DUAL EXAM PATTERN ENGINE)
# ==========================================

@user_bp.route("/streams/<slug>/tests")
def tests_feed(slug):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    if not stream:
        abort(404)

    raw_series = get_series_for_stream(stream["id"], stream["slug"])
    series_list = enrich_series_list(raw_series, current_user_id())
    return render_template("tests_feed.html", stream=stream, series_list=series_list)


@user_bp.route("/streams/<slug>/series/<series_id>/tests")
def series_tests(slug, series_id):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    if not stream:
        abort(404)
    raw_series = get_series_by_id(series_id)
    if not raw_series:
        abort(404)

    uid = current_user_id()
    series = enrich_series_list([raw_series], uid)[0]
    tests = get_tests_for_series(series_id, stream["id"], include_empty=True)

    series_attempts = get_series_attempts_for_user(uid, series_id, tests_in_series=tests)
    latest_attempt_by_tid = {}
    for a in series_attempts:
        tid = a.get("test_id")
        if tid and tid not in latest_attempt_by_tid:
            latest_attempt_by_tid[tid] = a

    active_tab = "attempts" if request.args.get("tab") == "attempts" else "tests"

    return render_template(
        "user_series_tests.html",
        stream=stream,
        series=series,
        tests=tests,
        series_attempts=series_attempts,
        latest_attempt_by_tid=latest_attempt_by_tid,
        active_tab=active_tab,
    )


@user_bp.route("/streams/<slug>/tests/<test_id>")
def test_overview(slug, test_id):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    if not stream:
        abort(404)
    test = get_test_by_id(test_id)
    if not test:
        flash("That test is no longer available or may have been removed.", "error")
        return redirect(url_for("user.tests_feed", slug=slug))
    test = dict(test)
    nta_meta = get_test_nta_meta(test_id, stored_total_marks=test.get("total_marks"))
    test["total_marks"] = nta_meta["effective_total_marks"]

    syllabus = get_test_syllabus(test_id)
    has_access = user_has_access_to_test(test, current_user_id())
    series_pricing = get_series_pricing(test.get("series_id")) if test.get("series_id") else {"is_paid": False, "price_inr": 0}

    attempt = _latest_mock_attempt(test_id) if has_access else None
    if attempt is None:
        attempt_state = "none"
    elif not attempt.get("submitted_at"):
        attempt_state = "in_progress"
    else:
        pending = get_latest_mock_reattempt_session(attempt["id"])
        if pending and not pending.get("submitted_at"):
            attempt_state, attempt = "in_progress", pending
        else:
            attempt_state = "submitted"

    return render_template(
        "test_overview.html", stream=stream, test=test, syllabus=syllabus, has_access=has_access,
        series_price=series_pricing.get("price_inr"),
        attempt=attempt, attempt_state=attempt_state,
        nta_meta=nta_meta,
    )


@user_bp.route("/streams/<slug>/tests/<test_id>/start", methods=["POST"])
def test_start(slug, test_id):
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    test = get_test_by_id(test_id)
    if not test:
        flash("That test is no longer available.", "error")
        return redirect(url_for("user.tests_feed", slug=slug))

    if not user_has_access_to_test(test, current_user_id()):
        flash("Please purchase this Test Series to unlock and attempt its mock tests.", "error")
        if test.get("series_id"):
            return redirect(url_for("payments.buy_series", series_id=test["series_id"]))
        return redirect(url_for("user.test_overview", slug=slug, test_id=test_id))

    qids = get_mock_question_ids_for_test(test_id) or []
    if not qids:
        flash("Questions for this test are still being uploaded. Please try another test.", "error")
        fallback_url = url_for("user.series_tests", slug=slug, series_id=test["series_id"]) if test.get("series_id") else url_for("user.tests_feed", slug=slug)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "redirect": fallback_url})
        return redirect(fallback_url)

    raw_pattern = (request.form.get("exam_pattern") or request.args.get("exam_pattern") or "compulsory_45").strip().lower()
    exam_pattern = "optional_50" if raw_pattern in ("optional_50", "optional", "200", "50") else "compulsory_45"

    kind = (request.form.get("kind") or "full").strip()
    if kind == "full":
        attempt_id = create_test_attempt(test_id, user_id=current_user_id(), exam_pattern=exam_pattern)
        result = None
    else:
        attempt_id, result = start_mock_reattempt(test_id, current_user_id(), kind, exam_pattern=exam_pattern)

    if not attempt_id:
        if result:
            flash(_REATTEMPT_ERRORS.get(result, "Could not start the attempt."), "error")
            target = url_for("user.test_analyse", slug=slug, test_id=test_id)
        else:
            target = url_for("user.test_overview", slug=slug, test_id=test_id)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "redirect": target})
        return redirect(target)

    session[f"exam_pattern_{attempt_id}"] = exam_pattern
    set_attempt_exam_pattern(attempt_id, exam_pattern)

    attempt_url = url_for("user.test_attempt", slug=slug, test_id=test_id, attempt_id=attempt_id)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True, "redirect": attempt_url})
    return redirect(attempt_url)


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
    if not test:
        flash("That test is no longer available.", "error")
        return redirect(url_for("user.tests_feed", slug=slug))
    if not user_has_access_to_test(test, current_user_id()):
        return redirect(url_for("user.test_overview", slug=slug, test_id=test_id))

    test = dict(test)
    nta_meta = get_test_nta_meta(test_id, stored_total_marks=test.get("total_marks"))
    test["total_marks"] = nta_meta["effective_total_marks"]

    exam_pattern = session.get(f"exam_pattern_{attempt_id}") or get_attempt_exam_pattern(attempt_id, default="compulsory_45")
    set_attempt_exam_pattern(attempt_id, exam_pattern)

    questions_by_subject = get_mock_questions_for_test(test_id)
    saved_progress = get_attempt_answers_map(attempt_id)

    replay_ids = _mock_replay_ids(attempt)
    if replay_ids is not None:
        keep = set(replay_ids)
        questions_by_subject = {
            subj: [q for q in qs if q["id"] in keep] for subj, qs in questions_by_subject.items()
        }
        questions_by_subject = {subj: qs for subj, qs in questions_by_subject.items() if qs}
        saved_progress = {qid: v for qid, v in saved_progress.items() if qid in keep}
        subject_caps = {subj: len(qs) for subj, qs in questions_by_subject.items()}
    else:
        if exam_pattern == "compulsory_45":
            # Randomly lock 45 Physics, 45 Chemistry, 90 Biology (180 compulsory questions total)
            questions_by_subject = lock_compulsory_questions_by_subject(questions_by_subject, attempt_id)
            locked_set = {q["id"] for qs in questions_by_subject.values() for q in qs}
            saved_progress = {qid: v for qid, v in saved_progress.items() if qid in locked_set}
            subject_caps = {subj: len(qs) for subj, qs in questions_by_subject.items()}
        else:
            # 200Q Optional Pattern: Show all uploaded questions (50P, 50C, 100B) with 45/45/90 attempt caps
            subject_caps = {subj: compute_nta_subject_cap(len(qs)) for subj, qs in questions_by_subject.items()}

    full_seconds = int((test.get("duration_minutes") or 180) * 60)
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
        subject_caps=subject_caps,
        saved_progress=saved_progress,
        duration_seconds=duration_seconds,
        nta_meta=nta_meta,
        exam_pattern=exam_pattern,
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

    exam_pattern = session.get(f"exam_pattern_{attempt_id}") or get_attempt_exam_pattern(attempt_id, default="compulsory_45")
    set_attempt_exam_pattern(attempt_id, exam_pattern)

    all_valid_ids = set(get_mock_question_ids_for_test(test_id))
    replay_ids = _mock_replay_ids(attempt)
    if replay_ids is not None:
        writable_ids = set(replay_ids)
        valid_ids = all_valid_ids
    elif exam_pattern == "compulsory_45":
        locked_ids = set(get_locked_compulsory_question_ids(test_id, attempt_id)) or all_valid_ids
        writable_ids = locked_ids
        valid_ids = locked_ids
    else:
        writable_ids = all_valid_ids
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
        if qid in writable_ids and isinstance(entry, dict)
    ]
    bulk_save_attempt_progress(attempt_id, bulk_entries)

    progress = get_attempt_answers_map(attempt_id)
    submit_test_attempt(attempt_id, test_id, progress_map=progress, valid_ids=valid_ids, exam_pattern=exam_pattern)

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
    if not stream or not test:
        abort(404)
    test = dict(test)
    nta_meta = get_test_nta_meta(test_id, stored_total_marks=test.get("total_marks"))
    test["total_marks"] = nta_meta["effective_total_marks"]

    counts = get_mock_analyse_counts(attempt_id, test_id)
    pending = get_latest_mock_reattempt_session(attempt_id)
    time_breakdown = get_attempt_time_breakdown(attempt_id, test_id)
    return render_template(
        "test_result.html",
        stream=stream, test=test, attempt=attempt, counts=counts, reattempt_session=pending,
        time_by_subject=time_breakdown["by_subject"],
        time_by_question=time_breakdown["by_question"],
        nta_meta=nta_meta,
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
    if not test:
        flash("That test is no longer available.", "error")
        return redirect(url_for("user.tests_feed", slug=slug))
    test = dict(test)
    nta_meta = get_test_nta_meta(test_id, stored_total_marks=test.get("total_marks"))
    test["total_marks"] = nta_meta["effective_total_marks"]

    exam_pattern = session.get(f"exam_pattern_{attempt_id}") or get_attempt_exam_pattern(attempt_id, default="compulsory_45")
    questions_by_subject = get_mock_questions_for_test_review(test_id, attempt_id, exam_pattern=exam_pattern)
    subject_caps = {subj: compute_nta_subject_cap(len(qs)) for subj, qs in questions_by_subject.items()}

    return render_template(
        "test_attempt.html",
        stream=stream,
        test=test,
        attempt=attempt,
        questions_by_subject=questions_by_subject,
        subject_caps=subject_caps,
        saved_progress={},
        is_review_mode=True,
        nta_meta=nta_meta,
        exam_pattern=exam_pattern,
    )


def _mock_replay_ids(attempt):
    if (attempt.get("attempt_kind") or "full") == "full":
        return None
    all_ids = list(get_mock_question_ids_for_test(attempt["test_id"]) or [])
    rows = get_attempt_answers_map(attempt["id"])
    carried = {qid for qid, row in rows.items() if row.get("was_replayed") is False}
    return [qid for qid in all_ids if qid not in carried]


@user_bp.route("/streams/<slug>/tests/<test_id>/analyse")
def test_analyse(slug, test_id):
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    test = get_test_by_id(test_id)
    if not stream or not test:
        abort(404)
    test = dict(test)
    nta_meta = get_test_nta_meta(test_id, stored_total_marks=test.get("total_marks"))
    test["total_marks"] = nta_meta["effective_total_marks"]

    attempt = _latest_mock_attempt(test_id)
    if not attempt:
        return redirect(url_for("user.test_overview", slug=slug, test_id=test_id))
    if not attempt.get("submitted_at"):
        return redirect(url_for("user.test_attempt", slug=slug, test_id=test_id, attempt_id=attempt["id"]))

    pending = get_latest_mock_reattempt_session(attempt["id"])
    if pending and not pending.get("submitted_at"):
        return redirect(url_for("user.test_attempt", slug=slug, test_id=test_id, attempt_id=pending["id"]))

    counts = get_mock_analyse_counts(attempt["id"], test_id)
    time_breakdown = get_attempt_time_breakdown(attempt["id"], test_id)
    return render_template(
        "test_analyse.html", stream=stream, test=test, attempt=attempt, counts=counts, reattempt_session=pending,
        time_by_subject=time_breakdown["by_subject"], time_by_question=time_breakdown["by_question"],
        nta_meta=nta_meta,
    )


@user_bp.route("/streams/<slug>/tests/<test_id>/analyse/<view>")
def test_analyse_view(slug, test_id, view):
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
    return get_latest_full_mock_attempt(test_id, current_user_id())


# ---------- Profile ----------

@user_bp.route("/profile")
def profile():
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    uid = current_user_id()
    attempts = get_attempts_for_user(uid)
    orders = pay.list_orders_for_user(uid) or []
    return render_template("profile.html", attempts=attempts, orders=orders)


@user_bp.route("/profile/clear-history", methods=["POST"])
def clear_history():
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    
    delete_all_attempts_for_user(current_user_id())
    
    flash("All your test history and attempts have been permanently deleted.", "success")
    return redirect(url_for("user.my_dashboard"))


USERNAME_MIN_LEN = 3
USERNAME_MAX_LEN = 20
USERNAME_TAKEN_MESSAGE = "That username is already taken. Please choose another one."


def _validate_username_format(username):
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

    if _username_taken(new_username, exclude_user_id=user_id):
        return jsonify({"ok": False, "error": USERNAME_TAKEN_MESSAGE}), 409

    dummy_email = f"{new_username}@sangam.local"

    try:
        supabase_admin.auth.admin.update_user_by_id(user_id, {"email": dummy_email})
    except Exception as exc:
        error_msg = str(exc).lower()
        if "already" in error_msg or "unique" in error_msg or "exists" in error_msg:
            return jsonify({"ok": False, "error": USERNAME_TAKEN_MESSAGE}), 409
        return jsonify({"ok": False, "error": "Something went wrong while updating your username. Please try again."}), 500

    try:
        supabase_admin.table("profiles").update({"username": new_username}).eq("id", user_id).execute()
    except Exception:
        try:
            supabase_admin.auth.admin.update_user_by_id(user_id, {"email": f"{current_username}@sangam.local"})
        except Exception:
            pass
        return jsonify({"ok": False, "error": USERNAME_TAKEN_MESSAGE}), 409

    return jsonify({"ok": True, "username": new_username, "message": "Username updated successfully."})


def _verify_current_password(user_id, current_password):
    try:
        user_res = supabase_admin.auth.admin.get_user_by_id(user_id)
        real_email = user_res.user.email if user_res and user_res.user else None
        if not real_email:
            return False

        verify_client = new_auth_client()
        verify_result = verify_client.auth.sign_in_with_password(
            {"email": real_email, "password": current_password}
        )
        return bool(verify_result and verify_result.session)
    except Exception:
        return False


@user_bp.route("/settings/verify-password", methods=["POST"])
def verify_password():
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

    session.clear()
    return jsonify({"ok": True, "message": "Your account has been permanently deleted.", "redirect": url_for("user.landing")})
