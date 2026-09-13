from flask import render_template, request, redirect, url_for, flash

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.extensions import supabase_admin


@admin_bp.route("/tests")
@admin_required
def tests_list():
    streams = supabase_admin.table("streams").select("id, name").order("display_order").execute().data
    categories = supabase_admin.table("test_categories").select("id, name, stream_id").execute().data
    tests = (
        supabase_admin.table("tests")
        .select("id, title, duration_minutes, total_marks, is_premium, is_active, streams(name)")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    return render_template("admin_tests.html", streams=streams, categories=categories, tests=tests)


@admin_bp.route("/tests/create", methods=["POST"])
@admin_required
def tests_create():
    payload = {
        "stream_id": request.form.get("stream_id"),
        "category_id": request.form.get("category_id"),
        "title": request.form.get("title", "").strip(),
        "description": request.form.get("description", "").strip() or None,
        "duration_minutes": int(request.form.get("duration_minutes") or 60),
        "total_marks": int(request.form.get("total_marks")) if request.form.get("total_marks") else None,
        "negative_marking": float(request.form.get("negative_marking") or 0),
        "is_premium": request.form.get("is_premium") == "on",
        "price_inr": float(request.form.get("price_inr") or 0),
    }
    try:
        result = supabase_admin.table("tests").insert(payload).execute()
        test_id = result.data[0]["id"]
        flash("Test created — now map questions to it below.", "success")
        return redirect(url_for("admin.tests_manage", test_id=test_id))
    except Exception as exc:
        flash(f"Could not create test: {exc}", "error")
        return redirect(url_for("admin.tests_list"))


@admin_bp.route("/tests/<test_id>/manage")
@admin_required
def tests_manage(test_id):
    test = supabase_admin.table("tests").select("*").eq("id", test_id).single().execute().data
    streams = supabase_admin.table("streams").select("id, name").order("display_order").execute().data

    chapter_id = request.args.get("chapter_id")
    subject_id = request.args.get("subject_id")
    stream_id = request.args.get("stream_id", test["stream_id"])

    subjects = (
        supabase_admin.table("subjects").select("id, name").eq("stream_id", stream_id).order("display_order").execute().data
        if stream_id else []
    )
    chapters = (
        supabase_admin.table("chapters").select("id, name").eq("subject_id", subject_id).order("display_order").execute().data
        if subject_id else []
    )
    chapter_questions = (
        supabase_admin.table("questions").select("id, question_text, topic_name, is_pyq").eq("chapter_id", chapter_id).execute().data
        if chapter_id else []
    )

    mapped = (
        supabase_admin.table("test_questions")
        .select("question_id, questions(question_text, topic_name, chapters(name, subjects(name)))")
        .eq("test_id", test_id)
        .execute()
        .data
    )
    mapped_ids = {m["question_id"] for m in mapped}

    return render_template(
        "admin_test_manage.html", test=test, streams=streams, subjects=subjects, chapters=chapters,
        chapter_questions=chapter_questions, mapped=mapped, mapped_ids=mapped_ids,
        selected_stream_id=stream_id, selected_subject_id=subject_id, selected_chapter_id=chapter_id,
    )


@admin_bp.route("/tests/<test_id>/questions/add", methods=["POST"])
@admin_required
def tests_add_questions(test_id):
    question_ids = request.form.getlist("question_ids")
    rows = [{"test_id": test_id, "question_id": qid} for qid in question_ids]
    if rows:
        try:
            supabase_admin.table("test_questions").upsert(rows).execute()
            flash(f"Added {len(rows)} question(s) to the test.", "success")
        except Exception as exc:
            flash(f"Could not add questions: {exc}", "error")
    return redirect(url_for(
        "admin.tests_manage", test_id=test_id,
        stream_id=request.form.get("stream_id"), subject_id=request.form.get("subject_id"),
        chapter_id=request.form.get("chapter_id"),
    ))


@admin_bp.route("/tests/<test_id>/questions/<question_id>/remove", methods=["POST"])
@admin_required
def tests_remove_question(test_id, question_id):
    supabase_admin.table("test_questions").delete().eq("test_id", test_id).eq("question_id", question_id).execute()
    return redirect(url_for("admin.tests_manage", test_id=test_id))
