from flask import render_template, request, redirect, url_for, flash

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.extensions import supabase_admin
from app.admin.subject_routes import _slugify


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
    current = supabase_admin.table("chapters").select("is_active").eq("id", chapter_id).single().execute().data
    if current:
        supabase_admin.table("chapters").update({"is_active": not current["is_active"]}).eq("id", chapter_id).execute()
    return redirect(url_for("admin.chapters_list"))


@admin_bp.route("/chapters/<chapter_id>/delete", methods=["POST"])
@admin_required
def chapters_delete(chapter_id):
    try:
        supabase_admin.table("chapters").delete().eq("id", chapter_id).execute()
        flash("Chapter deleted.", "success")
    except Exception as exc:
        flash(f"Couldn't delete — it likely still has questions attached. ({exc})", "error")
    return redirect(url_for("admin.chapters_list"))
