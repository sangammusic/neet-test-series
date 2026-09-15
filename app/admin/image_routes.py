"""
Per-question image upload & Deep Storage Cleanup (Pillar 4).

Flow: bulk-paste JSON sets has_image=true on a question row (see
_validate_bulk_question / _validate_mock_question) but leaves
image_url null. The admin UI then shows an "Upload Image" prompt for
every such row. This file handles that upload:

    1. Admin picks a photo (from phone gallery, scanned PDF page
       exported as image, whatever) in the browser.
    2. compress_image() re-encodes it to a small JPEG — this is the
       part that keeps Supabase's 1GB free Storage from filling up
       after a few hundred uploads. A raw phone photo is often
       1-3MB; after this it's typically 30-100KB, so 1GB comfortably
       holds several thousand diagrams.
    3. The compressed bytes are pushed to the `question-images`
       Storage bucket.
    4. REPLACEMENT & REMOVAL CLEANUP (Zero-Kachra): Old images are 
       permanently deleted from the bucket when replaced or removed.

One route handles both tables (table_name is passed in the form) so
there's a single compression + storage path instead of duplicating
this logic per question type.
"""
import io
import uuid

from flask import request, jsonify
from PIL import Image, ImageOps

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.extensions import supabase_admin

BUCKET_NAME = "question-images"
ALLOWED_TABLES = {"questions", "mock_questions"}

# Compression targets. A NEET diagram only needs to be legible on a
# phone/laptop screen, not print-quality — 1000px on the long edge at
# JPEG quality 70 is plenty sharp for that and keeps files tiny.
MAX_DIMENSION = 1000
JPEG_QUALITY = 70
MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15MB raw upload cap, before compression


def _delete_image_from_storage(image_url):
    """
    Helper function for Pillar 4: Deep Storage Cleanup.
    Extracts the file path safely from the public image_url and permanently 
    deletes it from the Supabase Storage bucket to maintain zero-kachra.
    """
    if not image_url:
        return
    try:
        # URL format: https://[project_ref].supabase.co/storage/v1/object/public/question-images/[table]/[id]/[uuid].jpg
        bucket_prefix = f"/{BUCKET_NAME}/"
        if bucket_prefix in image_url:
            path = image_url.split(bucket_prefix)[1]
            supabase_admin.storage.from_(BUCKET_NAME).remove([path])
    except Exception as e:
        print(f"Failed to delete image from storage {image_url}: {e}")


def compress_image(file_bytes: bytes) -> bytes:
    """
    Resizes to at most MAX_DIMENSION on the long edge and re-encodes
    as JPEG at JPEG_QUALITY. Handles PNG-with-transparency and
    phone-camera EXIF rotation correctly (ImageOps.exif_transpose),
    since otherwise sideways/upside-down diagrams are a common
    phone-upload bug.
    """
    img = Image.open(io.BytesIO(file_bytes))
    img = ImageOps.exif_transpose(img)  # fix phone-camera rotation

    if img.mode in ("RGBA", "P"):
        # Flatten transparency onto white — JPEG has no alpha channel.
        # Without this, transparent PNG diagrams (common from
        # scanning apps) come out with black backgrounds.
        background = Image.new("RGB", img.size, (255, 255, 255))
        img = img.convert("RGBA")
        background.paste(img, mask=img.split()[3])
        img = background
    else:
        img = img.convert("RGB")

    img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()


@admin_bp.route("/questions/<table_name>/<question_id>/upload-image", methods=["POST"])
@admin_required
def question_upload_image(table_name, question_id):
    """
    AJAX endpoint — expects a multipart/form-data POST with one file
    field named "image". Returns JSON so the admin page's JS can swap
    the "Upload Image" prompt for a thumbnail without a full page reload.
    """
    if table_name not in ALLOWED_TABLES:
        return jsonify({"ok": False, "error": "invalid table"}), 400

    file = request.files.get("image")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    raw_bytes = file.read()
    if not raw_bytes:
        return jsonify({"ok": False, "error": "Empty file"}), 400
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        return jsonify({"ok": False, "error": "File too large (max 15MB before compression)"}), 400

    try:
        compressed = compress_image(raw_bytes)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Could not process image — is it a valid photo? ({exc})"}), 400

    # ZERO-KACHRA: Check if an old image exists and safely delete it from storage before replacing
    try:
        old_data = supabase_admin.table(table_name).select("image_url").eq("id", question_id).single().execute().data
        if old_data and old_data.get("image_url"):
            _delete_image_from_storage(old_data["image_url"])
    except Exception:
        pass  # If it fails to fetch, ignore safely and proceed with the new upload

    storage_path = f"{table_name}/{question_id}/{uuid.uuid4().hex}.jpg"

    try:
        supabase_admin.storage.from_(BUCKET_NAME).upload(
            storage_path,
            compressed,
            {"content-type": "image/jpeg"},
        )
    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": f"Upload to storage failed: {exc}. "
                     f"Make sure a public bucket named '{BUCKET_NAME}' exists.",
        }), 500

    public_url = supabase_admin.storage.from_(BUCKET_NAME).get_public_url(storage_path)

    try:
        supabase_admin.table(table_name).update({
            "image_url": public_url,
            "has_image": True,
        }).eq("id", question_id).execute()
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Image uploaded but saving the link failed: {exc}"}), 500

    return jsonify({
        "ok": True,
        "image_url": public_url,
        "compressed_size_kb": round(len(compressed) / 1024, 1),
    })


@admin_bp.route("/questions/<table_name>/<question_id>/remove-image", methods=["POST"])
@admin_required
def question_remove_image(table_name, question_id):
    """
    Pillar 4 Deep Storage Cleanup implementation.
    Clears image_url AND physically deletes the old file from Storage.
    Keeps has_image = true, since the question is still meant to have one.
    """
    if table_name not in ALLOWED_TABLES:
        return jsonify({"ok": False, "error": "invalid table"}), 400

    try:
        # Fetch the current image_url to permanently delete from storage
        old_data = supabase_admin.table(table_name).select("image_url").eq("id", question_id).single().execute().data
        if old_data and old_data.get("image_url"):
            _delete_image_from_storage(old_data["image_url"])

        # Update database to clear the URL
        supabase_admin.table(table_name).update({"image_url": None}).eq("id", question_id).execute()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True})
