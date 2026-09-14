"""
Mock Test admin: create a test, then map mock_questions to it.

Mapping is direct-to-test: the admin pastes a JSON array (same shape
as the Mock Question bulk upload) straight into the test-manage page.
Each row is validated + inserted into mock_questions (via the same
_validate_mock_question used by mock_question_routes.py, so subject_name
resolution and stream-locking behave identically in both places),
then immediately linked to this test_id in mock_test_questions.

There is no Stream/Subject/Chapter picker here — a mock test spans
the whole syllabus by design, so the only unit of work is "this test"
and "this pasted batch of mixed-subject questions".
"""
import json

from flask import render_template, request, redirect, url_for, flash

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.admin.mock_question_routes import _validate_mock_question
from app.extensions import supabase_admin


@admin_bp.route("/tests")
@admin_required
def tests_list():
    streams = supabase_admin.table("streams").select("id, name").order("display_order").execute().data
    categories = supabase_admin.table("test_categories").select("id, name, stream_id").order("display_order").execute().data
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
    negative_marking_raw = float(request.form.get("negative_marking") or 0)
    # BUGFIX: nothing stopped a negative value (e.g. -1) here. Since
    # scoring does correct_count - (wrong_count * negative_marking),
    # a negative negative_marking flips the sign and ADDS wrong-answer
    # count to the score instead of subtracting it — this is exactly
    # what produced a score of 25 from 3 correct / 22 wrong on the
    # "Test pdf" test (someone typed -1 instead of 1). Clamp to >= 0;
    # the "per wrong answer" penalty should never itself be negative.
    if negative_marking_raw < 0:
        flash("Negative Marking can't be a negative number — it was saved as its positive value instead.", "error")
    negative_marking = abs(negative_marking_raw)

    payload = {
        "stream_id": request.form.get("stream_id"),
        "category_id": request.form.get("category_id"),
        "title": request.form.get("title", "").strip(),
        "description": request.form.get("description", "").strip() or None,  # holds the Syllabus text
        "duration_minutes": int(request.form.get("duration_minutes") or 60),
        "total_marks": int(request.form.get("total_marks")) if request.form.get("total_marks") else None,
        "negative_marking": negative_marking,
        "is_premium": request.form.get("is_premium") == "on",
        "price_inr": float(request.form.get("price_inr") or 0),
    }
    try:
        result = supabase_admin.table("tests").insert(payload).execute()
        test_id = result.data[0]["id"]
        flash("Test created — now paste questions below to map them.", "success")
        return redirect(url_for("admin.tests_manage", test_id=test_id))
    except Exception as exc:
        flash(f"Could not create test: {exc}", "error")
        return redirect(url_for("admin.tests_list"))


@admin_bp.route("/tests/<test_id>/manage")
@admin_required
def tests_manage(test_id):
    """
    Shows the test's title/syllabus, a JSON paste box, and the list of
    mock_questions already mapped to it — nothing else. No Stream/
    Subject/Chapter navigation: mapping works purely off the pasted
    JSON's own subject_name per row.
    """
    test = supabase_admin.table("tests").select("*").eq("id", test_id).single().execute().data

    mapped = (
        supabase_admin.table("mock_test_questions")
        .select("mock_question_id, mock_questions(question_text, topic_name, is_pyq, pyq_year, has_image, image_url, subjects(name))")
        .eq("test_id", test_id)
        .execute()
        .data
    )

    return render_template("admin_test_manage.html", test=test, mapped=mapped)


@admin_bp.route("/tests/<test_id>/questions/bulk-map", methods=["POST"])
@admin_required
def tests_bulk_map_questions(test_id):
    """
    Parses a pasted JSON array of mixed-subject questions, validates
    + inserts each into mock_questions (subject_name resolved against
    the TEST's stream — see _validate_mock_question), then immediately
    links every newly-inserted question to this test via
    mock_test_questions.

    Nothing is mapped without first being inserted into mock_questions
    — there's no "pick from an existing pool" step here, this route
    creates the questions and the mapping in the same request.
    """
    test = supabase_admin.table("tests").select("id, stream_id").eq("id", test_id).single().execute().data
    if not test:
        flash("Test not found.", "error")
        return redirect(url_for("admin.tests_list"))

    stream_id = test["stream_id"]
    raw_json = request.form.get("bulk_json", "").strip()

    redirect_target = lambda: redirect(url_for("admin.tests_manage", test_id=test_id))  # noqa: E731

    if not raw_json:
        flash("Paste a JSON array of questions before submitting.", "error")
        return redirect_target()

    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        flash(f"Invalid JSON — could not parse: {exc}", "error")
        return redirect_target()

    if not isinstance(parsed, list):
        flash("Expects a JSON array of question objects, e.g. [{...}, {...}].", "error")
        return redirect_target()

    if not parsed:
        flash("The pasted JSON array is empty — nothing to upload.", "error")
        return redirect_target()

    subjects_for_stream = (
        supabase_admin.table("subjects")
        .select("id, name")
        .eq("stream_id", stream_id)
        .execute()
        .data
    )
    subject_name_to_id = {s["name"].strip().lower(): s["id"] for s in subjects_for_stream}

    difficulty_ids = {
        d["id"] for d in
        supabase_admin.table("difficulty_levels").select("id").execute().data
    }

    valid_payloads = []
    errors = []
    for i, raw in enumerate(parsed, start=1):
        payload, error = _validate_mock_question(raw, subject_name_to_id, difficulty_ids, stream_id)
        if error:
            errors.append(f"row {i}: {error}")
        else:
            valid_payloads.append(payload)

    inserted_ids = []
    if valid_payloads:
        try:
            result = supabase_admin.table("mock_questions").insert(valid_payloads).execute()
            inserted_ids = [row["id"] for row in result.data]
        except Exception as exc:
            flash(f"Upload failed at the database level: {exc}", "error")
            return redirect_target()

    if inserted_ids:
        try:
            # BUGFIX: question_order was never set here, so every mapped
            # question defaulted to question_order = 0 in the DB. Since
            # get_mock_questions_for_test() sorts by question_order, every
            # batch of questions ended up with an undefined/arbitrary order
            # on the actual test-attempt screen. Use the order they were
            # pasted in (paired with inserted_ids, which insert() returns
            # in the same order valid_payloads was built in).
            mapping_rows = [
                {"test_id": test_id, "mock_question_id": qid, "question_order": i}
                for i, qid in enumerate(inserted_ids, start=1)
            ]
            supabase_admin.table("mock_test_questions").upsert(
                mapping_rows, on_conflict="test_id,mock_question_id"
            ).execute()
        except Exception as exc:
            flash(
                f"{len(inserted_ids)} question(s) were inserted into the question pool, "
                f"but linking them to this test failed: {exc}",
                "error",
            )
            return redirect_target()

    if inserted_ids and not errors:
        flash(f"{len(inserted_ids)} question(s) inserted and mapped to this test.", "success")
    elif inserted_ids and errors:
        flash(
            f"{len(inserted_ids)} question(s) inserted and mapped, but "
            f"{len(errors)} row(s) were skipped — {'; '.join(errors[:5])}"
            + (f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""),
            "error",
        )
    else:
        flash(
            f"No questions were mapped — all {len(errors)} row(s) failed validation. "
            f"{'; '.join(errors[:5])}" + (f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""),
            "error",
        )

    return redirect_target()


@admin_bp.route("/tests/<test_id>/questions/<question_id>/remove", methods=["POST"])
@admin_required
def tests_remove_question(test_id, question_id):
    """question_id here refers to mock_questions.id (the mock_test_questions FK target)."""
    supabase_admin.table("mock_test_questions").delete().eq("test_id", test_id).eq("mock_question_id", question_id).execute()
    return redirect(url_for("admin.tests_manage", test_id=test_id))


@admin_bp.route("/tests/<test_id>/delete", methods=["POST"])
@admin_required
def tests_delete(test_id):
    """
    FEATURE (was missing): delete an uploaded/created test entirely.

    `tests` has ON DELETE CASCADE from test_questions, test_attempts,
    mock_test_questions, and test_access_grants (see schema.sql /
    migration_mock_test_attempts.sql), so deleting the test row also
    cleans up its question-mapping and any student attempts against
    it automatically — nothing orphaned is left behind.

    Deliberately does NOT delete the underlying mock_questions rows
    themselves (a question may be reused across multiple tests) —
    only this test and its mapping to those questions.
    """
    try:
        supabase_admin.table("tests").delete().eq("id", test_id).execute()
        flash("Test deleted.", "success")
    except Exception as exc:
        flash(f"Could not delete test: {exc}", "error")
    return redirect(url_for("admin.tests_list"))
