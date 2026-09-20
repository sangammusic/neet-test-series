from flask import render_template, request, redirect, url_for, flash

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.extensions import supabase_admin
from app.admin.subject_routes import _slugify

# Required for Pillar 4: Deep Storage Cleanup
BUCKET_NAME = "question-images"


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
        if "question-images/" in image_url:
            path = image_url.split("question-images/")[1]
            supabase_admin.storage.from_(BUCKET_NAME).remove([path])
    except Exception as e:
        print(f"Failed to delete image from storage {image_url}: {e}")


@admin_bp.route("/chapters")
@admin_required
def chapters_list():
    subjects = (
        supabase_admin.table("subjects")
        .select("id, name, streams(name)")
        .eq("is_active", True)
        .order("stream_id")
        .execute()
        .data
    )
    chapters = (
        supabase_admin.table("chapters")
        .select("id, name, slug, display_order, is_active, subject_id, subjects(name, streams(name))")
        .order("subject_id")
        .order("display_order")
        .execute()
        .data
    )
    return render_template("admin_chapters.html", subjects=subjects, chapters=chapters)


@admin_bp.route("/chapters/create", methods=["POST"])
@admin_required
def chapters_create():
    subject_id = request.form.get("subject_id")
    name = request.form.get("name", "").strip()
    slug = request.form.get("slug", "").strip() or _slugify(name)
    display_order = request.form.get("display_order") or 0

    if not subject_id or not name:
        flash("Subject and name are required.", "error")
        return redirect(url_for("admin.chapters_list"))

    try:
        supabase_admin.table("chapters").insert({
            "subject_id": subject_id,
            "name": name,
            "slug": slug,
            "display_order": int(display_order),
        }).execute()
        flash(f'Chapter "{name}" created.', "success")
    except Exception as exc:
        flash(f"Could not create chapter — check the slug isn't already used in this subject. ({exc})", "error")

    return redirect(url_for("admin.chapters_list"))


@admin_bp.route("/chapters/<chapter_id>/toggle", methods=["POST"])
@admin_required
def chapters_toggle(chapter_id):
    current = supabase_admin.table("chapters").select("is_active").eq("id", chapter_id).maybe_single().execute().data
    if current:
        supabase_admin.table("chapters").update({"is_active": not current["is_active"]}).eq("id", chapter_id).execute()
    return redirect(url_for("admin.chapters_list"))


@admin_bp.route("/chapters/<chapter_id>/delete", methods=["POST"])
@admin_required
def chapters_delete(chapter_id):
    """
    Pillar 3 & 4 Implementation: Force Delete & Deep Storage Cleanup.
    Since the admin has accepted the 'Heavy Material Warning' on the frontend,
    this route cleanly wipes all uploaded images from the bucket, deletes the 
    attached questions, and finally deletes the chapter itself.
    """
    try:
        # 1. Fetch all questions under this chapter to check for attached images
        questions = supabase_admin.table("questions").select("image_url").eq("chapter_id", chapter_id).execute().data
        
        # 2. Delete physical image files from Storage Bucket (Zero Kachra)
        for q in questions:
            if q.get("image_url"):
                _delete_image_from_storage(q["image_url"])
        
        # 3. Explicitly delete questions to bypass Foreign Key constraint block
        if questions:
            supabase_admin.table("questions").delete().eq("chapter_id", chapter_id).execute()
            
        # 4. Finally, delete the chapter itself
        supabase_admin.table("chapters").delete().eq("id", chapter_id).execute()
        
        flash("Chapter, along with all its uploaded questions and images, was permanently deleted.", "success")
    except Exception as exc:
        flash(f"Could not completely delete chapter: {exc}", "error")
        
    return redirect(url_for("admin.chapters_list"))
