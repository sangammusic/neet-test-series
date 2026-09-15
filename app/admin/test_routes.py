"""
Pillar 1: Mock Test Series (The Folder System).

Flow:
1. Level 1: Admin creates "Test Series Folders" (mock_test_series).
2. Level 2: Admin drills into a folder to see/create Tests.
   - BUG FIXED: Duplicate categories filtered.
   - VALIDATION: Blank total_marks throws an error, no automatic default.
3. Level 3: Admin manages questions for a test via:
    A. 25-Chunk Upload (3-Tabs) with STRICT NTA LIMITS (45-45-90 for 720 marks).
    B. Live JSON Edit: Seamless replacement of JSON + Image toggle.
    C. NO DELETE FOR SINGLE QUESTIONS (Pattern protection).
    D. Deep Storage Cleanup on Full Test Delete.
"""
import json

from flask import render_template, request, redirect, url_for, flash, jsonify

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.admin.mock_question_routes import _validate_mock_question
from app.extensions import supabase_admin

BUCKET_NAME = "question-images"


def _delete_image_from_storage(image_url):
    """
    Helper function for Pillar 4: Deep Storage Cleanup.
    Extracts the file path from the public image_url and permanently 
    deletes it from the Supabase Storage bucket.
    """
    if not image_url:
        return
    try:
        # URL format: https://[project_ref].supabase.co/storage/v1/object/public/question-images/mock_questions/[id]/[uuid].jpg
        if "question-images/" in image_url:
            path = image_url.split("question-images/")[1]
            supabase_admin.storage.from_(BUCKET_NAME).remove([path])
    except Exception as e:
        print(f"Failed to delete image from storage {image_url}: {e}")


# ==========================================
# LEVEL 1: TEST SERIES FOLDERS
# ==========================================
@admin_bp.route("/test-series", methods=["GET", "POST"])
@admin_required
def test_series_list():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Folder name cannot be empty.", "error")
        else:
            try:
                supabase_admin.table("mock_test_series").insert({"name": name}).execute()
                flash(f"Folder '{name}' created successfully.", "success")
            except Exception as exc:
                flash(f"Could not create folder: {exc}", "error")
        return redirect(url_for("admin.test_series_list"))

    series = (
        supabase_admin.table("mock_test_series")
        .select("*")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    return render_template("admin_test_folders.html", series=series)


# ==========================================
# LEVEL 2: TESTS INSIDE A FOLDER
# ==========================================
@admin_bp.route("/test-series/<series_id>/tests", methods=["GET", "POST"])
@admin_required
def series_tests(series_id):
    series_data = supabase_admin.table("mock_test_series").select("*").eq("id", series_id).single().execute().data
    if not series_data:
        flash("Test Series Folder not found.", "error")
        return redirect(url_for("admin.test_series_list"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        total_marks_raw = request.form.get("total_marks")

        # STRICT VALIDATION: Throws error if critical fields are blank
        if not title or not total_marks_raw or not str(total_marks_raw).strip():
            flash("Please fill all the details, including Total Marks.", "error")
            return redirect(url_for("admin.series_tests", series_id=series_id))

        negative_marking_raw = float(request.form.get("negative_marking") or 0)
        negative_marking = abs(negative_marking_raw)

        payload = {
            "series_id": series_id,
            "stream_id": request.form.get("stream_id"),
            "category_id": request.form.get("category_id"),
            "title": title,
            "description": request.form.get("description", "").strip() or None,
            "duration_minutes": int(request.form.get("duration_minutes") or 180),
            "total_marks": int(total_marks_raw),
            "negative_marking": negative_marking,
            "is_premium": request.form.get("is_premium") == "on",
            "price_inr": float(request.form.get("price_inr") or 0),
        }
        try:
            result = supabase_admin.table("tests").insert(payload).execute()
            test_id = result.data[0]["id"]
            flash("Test created — you can now map questions in chunks.", "success")
            return redirect(url_for("admin.tests_manage", test_id=test_id))
        except Exception as exc:
            flash(f"Could not create test: {exc}", "error")
            return redirect(url_for("admin.series_tests", series_id=series_id))

    streams = supabase_admin.table("streams").select("id, name").order("display_order").execute().data
    
    # CATEGORY DEDUPLICATION: Ensures "Minor Test" prints only once
    raw_categories = supabase_admin.table("test_categories").select("id, name, stream_id").order("display_order").execute().data
    seen_cats = set()
    categories = []
    for c in raw_categories:
        key = c["name"].strip().lower() 
        if key not in seen_cats:
            seen_cats.add(key)
            categories.append(c)

    tests = (
        supabase_admin.table("tests")
        .select("id, title, duration_minutes, total_marks, is_premium, is_active, streams(name)")
        .eq("series_id", series_id)
        .order("created_at", desc=True)
        .execute()
        .data
    )
    return render_template("admin_tests.html", series=series_data, streams=streams, categories=categories, tests=tests)


# ==========================================
# LEVEL 3: MANAGE QUESTIONS (UPLOAD & EDIT)
# ==========================================
@admin_bp.route("/tests/<test_id>/manage")
@admin_required
def tests_manage(test_id):
    test = supabase_admin.table("tests").select("*, mock_test_series(id, name)").eq("id", test_id).single().execute().data
    if not test:
        flash("Test not found.", "error")
        return redirect(url_for("admin.test_series_list"))

    mapped = (
        supabase_admin.table("mock_test_questions")
        .select("mock_question_id, question_order, mock_questions(question_text, topic_name, is_pyq, pyq_year, has_image, image_url, subjects(name))")
        .eq("test_id", test_id)
        .order("question_order")
        .execute()
        .data
    )
    return render_template("admin_test_manage.html", test=test, mapped=mapped)


@admin_bp.route("/tests/<test_id>/questions/bulk-map", methods=["POST"])
@admin_required
def tests_bulk_map_questions(test_id):
    is_ajax = request.args.get("ajax") == "1"
    
    test = supabase_admin.table("tests").select("id, stream_id, total_marks").eq("id", test_id).single().execute().data
    if not test:
        if is_ajax: return jsonify({"ok": False, "error": "Test not found."}), 404
        flash("Test not found.", "error")
        return redirect(url_for("admin.test_series_list"))

    stream_id = test["stream_id"]
    
    if request.is_json:
        raw_json_data = request.json.get("bulk_json")
        raw_json = json.dumps(raw_json_data) if isinstance(raw_json_data, list) else str(raw_json_data or "").strip()
    else:
        raw_json = request.form.get("bulk_json", "").strip()

    if not raw_json:
        if is_ajax: return jsonify({"ok": False, "error": "Paste a JSON array of questions."}), 400
        return jsonify({"ok": False, "error": "No data"}), 400

    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        if is_ajax: return jsonify({"ok": False, "error": "Invalid JSON format. Check commas and quotes."}), 400
        return jsonify({"ok": False, "error": "Invalid JSON"}), 400

    if not isinstance(parsed, list) or len(parsed) == 0:
        if is_ajax: return jsonify({"ok": False, "error": "Empty or invalid array."}), 400
        return jsonify({"ok": False, "error": "Empty array"}), 400

    subjects_for_stream = supabase_admin.table("subjects").select("id, name").eq("stream_id", stream_id).execute().data
    subject_name_to_id = {s["name"].strip().lower(): s["id"] for s in subjects_for_stream}
    id_to_subj = {s["id"]: s["name"].strip().lower() for s in subjects_for_stream}
    
    difficulty_ids = {d["id"] for d in supabase_admin.table("difficulty_levels").select("id").execute().data}

    valid_payloads, errors = [], []
    for i, raw in enumerate(parsed, start=1):
        payload, error = _validate_mock_question(raw, subject_name_to_id, difficulty_ids, stream_id)
        if error:
            errors.append(f"row {i}: {error}")
        else:
            valid_payloads.append(payload)

    # ---------------------------------------------------------
    # STRICT NTA LIMIT LOGIC (45-45-90) FOR 720 MARKS TESTS
    # ---------------------------------------------------------
    if test.get("total_marks") == 720 and not errors:
        existing = supabase_admin.table("mock_test_questions").select("mock_questions(subjects(name))").eq("test_id", test_id).execute().data
        
        phys_count = 0
        chem_count = 0
        bio_count = 0
        
        for row in existing:
            q = row.get("mock_questions")
            if q and q.get("subjects") and q["subjects"].get("name"):
                s_name = q["subjects"]["name"].strip().lower()
                if "physics" in s_name: phys_count += 1
                elif "chem" in s_name: chem_count += 1
                elif "bio" in s_name or "botan" in s_name or "zoolo" in s_name: bio_count += 1
                
        for p in valid_payloads:
            s_name = id_to_subj.get(p.get("subject_id"), "")
            if "physics" in s_name: phys_count += 1
            elif "chem" in s_name: chem_count += 1
            elif "bio" in s_name or "botan" in s_name or "zoolo" in s_name: bio_count += 1
            
        if phys_count > 45:
            if is_ajax: return jsonify({"ok": False, "error": f"NTA LIMIT ERROR: Uploading exceeds the 45 Physics limit. (Total would be {phys_count})"}), 400
        if chem_count > 45:
            if is_ajax: return jsonify({"ok": False, "error": f"NTA LIMIT ERROR: Uploading exceeds the 45 Chemistry limit. (Total would be {chem_count})"}), 400
        if bio_count > 90:
            if is_ajax: return jsonify({"ok": False, "error": f"NTA LIMIT ERROR: Uploading exceeds the 90 Biology limit. (Total would be {bio_count})"}), 400
    # ---------------------------------------------------------

    inserted_ids = []
    if valid_payloads and not errors:
        try:
            result = supabase_admin.table("mock_questions").insert(valid_payloads).execute()
            inserted_ids = [row["id"] for row in result.data]
        except Exception as exc:
            if is_ajax: return jsonify({"ok": False, "error": f"DB Upload failed: {exc}"}), 500
            return jsonify({"ok": False, "error": "DB Upload failed"}), 500

    if inserted_ids:
        try:
            existing_orders = supabase_admin.table("mock_test_questions").select("question_order").eq("test_id", test_id).execute().data
            start_order = max([row["question_order"] for row in existing_orders], default=-1) + 1 if existing_orders else 0
            
            mapping_rows = [
                {"test_id": test_id, "mock_question_id": qid, "question_order": start_order + i}
                for i, qid in enumerate(inserted_ids)
            ]
            supabase_admin.table("mock_test_questions").upsert(mapping_rows, on_conflict="test_id,mock_question_id").execute()
        except Exception as exc:
            if is_ajax: return jsonify({"ok": False, "error": f"Mapping failed: {exc}"}), 500

    return jsonify({"ok": True if inserted_ids else False, "inserted": len(inserted_ids), "errors": errors})


@admin_bp.route("/tests/<test_id>/questions/<question_id>/edit", methods=["POST"])
@admin_required
def tests_edit_question(test_id, question_id):
    """
    Live JSON Edit: Fully replaces the question text, options directly.
    Handles manual True/False image toggle safely.
    """
    raw = request.json
    if not raw:
        return jsonify({"ok": False, "error": "No JSON payload received"}), 400

    test = supabase_admin.table("tests").select("stream_id").eq("id", test_id).single().execute().data
    if not test:
        return jsonify({"ok": False, "error": "Test not found."}), 404

    stream_id = test["stream_id"]
    subjects = supabase_admin.table("subjects").select("id, name").eq("stream_id", stream_id).execute().data
    subject_name_to_id = {s["name"].strip().lower(): s["id"] for s in subjects}
    difficulty_ids = {d["id"] for d in supabase_admin.table("difficulty_levels").select("id").execute().data}

    payload, error = _validate_mock_question(raw, subject_name_to_id, difficulty_ids, stream_id)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    old_q = supabase_admin.table("mock_questions").select("image_url, has_image").eq("id", question_id).single().execute().data
    if old_q and old_q.get("image_url") and not payload.get("has_image"):
        _delete_image_from_storage(old_q["image_url"])
        payload["image_url"] = None

    try:
        supabase_admin.table("mock_questions").update(payload).eq("id", question_id).execute()
        return jsonify({"ok": True, "has_image": payload.get("has_image")})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@admin_bp.route("/tests/<test_id>/delete", methods=["POST"])
@admin_required
def tests_delete(test_id):
    """
    Deep Storage Permanent Cleanup: 
    When an admin deletes a test, the backend permanently deletes EVERY 
    single image attached to this test before wiping it from DB.
    """
    try:
        test_info = supabase_admin.table("tests").select("series_id").eq("id", test_id).single().execute().data
        series_id = test_info.get("series_id") if test_info else None

        mapped = (
            supabase_admin.table("mock_test_questions")
            .select("mock_question_id, mock_questions(image_url)")
            .eq("test_id", test_id)
            .execute()
            .data
        )

        question_ids = []
        for row in mapped:
            qid = row.get("mock_question_id")
            if qid:
                question_ids.append(qid)
            
            q = row.get("mock_questions")
            if q and q.get("image_url"):
                _delete_image_from_storage(q["image_url"])

        supabase_admin.table("tests").delete().eq("id", test_id).execute()
        
        if question_ids:
            supabase_admin.table("mock_questions").delete().in_("id", question_ids).execute()

        flash("Test and ALL related images permanently wiped from storage.", "success")
        if series_id:
            return redirect(url_for("admin.series_tests", series_id=series_id))
    except Exception as exc:
        flash(f"Could not completely delete test: {exc}", "error")
        
    return redirect(url_for("admin.test_series_list"))
