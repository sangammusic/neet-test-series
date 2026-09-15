from flask import render_template, request, redirect, url_for, flash

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.extensions import supabase_admin


def _slugify(text: str) -> str:
    return "-".join(text.strip().lower().split())


@admin_bp.route("/subjects")
@admin_required
def subjects_list():
    streams = supabase_admin.table("streams").select("id, name, slug").order("display_order").execute().data
    subjects = (
        supabase_admin.table("subjects")
        .select("id, name, slug, display_order, is_active, stream_id, streams(name)")
        .order("stream_id")
        .order("display_order")
        .execute()
        .data
    )
    return render_template("admin_subjects.html", streams=streams, subjects=subjects)


@admin_bp.route("/subjects/create", methods=["POST"])
@admin_required
def subjects_create():
    stream_id = request.form.get("stream_id")
    name = request.form.get("name", "").strip()
    slug = request.form.get("slug", "").strip() or _slugify(name)
    display_order_raw = request.form.get("display_order")

    if not stream_id or not name:
        flash("Stream and Subject Name are strictly required.", "error")
        return redirect(url_for("admin.subjects_list"))

    try:
        display_order = int(display_order_raw) if display_order_raw else 0
    except ValueError:
        display_order = 0

    try:
        supabase_admin.table("subjects").insert({
            "stream_id": stream_id,
            "name": name,
            "slug": slug,
            "display_order": display_order,
        }).execute()
        flash(f'Subject "{name}" successfully created.', "success")
    except Exception as exc:
        flash(f"Could not create subject. The slug might already be in use for this stream. ({exc})", "error")

    return redirect(url_for("admin.subjects_list"))


@admin_bp.route("/subjects/<subject_id>/toggle", methods=["POST"])
@admin_required
def subjects_toggle(subject_id):
    current = supabase_admin.table("subjects").select("is_active").eq("id", subject_id).single().execute().data
    if current:
        supabase_admin.table("subjects").update({"is_active": not current["is_active"]}).eq("id", subject_id).execute()
    return redirect(url_for("admin.subjects_list"))


@admin_bp.route("/subjects/<subject_id>/delete", methods=["POST"])
@admin_required
def subjects_delete(subject_id):
    """
    Pillar 3 Implementation: Strict Hierarchy Check.
    Instead of blindly attempting to delete and failing on a raw DB constraint, 
    we explicitly check if chapters exist under this subject to give a clear, 
    user-friendly block message to the admin. Prevent accidental massive data loss.
    """
    try:
        # 1. Check for attached chapters first (Strict Hierarchy Enforcement)
        chapters = supabase_admin.table("chapters").select("id").eq("subject_id", subject_id).execute().data
        
        if chapters and len(chapters) > 0:
            flash(f"BLOCKED: This subject contains {len(chapters)} chapter(s). You must safely delete those chapters first to prevent massive accidental data loss.", "error")
            return redirect(url_for("admin.subjects_list"))

        # 2. Safe to delete if no chapters exist
        supabase_admin.table("subjects").delete().eq("id", subject_id).execute()
        flash("Subject deleted successfully.", "success")
    except Exception as exc:
        flash(f"Could not delete subject: {exc}", "error")
        
    return redirect(url_for("admin.subjects_list"))
