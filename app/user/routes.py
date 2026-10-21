# SANGAM STUDY v2.2 — Handcrafted UI Build (2026-10-21)
import re
from flask import render_template, render_template_string, request, redirect, url_for, session, jsonify, abort, flash
from jinja2 import TemplateNotFound

from app.user import user_bp
from app.extensions import supabase_admin, supabase_public, new_auth_client
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
    get_practice_review_items, get_practice_analyse_counts, get_practice_time_breakdown, select_reattempt_question_ids,
    get_mock_review_items, get_mock_analyse_counts, save_mock_mistake_note, start_mock_reattempt,
    get_latest_full_mock_attempt, get_latest_mock_reattempt_session,
    get_latest_practice_reattempt_session, _visible_image,
    practice_set_is_premium, PRACTICE_RUNNER_COLUMNS,
    get_practice_folders_for_chapter, get_practice_sets_for_folder,
)
from app.shared.access import user_has_premium
from app.shared.utils import is_logged_in, current_user_id


# ==========================================
# ENTRY / AUTH-GATED FLOW
#
# Guest mode has been permanently removed. Every route below that
# used to branch on "logged in vs guest_id cookie" now simply
# requires login -- an anonymous visitor is redirected straight to
# auth.login. There is no more "continue without an account" path.
# ==========================================

_BOOKS_MATERIALS_FALLBACK_HTML = '{% extends "shared/base.html" %}\n{% block title %}Books & Premium Material — {{ stream.name }}{% endblock %}\n{% block content %}\n<section class="max-w-4xl mx-auto px-5 pt-8 pb-20 relative z-10" x-data="{ selectedItem: null }">\n\n  <!-- Compact Top Header -->\n  <div class="mb-7 border-b border-white/10 pb-4 flex flex-wrap items-center justify-between gap-3">\n    <div>\n      <a href="{{ url_for(\'user.stream_dashboard\', slug=stream.slug) }}"\n         data-back-url="{{ url_for(\'user.stream_dashboard\', slug=stream.slug) }}"\n         class="text-xs font-bold text-gray-400 hover:text-gold mb-2 inline-flex items-center gap-1.5 transition-colors">\n        <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">\n          <path stroke-linecap="round" stroke-linejoin="round" d="M10 19l-7-7m0 0l7-7m-7 7h18" />\n        </svg>\n        Back to Workspace\n      </a>\n      <div class="flex items-center gap-2.5">\n        <span class="text-[10px] font-black tracking-widest uppercase text-gold border border-gold/30 bg-gold/10 px-2.5 py-0.5 rounded">{{ stream.name }}</span>\n        <h1 class="font-display text-2xl font-bold text-white tracking-tight">Books &amp; Premium Material</h1>\n      </div>\n    </div>\n  </div>\n\n  <!-- 3 Compact Resource Shelves (Clicking any item opens the Telegram Channel Vault) -->\n  <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">\n\n    <!-- Shelf 1 -->\n    <button type="button" @click="selectedItem = \'NCERT & Standard Reference Books\'"\n            class="text-left bg-[#12141d]/90 border border-white/10 hover:border-gold/45 rounded-xl p-5 backdrop-blur-xl transition-all group flex flex-col justify-between shadow-lg">\n      <div>\n        <div class="w-9 h-9 rounded-lg bg-gold/10 border border-gold/25 text-gold flex items-center justify-center mb-3">\n          <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8">\n            <path stroke-linecap="round" stroke-linejoin="round" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />\n          </svg>\n        </div>\n        <h2 class="font-display text-base font-bold text-white mb-1 group-hover:text-gold transition-colors">Reference Books &amp; NCERT</h2>\n        <p class="text-xs text-gray-400 leading-relaxed mb-3">Highlighted NCERT PDFs, exemplar banks, and standard {{ stream.name }} reference books.</p>\n      </div>\n      <span class="text-xs font-bold text-gold inline-flex items-center gap-1">Access Books &rarr;</span>\n    </button>\n\n    <!-- Shelf 2 -->\n    <button type="button" @click="selectedItem = \'Short Notes & Formula Handbooks\'"\n            class="text-left bg-[#12141d]/90 border border-white/10 hover:border-gold/45 rounded-xl p-5 backdrop-blur-xl transition-all group flex flex-col justify-between shadow-lg">\n      <div>\n        <div class="w-9 h-9 rounded-lg bg-emerald-400/10 border border-emerald-400/25 text-emerald-300 flex items-center justify-center mb-3">\n          <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8">\n            <path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />\n          </svg>\n        </div>\n        <h2 class="font-display text-base font-bold text-white mb-1 group-hover:text-emerald-300 transition-colors">Short Notes &amp; Mindmaps</h2>\n        <p class="text-xs text-gray-400 leading-relaxed mb-3">One-page revision sheets, reaction charts, and chapter-wise formula handbooks.</p>\n      </div>\n      <span class="text-xs font-bold text-emerald-300 inline-flex items-center gap-1">Access Notes &rarr;</span>\n    </button>\n\n    <!-- Shelf 3 -->\n    <button type="button" @click="selectedItem = \'Premium Modules & Ranker DPPs\'"\n            class="text-left bg-[#12141d]/90 border border-white/10 hover:border-gold/45 rounded-xl p-5 backdrop-blur-xl transition-all group flex flex-col justify-between shadow-lg">\n      <div>\n        <div class="w-9 h-9 rounded-lg bg-amber-400/10 border border-amber-400/25 text-amber-300 flex items-center justify-center mb-3">\n          <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8">\n            <path stroke-linecap="round" stroke-linejoin="round" d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />\n          </svg>\n        </div>\n        <h2 class="font-display text-base font-bold text-white mb-1 group-hover:text-amber-300 transition-colors">Premium Study Modules</h2>\n        <p class="text-xs text-gray-400 leading-relaxed mb-3">High-yield coaching modules, daily practice papers (DPPs), and ranker booklets.</p>\n      </div>\n      <span class="text-xs font-bold text-amber-300 inline-flex items-center gap-1">Access Modules &rarr;</span>\n    </button>\n\n  </div>\n\n  <!-- Official Telegram Channel Card (Shown directly + highlighted when a shelf is clicked) -->\n  <div class="bg-[#12151f]/95 border rounded-xl p-5 sm:p-6 backdrop-blur-xl shadow-xl transition-all"\n       :class="selectedItem ? \'border-[#229ED9] ring-1 ring-[#229ED9]/40\' : \'border-[#229ED9]/30\'">\n    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">\n      <div class="flex items-start gap-3.5">\n        <div class="w-10 h-10 rounded-xl bg-[#229ED9]/15 border border-[#229ED9]/30 flex items-center justify-center text-[#229ED9] shrink-0 mt-0.5">\n          <svg viewBox="0 0 24 24" fill="currentColor" class="w-5 h-5">\n            <path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm5.568 8.16c-.169 1.858-.896 6.728-.896 6.728-.041.24-.145.483-.361.62-.216.135-.481.129-.702.03l-3.128-1.516-1.51 1.454c-.155.153-.353.199-.548.126l.373-3.31 6.004-5.446c.263-.235-.058-.366-.4-.135l-7.435 4.687-3.2-1.02c-.696-.222-.71-.696.146-1.03l12.494-4.822c.58-.216 1.088.14.9 1.041z"/>\n          </svg>\n        </div>\n        <div>\n          <p x-show="selectedItem" x-cloak class="text-[11px] font-bold text-gold uppercase tracking-wider mb-1">\n            Selected: <span x-text="selectedItem"></span>\n          </p>\n          <h3 class="font-display text-lg font-bold text-white">Join Our Telegram Channel</h3>\n          <p class="text-xs text-gray-400 mt-1 leading-relaxed max-w-lg">\n            All {{ stream.name }} books, handwritten short notes, PDFs, and premium study materials are uploaded and updated daily on our official Telegram channel.\n          </p>\n        </div>\n      </div>\n\n      <a href="https://t.me/neetjeesangam" target="_blank" rel="noopener noreferrer"\n         class="shrink-0 inline-flex items-center justify-center gap-2 text-xs sm:text-sm font-bold text-white bg-[#229ED9] hover:bg-[#1e8ec4] px-5 py-3 rounded-xl shadow-[0_4px_16px_rgba(34,158,217,0.3)] transition-all">\n        <span>Join Our Telegram Channel</span>\n        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.2">\n          <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 19.5l15-15m0 0H8.25m11.25 0v11.25" />\n        </svg>\n      </a>\n    </div>\n  </div>\n\n</section>\n{% endblock %}\n<!-- SANGAM STUDY v2.2 — Handcrafted UI Build (2026-10-21) -->\n'


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

    existing = get_streams_for_user(user_id=current_user_id())
    slug = _resolve_active_stream_slug(existing)
    if slug:
        return redirect(url_for("user.stream_dashboard", slug=slug))
    return redirect(url_for("user.start_learning"))


@user_bp.route("/start")
def start_learning():
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    existing = get_streams_for_user(user_id=current_user_id())
    slug = _resolve_active_stream_slug(existing)
    if slug:
        return redirect(url_for("user.stream_dashboard", slug=slug))

    return render_template("start_learning.html")


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

    # Exclusive single-stream selection: keep only the chosen stream so NEET and JEE never mix
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
    return redirect(url_for("user.stream_select"))


# ==========================================
# MENU 1: CHAPTER-WISE MCQ/PYQ PRACTICE (8-LEVEL SANGAM FLOW)
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
    """Level 4: Select MCQ or PYQ"""
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    if not stream or not chapter:
        abort(404)
    if not _chapter_matches_stream(chapter, stream):
        return redirect(url_for("user.practice_subjects", slug=stream["slug"]))
    return render_template("practice_chapter_detail.html", stream=stream, chapter=chapter)


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>")
def practice_category(slug, chapter_id, mode):
    """Level 5: Select Topic-wise Mastery or Chapter Arena"""
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
    """Level 6: Show Folders for the specific category (paginated + legacy-safe)"""
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
    """Level 7: Show Quizzes inside the Folder (paginated + natural-sorted)"""
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    stream = get_stream_by_slug(slug)
    chapter = get_chapter_by_id(chapter_id)
    if not stream or not chapter:
        abort(404)
    if not _chapter_matches_stream(chapter, stream):
        return redirect(url_for("user.practice_subjects", slug=stream["slug"]))
    sets = get_practice_sets_for_folder(chapter_id, mode, category, folder)
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


def _premium_gate(slug, chapter_id, mode, category, folder, set_name):
    """
    PHASE 0 FIX -- premium practice sets used to open for ANY logged-in user who
    typed the URL (only the overview page knew about premium).

    Returns a redirect to the quiz overview (which shows the unlock card) when the
    set is premium and this user has no active plan; returns None when access is fine.
    Admins and Season-Pass holders pass. Free sets never touch the subscription table.
    """
    if not practice_set_is_premium(chapter_id, mode, category, folder, set_name):
        return None
    if user_has_premium(current_user_id()):
        return None
    flash("This is a premium practice set. Get the Season Pass to unlock it.", "error")
    return redirect(url_for(
        "user.practice_overview", slug=slug, chapter_id=chapter_id, mode=mode,
        category=category, folder=folder, set_name=set_name,
    ))


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


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<name:folder>/<name:set_name>")
def practice_overview(slug, chapter_id, mode, category, folder, set_name):
    """Level 8: quiz landing page. Shows Initialize / Resume / Analyse+Reattempt by attempt state."""
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    stream, chapter = _practice_ctx(slug, chapter_id, mode)
    questions = get_practice_quiz_questions(chapter_id, mode, category, folder, set_name, columns="id, is_premium, marks")

    if not questions:
        abort(404)

    is_premium = any(q.get("is_premium") for q in questions)
    locked = is_premium and not user_has_premium(current_user_id())
    total_questions = len(questions)
    total_marks = sum((q.get("marks") if q.get("marks") is not None else 4) for q in questions)

    attempt = get_latest_practice_attempt(current_user_id(), chapter_id, mode, category, folder, set_name)
    if attempt is None:
        attempt_state = "none"
    elif not attempt.get("submitted_at"):
        attempt_state = "in_progress"
    else:
        pending = get_latest_practice_reattempt_session(attempt["id"])   # unfinished reattempt -> Resume
        if pending and not pending.get("submitted_at"):
            attempt_state, attempt = "in_progress", pending
        else:
            attempt_state = "submitted"

    return render_template(
        "practice_overview.html", stream=stream, chapter=chapter, mode=mode, category=category,
        folder=folder, set_name=set_name, total_questions=total_questions, total_marks=total_marks,
        is_premium=is_premium, locked=locked, attempt=attempt, attempt_state=attempt_state,
    )


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<name:folder>/<name:set_name>/start", methods=["POST"])
def practice_start(slug, chapter_id, mode, category, folder, set_name):
    """Creates the new latest attempt (kind = full | wrong_only | marked_and_wrong_only) and opens the runner."""
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
    """Level 9: the runner. Requires a persisted attempt (?attempt_id=...); resumes saved progress."""
    if not is_logged_in():
        return redirect(url_for("auth.login"))

    stream, chapter = _practice_ctx(slug, chapter_id, mode)
    overview = url_for("user.practice_overview", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name)

    gate = _premium_gate(slug, chapter_id, mode, category, folder, set_name)
    if gate:
        return gate

    attempt_id = request.args.get("attempt_id")
    if not attempt_id:
        # Old bookmarks / links without an attempt: start (or resume) properly instead of a stateless run.
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
    for q in questions:   # admin "Image Uploading OFF": file kept, but not shown to students
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


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<name:folder>/<name:set_name>/attempt/<attempt_id>/note", methods=["POST"])
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


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<name:folder>/<name:set_name>/attempt/<attempt_id>/submit", methods=["POST"])
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


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<name:folder>/<name:set_name>/analyse")
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

    pending = get_latest_practice_reattempt_session(attempt["id"])
    if pending and not pending.get("submitted_at"):
        return redirect(url_for("user.practice_run", slug=slug, chapter_id=chapter_id, mode=mode, category=category, folder=folder, set_name=set_name, attempt_id=pending["id"]))

    counts = get_practice_analyse_counts(attempt["id"])   # always the FULL attempt's own frozen record
    time_breakdown = get_practice_time_breakdown(attempt["id"])
    return render_template(
        "practice_analyse.html", stream=stream, chapter=chapter, mode=mode, category=category, folder=folder,
        set_name=set_name, attempt=attempt, counts=counts, reattempt_session=pending,
        total_seconds=time_breakdown["total_seconds"], time_by_question=time_breakdown["by_question"],
    )


@user_bp.route("/streams/<slug>/practice/chapter/<chapter_id>/<mode>/<category>/<name:folder>/<name:set_name>/analyse/<view>")
def practice_analyse_view(slug, chapter_id, mode, category, folder, set_name, view):
    """Review list: view = wrong | marked | full. Each question has an editable mistake note."""
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
    # Only this stream's folders -- previously this had no stream_id filter
    # at all, so every stream's students saw the exact same folder list (a
    # JEE student could open a "NEET Full Syllabus" folder and land on an
    # empty test list once inside). A legacy folder with stream_id still
    # NULL (created before this column existed) is also shown to every
    # stream for now, so nothing already-live silently disappears -- see
    # sql/migration_test_series_stream.sql.
    raw_series = (
        supabase_public.table("mock_test_series")
        .select("*")
        .or_(f"stream_id.eq.{stream['id']},stream_id.is.null")
        .order("created_at", desc=True)
        .execute()
        .data or []
    )
    other_slug = "jee" if stream["slug"].lower() == "neet" else ("neet" if stream["slug"].lower() == "jee" else "")
    series_list = [
        s for s in raw_series
        if s.get("stream_id") == stream["id"]
        or (
            s.get("stream_id") is None
            and (not other_slug or other_slug not in (s.get("name") or "").lower())
        )
    ]
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
        # New independent full attempt; earlier submitted attempts stay in history.
        attempt_id = create_test_attempt(test_id, user_id=current_user_id())
        result = None
    else:
        attempt_id, result = start_mock_reattempt(test_id, current_user_id(), kind)

    if not attempt_id:
        if result:
            flash(_REATTEMPT_ERRORS.get(result, "Could not start the attempt."), "error")
            target = url_for("user.test_analyse", slug=slug, test_id=test_id)
        else:
            target = url_for("user.test_overview", slug=slug, test_id=test_id)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "redirect": target})
        return redirect(target)

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
    if not stream or not test:
        abort(404)
    counts = get_mock_analyse_counts(attempt_id, test_id)
    pending = get_latest_mock_reattempt_session(attempt_id)
    time_breakdown = get_attempt_time_breakdown(attempt_id, test_id)
    return render_template(
        "test_result.html",
        stream=stream, test=test, attempt=attempt, counts=counts, reattempt_session=pending,
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

    pending = get_latest_mock_reattempt_session(attempt["id"])
    if pending and not pending.get("submitted_at"):
        return redirect(url_for("user.test_attempt", slug=slug, test_id=test_id, attempt_id=pending["id"]))

    counts = get_mock_analyse_counts(attempt["id"], test_id)   # the FULL attempt's own frozen record
    time_breakdown = get_attempt_time_breakdown(attempt["id"], test_id)
    return render_template(
        "test_analyse.html", stream=stream, test=test, attempt=attempt, counts=counts, reattempt_session=pending,
        time_by_subject=time_breakdown["by_subject"], time_by_question=time_breakdown["by_question"],
    )


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
    """User's latest FULL attempt for this test (never a reattempt session)."""
    return get_latest_full_mock_attempt(test_id, current_user_id())


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

        verify_client = new_auth_client()
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
