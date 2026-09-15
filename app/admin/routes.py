"""
Per-question image upload & Deep Storage Cleanup (Pillar 4).

Flow: bulk-paste JSON sets has_image=true on a question row but leaves
image_url null. The admin UI then shows an "Upload Image" prompt. 

This file handles:
    1. Compressing phone/large photos into tiny JPEGs (30-100KB).
    2. Uploading to Supabase Storage.
    3. REPLACEMENT & REMOVAL CLEANUP (Zero-Kachra): Old images are 
       permanently deleted from the bucket when replaced or removed.
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
    Extracts the file path from the public image_url and permanently 
    deletes it from the Supabase Storage bucket to maintain zero-kachra.
    """
    if not image_url:
        return
    try:
        # URL format: https://[project_ref].supabase.co/storage/v1/object/public/question-images/...
        if f"{BUCKET_NAME}/" in image_url:
            path = image_url.split(f"{BUCKET_NAME}/")[1]
            supabase_admin.storage.from_(BUCKET_NAME).remove([path])
    except Exception as e:
        print(f"Failed to delete image from storage {image_url}: {e}")


def compress_image(file_bytes: bytes) -> bytes:
    """
    Resizes to at most MAX_DIMENSION on the long edge and re-encodes
    as JPEG at JPEG_QUALITY. Handles PNG-with-transparency and
    phone-camera EXIF rotation correctly.
    """
    img = Image.open(io.BytesIO(file_bytes))
    img = ImageOps.exif_transpose(img)  # fix phone-camera rotation

    if img.mode in ("RGBA", "P"):
        # Flatten transparency onto white — JPEG has no alpha channel.
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
    AJAX endpoint for uploading/replacing images.
    Pillar 4 Implementation: If the user is REPLACING an image, we 
    must fetch the old image_url and delete it from storage first.
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

    # ZERO-KACHRA: Check if an old image exists and delete it before replacing
    try:
        old_data = supabase_admin.table(table_name).select("image_url").eq("id", question_id).single().execute().data
        if old_data and old_data.get("image_url"):
            _delete_image_from_storage(old_data["image_url"])
    except Exception:
        pass  # If it fails to fetch, ignore and proceed with the new upload

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
            "error": f"Upload to storage failed: {exc}. Make sure a public bucket named '{BUCKET_NAME}' exists.",
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
    Pillar 4 Implementation: Deep Storage Cleanup.
    Instead of just setting image_url to None, this physically deletes 
    the file from the Supabase Storage bucket. Zero orphan files left behind.
    """
    if table_name not in ALLOWED_TABLES:
        return jsonify({"ok": False, "error": "invalid table"}), 400

    try:
        # Fetch the current image_url to delete from storage
        old_data = supabase_admin.table(table_name).select("image_url").eq("id", question_id).single().execute().data
        if old_data and old_data.get("image_url"):
            _delete_image_from_storage(old_data["image_url"])

        # Update database to clear the URL but keep has_image=True so the upload box still shows
        supabase_admin.table(table_name).update({"image_url": None}).eq("id", question_id).execute()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True})
