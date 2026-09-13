from flask import render_template, request, redirect, url_for, session

from app.user import user_bp
from app.extensions import supabase_admin
from app.shared.models import (
    get_active_streams, get_streams_for_user_or_guest, get_stream_by_slug,
    get_subjects_for_stream, get_chapters_for_subject, get_chapter_by_id,
    get_topics_for_chapter, get_questions_for_practice,
    get_all_tests_for_stream, get_test_by_id, get_test_syllabus,
    user_has_access_to_test,
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
