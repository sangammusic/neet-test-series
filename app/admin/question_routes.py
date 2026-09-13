from flask import render_template, request, redirect, url_for, flash

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.extensions import supabase_admin


@admin_bp.route("/questions")
@admin_required
def questions_list():
    streams = supabase_admin.table("streams").select("id, name").order("display_order").execute().data

    stream_id = request.args.get("stream_id")
    subject_id = request.args.get("subject_id")
    chapter_id = request.args.get("chapter_id")

    subjects = []
    chapters = []
    questions = []
    chapter = None

    if stream_id:
        subjects = (
            supabase_admin.table("subjects")
            .select("id, name")
            .eq("stream_id", stream_id)
            .eq("is_active", True)
            .order("display_order")
            .execute()
            .data
        )

    if subject_id:
        chapters = (
            supabase_admin.table("chapters")
            .select("id, name")
            .eq("subject_id", subject_id)
            .eq("is_active", True)
            .order("display_order")
            .execute()
            .data
        )

    if chapter_id:
        chapter = supabase_admin.table("chapters").select("id, name").eq("id", chapter_id).single().execute().data
        questions = (
            supabase_admin.table("questions")
            .select("id, question_text, is_pyq, pyq_year, topic_name, is_premium, difficulty_id")
            .eq("chapter_id", chapter_id)
            .order("created_at", desc=True)
            .execute()
            .data
        )

    difficulty_levels = supabase_admin.table("difficulty_levels").select("id, name").order("display_order").execute().data

    return render_template(
        "admin_questions.html",
        streams=streams, subjects=subjects, chapters=chapters, questions=questions,
        chapter=chapter, difficulty_levels=difficulty_levels,
        selected_stream_id=stream_id, selected_subject_id=subject_id, selected_chapter_id=chapter_id,
    )


@admin_bp.route("/questions/create", methods=["POST"])
@admin_required
def questions_create():
    chapter_id = request.form.get("chapter_id")
    question_type = request.form.get("question_type")  # 'mcq' or 'pyq'
    is_pyq = (question_type == "pyq")
    pyq_year = request.form.get("pyq_year") or None

    payload = {
        "chapter_id": chapter_id,
        "difficulty_id": int(request.form.get("difficulty_id")),
        "topic_name": request.form.get("topic_name", "").strip() or "General",
        "question_text": request.form.get("question_text", "").strip(),
        "option_a": request.form.get("option_a", "").strip(),
        "option_b": request.form.get("option_b", "").strip(),
        "option_c": request.form.get("option_c", "").strip(),
        "option_d": request.form.get("option_d", "").strip(),
        "correct_option": request.form.get("correct_option"),
        "explanation": request.form.get("explanation", "").strip() or None,
        "is_pyq": is_pyq,
        "pyq_year": int(pyq_year) if (is_pyq and pyq_year) else None,
        "image_url": request.form.get("image_url", "").strip() or None,
        "is_premium": request.form.get("is_premium") == "on",
    }

    try:
        supabase_admin.table("questions").insert(payload).execute()
        flash("Question added.", "success")
    except Exception as exc:
        flash(f"Could not add question: {exc}", "error")

    # keep the admin on the same chapter view after adding
    subject_id = request.form.get("subject_id")
    stream_id = request.form.get("stream_id")
    return redirect(url_for("admin.questions_list", stream_id=stream_id, subject_id=subject_id, chapter_id=chapter_id))


@admin_bp.route("/questions/<question_id>/delete", methods=["POST"])
@admin_required
def questions_delete(question_id):
    stream_id = request.args.get("stream_id")
    subject_id = request.args.get("subject_id")
    chapter_id = request.args.get("chapter_id")
    try:
        supabase_admin.table("questions").delete().eq("id", question_id).execute()
        flash("Question deleted.", "success")
    except Exception as exc:
        flash(f"Could not delete question: {exc}", "error")
    return redirect(url_for("admin.questions_list", stream_id=stream_id, subject_id=subject_id, chapter_id=chapter_id))
