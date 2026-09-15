"""
Pillar 1: Mock Test Series (The Folder System).

Flow:
1. Level 1: Admin creates "Test Series Folders" (mock_test_series).
2. Level 2: Admin drills into a folder to see/create Tests.
   - BUG FIXED: Duplicate categories filtered.
   - VALIDATION: total_marks removed from initial creation. Marks_per_question handles it.
3. Level 3: Admin manages questions for a test via:
    A. 25-Chunk Upload (3-Tabs) with STRICT SANGAM STUDY HUB RULES (45-45-90 for 720 marks).
    B. Live JSON Edit: Seamless replacement of JSON + Image toggle (No Duplicates).
    C. Undo Chunk: Temporary history destruction with Deep Storage Cleanup.
    D. Deep Storage Permanent Cleanup (Fixed Chunking Bug & Trailing '?' Bug for 100% Wipe).
"""
import json
import logging

from flask import render_template, request, redirect, url_for, flash, jsonify

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.admin.mock_question_routes import _validate_mock_question
from app.extensions import supabase_admin

logger = logging.getLogger(__name__)

BUCKET_NAME = "question-images"


def _delete_image_from_storage(image_url):
    """
    Fallback Helper function for single image deletions.
    SANGAM FIX: Strips any trailing '?' from the URL to prevent silent Storage API failures.
    """
    if not image_url:
        return
    try:
        if "question-images/" in image_url:
            # Extract path and strip any query parameters (like ?t=...)
            path = image_url.split("question-images/")[1].split("?")[0]
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


@admin_bp.route("/test-series/<series_id>/edit", methods=["POST"])
@admin_required
def test_series_edit(series_id):
    """
    Renames an existing Test Series Folder.
    """
    new_name = request.form.get("name", "").strip()
    if not new_name:
        flash("Folder name cannot be empty.", "error")
    else:
        try:
            supabase_admin.table("mock_test_series").update({"name": new_name}).eq("id", series_id).execute()
            flash("Folder renamed successfully.", "success")
        except Exception as exc:
            flash(f"Could not rename folder: {exc}", "error")
            
    return redirect(url_for("admin.test_series_list"))


@admin_bp.route("/test-series/<series_id>/delete", methods=["POST"])
@admin_required
def test_series_delete(series_id):
    """
    Deep Storage Permanent Cleanup for an ENTIRE FOLDER.
    SANGAM FIX: Implemented Batch Chunking to prevent 414 URI Too Long error, 
    ensuring absolutely NO ghost data remains in the DB or Storage.

    DIAGNOSTIC LOGGING & PATH FIX (Sep 2026): Stripping trailing '?' from paths
    so Supabase Storage doesn't silently fail to delete the images.
    """
    try:
        tests_in_series = supabase_admin.table("tests").select("id").eq("series_id", series_id).execute().data
        test_ids = [t["id"] for t in tests_in_series]
        logger.info(f"[test_series_delete:{series_id}] tests in folder: {len(test_ids)}")

        question_ids = []
        image_paths = []

        if test_ids:
            for tid in test_ids:
                mapped = (
                    supabase_admin.table("mock_test_questions")
                    .select("mock_question_id")
                    .eq("test_id", tid)
                    .execute()
                    .data
                )
                for row in mapped:
                    qid = row.get("mock_question_id")
                    if qid:
                        question_ids.append(qid)

            # ROOT-CAUSE FIX: fetch image_url directly from mock_questions by id
            unique_question_ids = list(set(question_ids))
            logger.info(f"[test_series_delete:{series_id}] unique question_ids: {len(unique_question_ids)}")
            if unique_question_ids:
                for i in range(0, len(unique_question_ids), 100):
                    chunk = unique_question_ids[i:i + 100]
                    rows = (
                        supabase_admin.table("mock_questions")
                        .select("image_url")
                        .in_("id", chunk)
                        .execute()
                        .data
                    )
                    for q in rows:
                        if q.get("image_url") and "question-images/" in q["image_url"]:
                            # SANGAM FIX: Split by '?' to remove trailing query strings
                            clean_path = q["image_url"].split("question-images/")[1].split("?")[0]
                            image_paths.append(clean_path)

            logger.info(f"[test_series_delete:{series_id}] image_paths to remove ({len(image_paths)}): {image_paths}")

            # 1. Chunked Image Deletion from Bucket (Max 50 per batch)
            if image_paths:
                for i in range(0, len(image_paths), 50):
                    batch = image_paths[i:i+50]
                    try:
                        result = supabase_admin.storage.from_(BUCKET_NAME).remove(batch)
                        removed_names = [r.get("name") for r in (result or []) if isinstance(r, dict)]
                        logger.info(
                            f"[test_series_delete:{series_id}] storage.remove asked for {len(batch)} paths, "
                            f"Supabase confirmed {len(removed_names)} removed: {removed_names}"
                        )
                        if len(removed_names) < len(batch):
                            logger.warning(
                                f"[test_series_delete:{series_id}] MISMATCH — "
                                f"{len(batch) - len(removed_names)} path(s) did not delete. Asked for: {batch}"
                            )
                    except Exception as e:
                        logger.error(f"[test_series_delete:{series_id}] Bucket batch delete raised an exception: {e}")

            # 2. Chunked Test Mapping Deletion (Explicitly prevent FK blocks)
            for tid in test_ids:
                supabase_admin.table("mock_test_questions").delete().eq("test_id", tid).execute()
            
            # 3. Chunked Test Deletion
            for tid in test_ids:
                supabase_admin.table("tests").delete().eq("id", tid).execute()
            
            # 4. Chunked Question Deletion (Batches of 40 prevent API crash)
            if question_ids:
                unique_question_ids = list(set(question_ids))
                for i in range(0, len(unique_question_ids), 40):
                    chunk = unique_question_ids[i:i+40]
                    supabase_admin.table("mock_questions").delete().in_("id", chunk).execute()

        # 5. Finally, delete the folder itself
        supabase_admin.table("mock_test_series").delete().eq("id", series_id).execute()

        logger.info(f"[test_series_delete:{series_id}] DB cleanup complete.")
        flash("Folder and ALL its tests, questions, and images were permanently wiped.", "success")
    except Exception as exc:
        logger.error(f"[test_series_delete:{series_id}] Delete failed with exception: {exc}")
        flash(f"Could not completely delete folder: {exc}", "error")

    return redirect(url_for("admin.test_series_list"))


# ==========================================
# LEVEL 2: TESTS INSIDE A FOLDER
# ==========================================
@admin_bp.route("/test-series/<series_id>/tests", methods=["GET", "POST"])
@admin_required
def series_tests(series_id):
    series_data = supabase_admin.table("mock_test_series").select("*").eq("id", series_id).maybe_single().execute().data
    if not series_data:
        flash("Test Series Folder not found.", "error")
        return redirect(url_for("admin.test_series_list"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        marks_per_question_raw = request.form.get("marks_per_question")

        if not title or not marks_per_question_raw or not str(marks_per_question_raw).strip():
            flash("Please fill all the details, including Marks per Question.", "error")
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
            "total_marks": 0, # Auto-calculated from Page 2
            "negative_marking": negative_marking,
            "is_premium": request.form.get("is_premium") == "on",
            "price_inr": float(request.form.get("price_inr") or 0),
        }
        try:
            result = supabase_admin.table("tests").insert(payload).execute()
            test_id = result.data[0]["id"]
            flash("Test created — you can now set targets and start uploading questions.", "success")
            
            return redirect(url_for("admin.tests_upload", test_id=test_id, mpq=marks_per_question_raw))
        except Exception as exc:
            flash(f"Could not create test: {exc}", "error")
            return redirect(url_for("admin.series_tests", series_id=series_id))

    streams = supabase_admin.table("streams").select("id, name").order("display_order").execute().data
    
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
# LEVEL 3: THE 3-PAGE ISOLATED SYSTEM
# ==========================================

@admin_bp.route("/tests/<test_id>/upload")
@admin_required
def tests_upload(test_id):
    test = supabase_admin.table("tests").select("*, mock_test_series(id, name)").eq("id", test_id).maybe_single().execute().data
    if not test:
        flash("Test not found.", "error")
        return redirect(url_for("admin.test_series_list"))

    mpq = request.args.get("mpq", 4)
    return render_template("admin_test_upload.html", test=test, mpq=mpq)


@admin_bp.route("/tests/<test_id>/lock-targets", methods=["POST"])
@admin_required
def tests_lock_targets(test_id):
    """
    Auto Calculates and Locks Total Marks dynamically.
    """
    data = request.json
    total_marks = data.get("total_marks")
    
    if total_marks is None:
        return jsonify({"ok": False, "error": "total_marks calculation missing."}), 400
        
    try:
        supabase_admin.table("tests").update({"total_marks": int(total_marks)}).eq("id", test_id).execute()
        return jsonify({"ok": True, "total_marks": total_marks})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@admin_bp.route("/tests/<test_id>/manage")
@admin_required
def tests_manage(test_id):
    test = supabase_admin.table("tests").select("*, mock_test_series(id, name)").eq("id", test_id).maybe_single().execute().data
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
    
    test = supabase_admin.table("tests").select("id, stream_id, total_marks").eq("id", test_id).maybe_single().execute().data
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
            if is_ajax: return jsonify({"ok": False, "error": f"SANGAM STUDY HUB RULES: Uploading exceeds the 45 Physics limit. (Total would be {phys_count})"}), 400
        if chem_count > 45:
            if is_ajax: return jsonify({"ok": False, "error": f"SANGAM STUDY HUB RULES: Uploading exceeds the 45 Chemistry limit. (Total would be {chem_count})"}), 400
        if bio_count > 90:
            if is_ajax: return jsonify({"ok": False, "error": f"SANGAM STUDY HUB RULES: Uploading exceeds the 90 Biology limit. (Total would be {bio_count})"}), 400

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

    return jsonify({"ok": True if inserted_ids else False, "inserted": len(inserted_ids), "inserted_ids": inserted_ids, "errors": errors})


@admin_bp.route("/tests/<test_id>/questions/undo-chunk", methods=["POST"])
@admin_required
def tests_undo_chunk(test_id):
    """
    PERMANENT DELETION ROUTE. 
    Accepts an array of question_ids and wipes them from mapping, question bank, 
    and deletes any attached images from the Storage Bucket in batches.
    SANGAM FIX: Added trailing '?' stripper logic here too.
    """
    payload = request.json
    if not payload or not payload.get("question_ids"):
        return jsonify({"ok": False, "error": "No question IDs provided for undo."}), 400
    
    question_ids = payload["question_ids"]
    
    try:
        # 1. Fetch images to delete from Storage
        questions = supabase_admin.table("mock_questions").select("image_url").in_("id", question_ids).execute().data
        image_paths = []
        for q in questions:
            if q.get("image_url") and "question-images/" in q["image_url"]:
                # Remove trailing '?' before adding to deletion list
                clean_path = q["image_url"].split("question-images/")[1].split("?")[0]
                image_paths.append(clean_path)
        
        # Batch delete from storage
        if image_paths:
            for i in range(0, len(image_paths), 50):
                try:
                    supabase_admin.storage.from_(BUCKET_NAME).remove(image_paths[i:i+50])
                except Exception:
                    pass
        
        # 2. Delete test mapping
        supabase_admin.table("mock_test_questions").delete().eq("test_id", test_id).in_("mock_question_id", question_ids).execute()
        
        # 3. Delete from actual question bank
        supabase_admin.table("mock_questions").delete().in_("id", question_ids).execute()
        
        return jsonify({"ok": True, "message": "Chunk successfully undone and wiped."})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@admin_bp.route("/tests/<test_id>/questions/<question_id>/edit", methods=["POST"])
@admin_required
def tests_edit_question(test_id, question_id):
    """
    Live JSON Edit: Fully Replaces/Updates the question text, options directly.
    """
    raw = request.json
    if not raw:
        return jsonify({"ok": False, "error": "No JSON payload received"}), 400

    test = supabase_admin.table("tests").select("stream_id").eq("id", test_id).maybe_single().execute().data
    if not test:
        return jsonify({"ok": False, "error": "Test not found."}), 404

    stream_id = test["stream_id"]
    subjects = supabase_admin.table("subjects").select("id, name").eq("stream_id", stream_id).execute().data
    subject_name_to_id = {s["name"].strip().lower(): s["id"] for s in subjects}
    difficulty_ids = {d["id"] for d in supabase_admin.table("difficulty_levels").select("id").execute().data}

    payload, error = _validate_mock_question(raw, subject_name_to_id, difficulty_ids, stream_id)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    old_q = supabase_admin.table("mock_questions").select("image_url, has_image").eq("id", question_id).maybe_single().execute().data
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
    SANGAM FIX: Implemented Chunking to prevent 414 URI Too Long errors.
    SANGAM FIX 2: Strips trailing '?' from paths to prevent silent Storage API failures.
    """
    try:
        test_info = supabase_admin.table("tests").select("series_id").eq("id", test_id).maybe_single().execute().data
        series_id = test_info.get("series_id") if test_info else None

        mapped = (
            supabase_admin.table("mock_test_questions")
            .select("mock_question_id")
            .eq("test_id", test_id)
            .execute()
            .data
        )
        question_ids = list({row["mock_question_id"] for row in mapped if row.get("mock_question_id")})
        logger.info(f"[tests_delete:{test_id}] mapping rows found: {len(mapped)}, unique question_ids: {len(question_ids)}")

        image_paths = []
        if question_ids:
            for i in range(0, len(question_ids), 100):
                chunk = question_ids[i:i + 100]
                rows = (
                    supabase_admin.table("mock_questions")
                    .select("image_url")
                    .in_("id", chunk)
                    .execute()
                    .data
                )
                for q in rows:
                    if q.get("image_url") and "question-images/" in q["image_url"]:
                        # Remove trailing '?' so Supabase doesn't fail silently
                        clean_path = q["image_url"].split("question-images/")[1].split("?")[0]
                        image_paths.append(clean_path)

        logger.info(f"[tests_delete:{test_id}] image_paths to remove from storage ({len(image_paths)}): {image_paths}")

        # 1. Batch delete images from bucket safely in chunks of 50
        if image_paths:
            for i in range(0, len(image_paths), 50):
                batch = image_paths[i:i+50]
                try:
                    result = supabase_admin.storage.from_(BUCKET_NAME).remove(batch)
                    removed_names = [r.get("name") for r in (result or []) if isinstance(r, dict)]
                    logger.info(
                        f"[tests_delete:{test_id}] storage.remove asked for {len(batch)} paths, "
                        f"Supabase confirmed {len(removed_names)} removed: {removed_names}"
                    )
                    if len(removed_names) < len(batch):
                        logger.warning(
                            f"[tests_delete:{test_id}] MISMATCH — {len(batch) - len(removed_names)} "
                            f"path(s) did not delete. Asked for: {batch}"
                        )
                except Exception as e:
                    logger.error(f"[tests_delete:{test_id}] Bucket batch delete raised an exception: {e}")

        # 2. Delete test mapping explicitly to prevent FK constraint blocks
        supabase_admin.table("mock_test_questions").delete().eq("test_id", test_id).execute()

        # 3. Delete the Test itself
        supabase_admin.table("tests").delete().eq("id", test_id).execute()

        # 4. Safely delete questions in chunks of 40 to avoid 414 URI Too Long error
        if question_ids:
            for i in range(0, len(question_ids), 40):
                chunk = question_ids[i:i+40]
                supabase_admin.table("mock_questions").delete().in_("id", chunk).execute()

        logger.info(f"[tests_delete:{test_id}] DB cleanup complete — test, mapping, and {len(question_ids)} question(s) deleted.")
        flash("Test and ALL related questions/images permanently wiped from DB and Storage.", "success")
        if series_id:
            return redirect(url_for("admin.series_tests", series_id=series_id))
    except Exception as exc:
        logger.error(f"[tests_delete:{test_id}] Delete failed with exception: {exc}")
        flash(f"Could not completely delete test: {exc}", "error")

    return redirect(url_for("admin.test_series_list"))
