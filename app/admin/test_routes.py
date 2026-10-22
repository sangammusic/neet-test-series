# STUDY SANGAM HUB v4.0 — Mock Test Series Admin (Resilient Chunk Upload, Auto Subject Sync, NTA Pattern & Zero Empty Shells)
import json
import logging
import re

from flask import render_template, request, redirect, url_for, flash, jsonify

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.admin.mock_question_routes import _validate_mock_question
from app.extensions import supabase_admin, cache
from app.shared import payments_core as pay
from app.shared.access import enrich_series_list, get_series_pricing, invalidate_series_plans_cache
from app.shared.latex_lint import lint_question_payload
from app.shared.models import compute_nta_subject_cap, compute_nta_test_meta

logger = logging.getLogger(__name__)

BUCKET_NAME = "question-images"


def _invalidate_test_questions_cache(test_id=None, series_id=None):
    """
    Clears all cached mock test and series lookups immediately when Admin creates,
    edits, or deletes a folder, test, or question so students never see stale data.
    """
    from app.shared.models import (
        get_mock_questions_for_test,
        get_mock_question_ids_for_test,
        invalidate_mock_series_cache,
    )
    if test_id:
        cache.delete_memoized(get_mock_questions_for_test, test_id)
        cache.delete_memoized(get_mock_question_ids_for_test, test_id)
    invalidate_mock_series_cache(series_id=series_id, test_id=test_id)
    invalidate_series_plans_cache()


def _delete_image_from_storage(image_url):
    if not image_url:
        return
    try:
        bucket_prefix = f"/{BUCKET_NAME}/"
        if bucket_prefix in image_url:
            path = image_url.split(bucket_prefix)[1].split("?")[0]
            supabase_admin.storage.from_(BUCKET_NAME).remove([path])
        elif "question-images/" in image_url:
            path = image_url.split("question-images/")[1].split("?")[0]
            supabase_admin.storage.from_(BUCKET_NAME).remove([path])
    except Exception as e:
        logger.error(f"Failed to delete image from storage {image_url}: {e}")


def _parse_series_paid_and_price(form):
    access_type = (form.get("access_type") or "").strip().lower()
    is_paid = (access_type == "paid") or (form.get("is_paid") in ("1", "true", "on"))
    try:
        price_inr = round(float(form.get("price_inr") or 0), 2)
    except (TypeError, ValueError):
        price_inr = 0.0
    if not is_paid or price_inr <= 0:
        return False, 0.0
    return True, price_inr


def _ensure_subjects_for_stream(stream_id, required_names=("Physics", "Chemistry", "Biology")):
    """
    Ensures standard subjects exist for `stream_id` in `subjects` so bulk question upload
    never fails due to a missing subject row.
    """
    try:
        rows = supabase_admin.table("subjects").select("id, name").eq("stream_id", stream_id).execute().data or []
    except Exception as e:
        logger.error(f"Could not read subjects for stream {stream_id}: {e}")
        rows = []

    name_to_id = {r["name"].strip().lower(): r["id"] for r in rows if r.get("name") and r.get("id")}
    for idx, req_name in enumerate(required_names, start=1):
        key = req_name.strip().lower()
        if key not in name_to_id:
            slug = re.sub(r"[^a-z0-9]+", "-", key).strip("-")
            try:
                created = supabase_admin.table("subjects").insert({
                    "stream_id": stream_id,
                    "name": req_name,
                    "slug": slug,
                    "display_order": idx,
                    "is_active": True,
                }).execute().data or []
                if created and created[0].get("id"):
                    name_to_id[key] = created[0]["id"]
            except Exception:
                # Re-query in case of slug conflict
                try:
                    existing = (
                        supabase_admin.table("subjects")
                        .select("id, name")
                        .eq("stream_id", stream_id)
                        .execute()
                        .data or []
                    )
                    for r in existing:
                        if r.get("name") and r.get("id"):
                            name_to_id[r["name"].strip().lower()] = r["id"]
                except Exception:
                    pass
    return name_to_id


def _get_uploaded_subject_counts(test_id):
    """Returns {'physics': int, 'chemistry': int, 'biology': int, 'total': int} for `test_id`."""
    counts = {"physics": 0, "chemistry": 0, "biology": 0, "total": 0}
    try:
        rows = (
            supabase_admin.table("mock_test_questions")
            .select("mock_question_id, mock_questions(subjects(name))")
            .eq("test_id", test_id)
            .execute()
            .data or []
        )
        for r in rows:
            counts["total"] += 1
            mq = r.get("mock_questions") or {}
            s_name = ((mq.get("subjects") or {}).get("name") or "").strip().lower()
            if "phys" in s_name:
                counts["physics"] += 1
            elif "chem" in s_name:
                counts["chemistry"] += 1
            elif "bio" in s_name or "botan" in s_name or "zool" in s_name:
                counts["biology"] += 1
    except Exception as e:
        logger.warning(f"Error counting uploaded questions for test {test_id}: {e}")
    return counts


def _insert_mock_questions_resilient(payloads):
    """
    Inserts `payloads` into `mock_questions` with automatic fallback if the live DB
    is missing optional columns or difficulty_levels rows.
    """
    if not payloads:
        return []
    try:
        res = supabase_admin.table("mock_questions").insert(payloads).execute()
        return [r["id"] for r in (res.data or []) if r.get("id")]
    except Exception as first_exc:
        err_str = str(first_exc).lower()
        logger.warning(f"Primary mock_questions insert raised ({first_exc}); retrying with resilient fallback.")

        # Strip optional columns that might not exist in an older DB schema or FK mismatch
        optional_cols = ("is_premium", "has_image", "pyq_year", "is_pyq", "difficulty_id", "image_url")
        cleaned = []
        for p in payloads:
            row = dict(p)
            if "difficulty" in err_str or "foreign key" in err_str:
                row["difficulty_id"] = None
            for col in optional_cols:
                if col in err_str:
                    row.pop(col, None)
            cleaned.append(row)

        try:
            res = supabase_admin.table("mock_questions").insert(cleaned).execute()
            return [r["id"] for r in (res.data or []) if r.get("id")]
        except Exception:
            # Minimal core schema fallback
            core_keys = (
                "stream_id", "subject_id", "topic_name", "question_text",
                "option_a", "option_b", "option_c", "option_d", "correct_option", "explanation",
            )
            minimal = [{k: p.get(k) for k in core_keys if k in p} for p in payloads]
            res = supabase_admin.table("mock_questions").insert(minimal).execute()
            return [r["id"] for r in (res.data or []) if r.get("id")]


# ==========================================
# LEVEL 1: TEST SERIES FOLDERS (FREE OR PAID + PRICE)
# ==========================================
@admin_bp.route("/test-series", methods=["GET", "POST"])
@admin_required
def test_series_list():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        stream_id = request.form.get("stream_id")
        is_paid, price_inr = _parse_series_paid_and_price(request.form)

        if not name or not stream_id:
            flash("Please select a Stream and enter a Test Series folder name.", "error")
        else:
            created_row = None
            try:
                try:
                    res = supabase_admin.table("mock_test_series").insert(
                        {"name": name, "stream_id": stream_id, "is_paid": is_paid, "price_inr": price_inr}
                    ).execute()
                    created_row = (res.data or [None])[0]
                except Exception:
                    res = supabase_admin.table("mock_test_series").insert(
                        {"name": name, "stream_id": stream_id}
                    ).execute()
                    created_row = (res.data or [None])[0]

                if created_row and created_row.get("id"):
                    pay.sync_series_plan(
                        series_id=created_row["id"],
                        name=name,
                        is_paid=is_paid,
                        price_inr=price_inr,
                        stream_id=stream_id,
                    )
                _invalidate_test_questions_cache(series_id=created_row.get("id") if created_row else None)
                label = f"Paid (₹{int(price_inr) if price_inr == int(price_inr) else price_inr})" if is_paid else "Free"
                flash(f"Test Series '{name}' created as {label}.", "success")
            except Exception as exc:
                logger.error(f"Test series creation failed: {exc}")
                flash("Could not create folder. Please try again.", "error")
        return redirect(url_for("admin.test_series_list"))

    streams = supabase_admin.table("streams").select("id, name").order("display_order").execute().data
    raw_series = (
        supabase_admin.table("mock_test_series")
        .select("*, streams(name)")
        .order("created_at", desc=True)
        .execute()
        .data or []
    )
    series = enrich_series_list(raw_series)
    return render_template("admin_test_folders.html", series=series, streams=streams)


@admin_bp.route("/test-series/<series_id>/edit", methods=["POST"])
@admin_required
def test_series_edit(series_id):
    new_name = request.form.get("name", "").strip()
    is_paid, price_inr = _parse_series_paid_and_price(request.form)

    if not new_name:
        flash("Folder name cannot be empty.", "error")
    else:
        try:
            try:
                supabase_admin.table("mock_test_series").update(
                    {"name": new_name, "is_paid": is_paid, "price_inr": price_inr}
                ).eq("id", series_id).execute()
            except Exception:
                supabase_admin.table("mock_test_series").update({"name": new_name}).eq("id", series_id).execute()

            folder_row = supabase_admin.table("mock_test_series").select("stream_id").eq("id", series_id).maybe_single().execute().data or {}
            pay.sync_series_plan(
                series_id=series_id,
                name=new_name,
                is_paid=is_paid,
                price_inr=price_inr,
                stream_id=folder_row.get("stream_id"),
            )
            try:
                supabase_admin.table("tests").update(
                    {"is_premium": is_paid, "price_inr": price_inr}
                ).eq("series_id", series_id).execute()
            except Exception:
                pass

            _invalidate_test_questions_cache(series_id=series_id)
            flash("Test Series updated successfully.", "success")
        except Exception as exc:
            logger.error(f"Folder update failed: {exc}")
            flash("Could not update folder.", "error")
            
    return redirect(url_for("admin.test_series_list"))


@admin_bp.route("/test-series/<series_id>/delete", methods=["POST"])
@admin_required
def test_series_delete(series_id):
    try:
        tests_in_series = supabase_admin.table("tests").select("id").eq("series_id", series_id).execute().data
        test_ids = [t["id"] for t in tests_in_series]

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

            unique_question_ids = list(set(question_ids))
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
                        if q.get("image_url") and f"/{BUCKET_NAME}/" in q["image_url"]:
                            clean_path = q["image_url"].split(f"/{BUCKET_NAME}/")[1].split("?")[0]
                            image_paths.append(clean_path)
                        elif q.get("image_url") and "question-images/" in q["image_url"]:
                            clean_path = q["image_url"].split("question-images/")[1].split("?")[0]
                            image_paths.append(clean_path)

            if image_paths:
                for i in range(0, len(image_paths), 50):
                    batch = image_paths[i:i+50]
                    try:
                        supabase_admin.storage.from_(BUCKET_NAME).remove(batch)
                    except Exception as e:
                        logger.error(f"Bucket batch delete raised an exception: {e}")

            if test_ids:
                for i in range(0, len(test_ids), 40):
                    chunk = test_ids[i:i+40]
                    supabase_admin.table("mock_test_questions").delete().in_("test_id", chunk).execute()
                    supabase_admin.table("tests").delete().in_("id", chunk).execute()
            
            if unique_question_ids:
                for i in range(0, len(unique_question_ids), 40):
                    chunk = unique_question_ids[i:i+40]
                    supabase_admin.table("mock_questions").delete().in_("id", chunk).execute()

        supabase_admin.table("mock_test_series").delete().eq("id", series_id).execute()
        try:
            from app.shared.access import series_plan_code
            supabase_admin.table("plans").update({"is_active": False}).eq("code", series_plan_code(series_id)).execute()
        except Exception:
            pass
        _invalidate_test_questions_cache(series_id=series_id)
        flash("Folder and ALL its tests, questions, and images were permanently wiped.", "success")
    except Exception as exc:
        logger.error(f"Folder deletion failed: {exc}")
        flash("Could not completely delete folder.", "error")

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

    pricing = get_series_pricing(series_data)
    series_data["is_paid"] = pricing["is_paid"]
    series_data["price_inr"] = pricing["price_inr"]

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        marks_per_question_raw = request.form.get("marks_per_question")
        stream_id = series_data.get("stream_id") or request.form.get("stream_id")
        category_name = request.form.get("category_name", "").strip()

        if not title or not marks_per_question_raw or not str(marks_per_question_raw).strip() or not category_name or not stream_id:
            flash("Please fill all the details, including Category and Marks per Question.", "error")
            return redirect(url_for("admin.series_tests", series_id=series_id))

        category_id = None
        try:
            existing_cats = supabase_admin.table("test_categories").select("id, name").eq("stream_id", stream_id).execute().data
            for cat in existing_cats:
                if cat["name"].strip().lower() == category_name.lower():
                    category_id = cat["id"]
                    break
            
            if not category_id:
                generated_slug = re.sub(r'[^a-z0-9]+', '-', category_name.lower()).strip('-')
                new_cat = supabase_admin.table("test_categories").insert({
                    "name": category_name,
                    "slug": generated_slug,
                    "stream_id": stream_id
                }).execute().data
                if new_cat:
                    category_id = new_cat[0]["id"]
        except Exception as exc:
            logger.error(f"Failed to auto-create category: {exc}")
            flash(f"Category Error: Could not save the category '{category_name}'.", "error")
            return redirect(url_for("admin.series_tests", series_id=series_id))

        negative_marking_raw = float(request.form.get("negative_marking") or 0)
        negative_marking = abs(negative_marking_raw)

        payload = {
            "series_id": series_id,
            "stream_id": stream_id,
            "category_id": category_id,
            "title": title,
            "description": request.form.get("description", "").strip() or None,
            "duration_minutes": int(request.form.get("duration_minutes") or 180),
            "total_marks": 0,
            "negative_marking": negative_marking,
            "is_premium": bool(pricing["is_paid"]),
            "price_inr": float(pricing["price_inr"] or 0),
        }
        
        try:
            result = supabase_admin.table("tests").insert(payload).execute()
            test_id = result.data[0]["id"]
            _invalidate_test_questions_cache(test_id=test_id, series_id=series_id)
            return redirect(url_for("admin.tests_upload", test_id=test_id, mpq=marks_per_question_raw))
        except Exception as exc:
            logger.error(f"Could not create test: {exc}")
            flash("Could not create test.", "error")
            return redirect(url_for("admin.series_tests", series_id=series_id))

    streams = supabase_admin.table("streams").select("id, name").order("display_order").execute().data

    cats_query = supabase_admin.table("test_categories").select("id, name, stream_id").order("display_order")
    if series_data.get("stream_id"):
        cats_query = cats_query.eq("stream_id", series_data["stream_id"])
    raw_categories = cats_query.execute().data
    seen_cats = set()
    categories = []
    for c in raw_categories:
        key = c["name"].strip().lower() 
        if key not in seen_cats:
            seen_cats.add(key)
            categories.append(c)

    raw_tests = (
        supabase_admin.table("tests")
        .select("id, title, description, duration_minutes, total_marks, is_premium, is_active, streams(name)")
        .eq("series_id", series_id)
        .order("created_at", desc=True)
        .execute()
        .data or []
    )

    # Auto-clean any abandoned 0-question test shells so empty tests never sit in the folder!
    tests = []
    discarded_empty = 0
    for t in raw_tests:
        q_counts = _get_uploaded_subject_counts(t["id"])
        if q_counts["total"] <= 0:
            try:
                supabase_admin.table("tests").delete().eq("id", t["id"]).execute()
                discarded_empty += 1
            except Exception:
                pass
            continue
        t_copy = dict(t)
        t_copy["question_count"] = q_counts["total"]
        t_copy["subject_counts"] = q_counts
        nta_meta = compute_nta_test_meta(
            {"Physics": q_counts["physics"], "Chemistry": q_counts["chemistry"], "Biology": q_counts["biology"]},
            stored_total_marks=t_copy.get("total_marks"),
        )
        t_copy["total_marks"] = nta_meta["effective_total_marks"]
        t_copy["attemptable_questions"] = nta_meta["total_attemptable"]
        tests.append(t_copy)

    if discarded_empty > 0:
        _invalidate_test_questions_cache(series_id=series_id)

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
    uploaded_counts = _get_uploaded_subject_counts(test_id)
    return render_template("admin_test_upload.html", test=test, mpq=mpq, uploaded_counts=uploaded_counts)


@admin_bp.route("/tests/<test_id>/discard-empty", methods=["POST", "GET"])
@admin_required
def tests_discard_empty(test_id):
    """
    If an Admin cancels or leaves the Upload screen when 0 questions were uploaded,
    deletes the empty test shell so it never appears in the Test Series folder.
    """
    test = supabase_admin.table("tests").select("id, series_id").eq("id", test_id).maybe_single().execute().data
    series_id = test.get("series_id") if test else None
    if test:
        counts = _get_uploaded_subject_counts(test_id)
        if counts["total"] <= 0:
            try:
                supabase_admin.table("tests").delete().eq("id", test_id).execute()
                _invalidate_test_questions_cache(test_id=test_id, series_id=series_id)
            except Exception as e:
                logger.warning(f"Could not discard empty test {test_id}: {e}")
    if request.is_json or request.args.get("ajax") == "1":
        return jsonify({"ok": True})
    if series_id:
        return redirect(url_for("admin.series_tests", series_id=series_id))
    return redirect(url_for("admin.test_series_list"))


@admin_bp.route("/tests/<test_id>/lock-targets", methods=["POST"])
@admin_required
def tests_lock_targets(test_id):
    data = request.get_json(silent=True) or {}
    total_marks = data.get("total_marks")
    targets = data.get("targets") or {}
    mpq = float(data.get("mpq") or 4)

    if targets and isinstance(targets, dict):
        meta = compute_nta_test_meta(
            {
                "Physics": int(targets.get("physics") or 0),
                "Chemistry": int(targets.get("chemistry") or 0),
                "Biology": int(targets.get("biology") or 0),
            },
            default_mpq=mpq,
        )
        total_marks = meta["effective_total_marks"]

    if total_marks is None:
        return jsonify({"ok": False, "error": "total_marks calculation missing."}), 400
        
    try:
        supabase_admin.table("tests").update({"total_marks": int(total_marks)}).eq("id", test_id).execute()
        _invalidate_test_questions_cache(test_id=test_id)
        return jsonify({"ok": True, "total_marks": int(total_marks)})
    except Exception as exc:
        logger.error(f"Target lock failed: {exc}")
        return jsonify({"ok": False, "error": "Database error locking targets."}), 500


@admin_bp.route("/tests/<test_id>/manage")
@admin_required
def tests_manage(test_id):
    test = supabase_admin.table("tests").select("*, mock_test_series(id, name)").eq("id", test_id).maybe_single().execute().data
    if not test:
        flash("Test not found.", "error")
        return redirect(url_for("admin.test_series_list"))

    mapped = (
        supabase_admin.table("mock_test_questions")
        .select(
            "mock_question_id, question_order, "
            "mock_questions(question_text, option_a, option_b, option_c, option_d, explanation, "
            "difficulty_id, is_premium, topic_name, is_pyq, pyq_year, has_image, image_url, "
            "correct_option, subjects(name))"
        )
        .eq("test_id", test_id)
        .order("question_order")
        .execute()
        .data or []
    )

    # If 0 questions were uploaded and Admin clicked DONE, redirect back to upload screen with a clear notice
    if len(mapped) == 0:
        flash("Please upload at least 1 question chunk before opening the Question Manager.", "error")
        return redirect(url_for("admin.tests_upload", test_id=test_id))

    edit_json_map = {}
    for m in mapped:
        mq = m.get("mock_questions") or {}
        edit_json_map[m["mock_question_id"]] = {
            "subject_name": (mq.get("subjects") or {}).get("name") or "",
            "topic_name": mq.get("topic_name") or "General",
            "question_text": mq.get("question_text") or "",
            "option_a": mq.get("option_a") or "",
            "option_b": mq.get("option_b") or "",
            "option_c": mq.get("option_c") or "",
            "option_d": mq.get("option_d") or "",
            "correct_option": (mq.get("correct_option") or "").strip(),
            "difficulty_id": mq.get("difficulty_id"),
            "is_pyq": bool(mq.get("is_pyq")),
            "pyq_year": mq.get("pyq_year"),
            "explanation": mq.get("explanation") or "",
            "has_image": bool(mq.get("has_image")),
            "image_url": mq.get("image_url") or "",
            "is_premium": bool(mq.get("is_premium")),
        }

    return render_template("admin_test_manage.html", test=test, mapped=mapped, edit_json_map=edit_json_map)


@admin_bp.route("/tests/<test_id>/questions/bulk-map", methods=["POST"])
@admin_required
def tests_bulk_map_questions(test_id):
    is_ajax = request.args.get("ajax") == "1" or request.is_json
    
    test = supabase_admin.table("tests").select("id, stream_id, total_marks, series_id").eq("id", test_id).maybe_single().execute().data
    if not test:
        if is_ajax:
            return jsonify({"ok": False, "error": "Test not found."}), 404
        flash("Test not found.", "error")
        return redirect(url_for("admin.test_series_list"))

    stream_id = test["stream_id"]
    default_subject = None
    
    if request.is_json:
        body = request.get_json(silent=True) or {}
        raw_json_data = body.get("bulk_json")
        default_subject = body.get("subject_name")
        if isinstance(raw_json_data, (list, dict)):
            parsed = raw_json_data
            raw_json = None
        else:
            raw_json = str(raw_json_data or "").strip()
            parsed = None
    else:
        raw_json = request.form.get("bulk_json", "").strip()
        default_subject = request.form.get("subject_name")
        parsed = None

    if parsed is None:
        if not raw_json:
            return jsonify({"ok": False, "error": "Paste a JSON array of questions."}), 400
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError as e:
            return jsonify({"ok": False, "error": f"Invalid JSON format ({e.msg} at line {e.lineno}). Check brackets and commas."}), 400

    # Support pasting a single JSON question object {...} or {"questions": [...]}
    if isinstance(parsed, dict):
        if isinstance(parsed.get("questions"), list):
            parsed = parsed["questions"]
        else:
            parsed = [parsed]

    if not isinstance(parsed, list) or len(parsed) == 0:
        return jsonify({"ok": False, "error": "Empty or invalid JSON array."}), 400

    # Ensure standard subjects exist for this stream in Supabase
    subject_name_to_id = _ensure_subjects_for_stream(stream_id)
    try:
        difficulty_ids = {d["id"] for d in (supabase_admin.table("difficulty_levels").select("id").execute().data or [])}
    except Exception:
        difficulty_ids = {1, 2, 3}

    valid_payloads, errors = [], []
    for i, raw in enumerate(parsed, start=1):
        payload, error = _validate_mock_question(
            raw, subject_name_to_id, difficulty_ids, stream_id, default_subject_name=default_subject
        )
        if error:
            errors.append(f"Row {i}: {error}")
        else:
            valid_payloads.append(payload)

    if not valid_payloads:
        err_summary = "; ".join(errors[:3]) if errors else "No valid questions found in pasted JSON."
        return jsonify({
            "ok": False,
            "inserted": 0,
            "inserted_ids": [],
            "errors": errors,
            "error": err_summary,
        }), 400

    inserted_ids = []
    try:
        inserted_ids = _insert_mock_questions_resilient(valid_payloads)
    except Exception as exc:
        logger.error(f"Bulk Insert Error: {exc}")
        return jsonify({
            "ok": False,
            "error": f"Database insert error: {str(exc)[:160]}",
            "errors": errors,
        }), 500

    if inserted_ids:
        try:
            existing_orders = (
                supabase_admin.table("mock_test_questions")
                .select("question_order")
                .eq("test_id", test_id)
                .execute()
                .data or []
            )
            start_order = max([row.get("question_order", 0) for row in existing_orders], default=-1) + 1 if existing_orders else 0
            
            mapping_rows = [
                {"test_id": test_id, "mock_question_id": qid, "question_order": start_order + i}
                for i, qid in enumerate(inserted_ids)
            ]
            try:
                supabase_admin.table("mock_test_questions").upsert(
                    mapping_rows, on_conflict="test_id,mock_question_id"
                ).execute()
            except Exception:
                supabase_admin.table("mock_test_questions").insert(mapping_rows).execute()
        except Exception as exc:
            logger.error(f"Test Mapping Error: {exc}")
            return jsonify({"ok": False, "error": f"Mapping questions to test failed: {str(exc)[:140]}"}), 500

        # Auto-sync NTA total_marks based on uploaded subject counts
        try:
            counts = _get_uploaded_subject_counts(test_id)
            nta_meta = compute_nta_test_meta(
                {"Physics": counts["physics"], "Chemistry": counts["chemistry"], "Biology": counts["biology"]},
                stored_total_marks=test.get("total_marks"),
            )
            if int(test.get("total_marks") or 0) <= 0 or nta_meta["optional_skips"] > 0:
                supabase_admin.table("tests").update(
                    {"total_marks": nta_meta["effective_total_marks"]}
                ).eq("id", test_id).execute()
        except Exception as e:
            logger.warning(f"Could not auto-sync test total_marks: {e}")

        _invalidate_test_questions_cache(test_id=test_id, series_id=test.get("series_id"))

    latex_warnings = [
        {"row": i + 1, "fields": w}
        for i, p in enumerate(valid_payloads)
        for w in [lint_question_payload(p)]
        if w
    ]
    return jsonify({
        "ok": bool(inserted_ids),
        "inserted": len(inserted_ids),
        "inserted_ids": inserted_ids,
        "errors": errors,
        "error": ("; ".join(errors[:3]) if (errors and not inserted_ids) else None),
        "latex_warnings": latex_warnings,
    })


@admin_bp.route("/tests/<test_id>/questions/undo-chunk", methods=["POST"])
@admin_required
def tests_undo_chunk(test_id):
    payload = request.json
    if not payload or not payload.get("question_ids"):
        return jsonify({"ok": False, "error": "No question IDs provided for undo."}), 400
    
    question_ids = payload["question_ids"]
    
    try:
        questions = supabase_admin.table("mock_questions").select("image_url").in_("id", question_ids).execute().data
        image_paths = []
        for q in questions:
            if q.get("image_url") and f"/{BUCKET_NAME}/" in q["image_url"]:
                clean_path = q["image_url"].split(f"/{BUCKET_NAME}/")[1].split("?")[0]
                image_paths.append(clean_path)
            elif q.get("image_url") and "question-images/" in q["image_url"]:
                clean_path = q["image_url"].split("question-images/")[1].split("?")[0]
                image_paths.append(clean_path)
        
        if image_paths:
            for i in range(0, len(image_paths), 50):
                try:
                    supabase_admin.storage.from_(BUCKET_NAME).remove(image_paths[i:i+50])
                except Exception as e:
                    logger.warning(f"Undo chunk image wipe failed: {e}")
        
        supabase_admin.table("mock_test_questions").delete().eq("test_id", test_id).in_("mock_question_id", question_ids).execute()
        supabase_admin.table("mock_questions").delete().in_("id", question_ids).execute()

        _invalidate_test_questions_cache(test_id=test_id)
        return jsonify({"ok": True, "message": "Chunk successfully undone and wiped."})
    except Exception as exc:
        logger.error(f"Undo error: {exc}")
        return jsonify({"ok": False, "error": "Database error during undo."}), 500


@admin_bp.route("/tests/<test_id>/questions/<question_id>/remove", methods=["POST"])
@admin_required
def tests_remove_question(test_id, question_id):
    try:
        q = supabase_admin.table("mock_questions").select("image_url").eq("id", question_id).maybe_single().execute().data
        if q and q.get("image_url"):
            _delete_image_from_storage(q["image_url"])

        supabase_admin.table("mock_test_questions").delete().eq("test_id", test_id).eq("mock_question_id", question_id).execute()
        supabase_admin.table("mock_questions").delete().eq("id", question_id).execute()

        _invalidate_test_questions_cache(test_id=test_id)

        is_ajax = request.args.get("ajax") == "1" or request.is_json
        if is_ajax:
            return jsonify({"ok": True})
        flash("Question removed from test.", "success")
        return redirect(url_for("admin.tests_manage", test_id=test_id))
    except Exception as exc:
        logger.error(f"Remove question error: {exc}")
        is_ajax = request.args.get("ajax") == "1" or request.is_json
        if is_ajax:
            return jsonify({"ok": False, "error": "Failed to remove question"}), 500
        flash("Failed to remove question.", "error")
        return redirect(url_for("admin.tests_manage", test_id=test_id))


@admin_bp.route("/tests/<test_id>/questions/<question_id>/edit", methods=["POST"])
@admin_required
def tests_edit_question(test_id, question_id):
    raw = request.json
    if not raw:
        return jsonify({"ok": False, "error": "No JSON payload received"}), 400

    test = supabase_admin.table("tests").select("stream_id").eq("id", test_id).maybe_single().execute().data
    if not test:
        return jsonify({"ok": False, "error": "Test not found."}), 404

    stream_id = test["stream_id"]
    subject_name_to_id = _ensure_subjects_for_stream(stream_id)
    try:
        difficulty_ids = {d["id"] for d in (supabase_admin.table("difficulty_levels").select("id").execute().data or [])}
    except Exception:
        difficulty_ids = {1, 2, 3}

    payload, error = _validate_mock_question(raw, subject_name_to_id, difficulty_ids, stream_id)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    try:
        old_q = supabase_admin.table("mock_questions").select("image_url, has_image").eq("id", question_id).maybe_single().execute().data
        if not old_q:
            return jsonify({"ok": False, "error": "Question not found (it may have been deleted). Reload the page."}), 404
        if not (raw.get("image_url") or "").strip():
            payload["image_url"] = old_q.get("image_url")
        if payload.get("image_url"):
            payload["has_image"] = bool(raw.get("has_image", True))

        supabase_admin.table("mock_questions").update(payload).eq("id", question_id).execute()
        _invalidate_test_questions_cache(test_id=test_id)
        return jsonify({"ok": True, "has_image": payload.get("has_image"), "image_url": payload.get("image_url"),
                        "latex_warnings": lint_question_payload(payload)})
    except Exception as exc:
        logger.error(f"JSON Edit error: {exc}")
        return jsonify({"ok": False, "error": "Failed to update JSON in database."}), 500


@admin_bp.route("/questions/mock_questions/<question_id>/toggle-image", methods=["POST"])
@admin_required
def toggle_question_image(question_id):
    data = request.json
    if not data or "has_image" not in data:
        return jsonify({"ok": False, "error": "Missing has_image payload"}), 400
    
    has_image = bool(data["has_image"])
    
    try:
        supabase_admin.table("mock_questions").update({"has_image": has_image}).eq("id", question_id).execute()
        return jsonify({"ok": True})
    except Exception as exc:
        logger.error(f"Toggle image error: {exc}")
        return jsonify({"ok": False, "error": "Database error saving image state."}), 500


@admin_bp.route("/questions/mock_questions/<question_id>/change-answer", methods=["POST"])
@admin_required
def change_question_answer(question_id):
    data = request.json
    if not data or not data.get("correct_option"):
        return jsonify({"ok": False, "error": "Missing correct_option payload"}), 400
    
    correct_option = str(data["correct_option"]).strip().upper()
    
    if correct_option not in ("A", "B", "C", "D"):
        return jsonify({"ok": False, "error": "Invalid option"}), 400
    
    try:
        supabase_admin.table("mock_questions").update({"correct_option": correct_option}).eq("id", question_id).execute()
        return jsonify({"ok": True})
    except Exception as exc:
        logger.error(f"Change answer error: {exc}")
        return jsonify({"ok": False, "error": "Database error updating answer."}), 500


@admin_bp.route("/tests/<test_id>/delete", methods=["POST"])
@admin_required
def tests_delete(test_id):
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
                    if q.get("image_url") and f"/{BUCKET_NAME}/" in q["image_url"]:
                        clean_path = q["image_url"].split(f"/{BUCKET_NAME}/")[1].split("?")[0]
                        image_paths.append(clean_path)
                    elif q.get("image_url") and "question-images/" in q["image_url"]:
                        clean_path = q["image_url"].split("question-images/")[1].split("?")[0]
                        image_paths.append(clean_path)

        if image_paths:
            for i in range(0, len(image_paths), 50):
                batch = image_paths[i:i+50]
                try:
                    supabase_admin.storage.from_(BUCKET_NAME).remove(batch)
                except Exception as e:
                    logger.error(f"Bucket batch delete raised an exception: {e}")

        supabase_admin.table("mock_test_questions").delete().eq("test_id", test_id).execute()
        supabase_admin.table("tests").delete().eq("id", test_id).execute()

        if question_ids:
            for i in range(0, len(question_ids), 40):
                chunk = question_ids[i:i+40]
                supabase_admin.table("mock_questions").delete().in_("id", chunk).execute()

        _invalidate_test_questions_cache(test_id=test_id, series_id=series_id)
        flash("Test and ALL related questions/images permanently wiped from DB and Storage.", "success")
        if series_id:
            return redirect(url_for("admin.series_tests", series_id=series_id))
    except Exception as exc:
        logger.error(f"Test delete error: {exc}")
        flash("Could not completely delete test.", "error")

    return redirect(url_for("admin.test_series_list"))
