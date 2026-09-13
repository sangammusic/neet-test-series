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
    display_order = request.form.get("display_order") or 0

    if not stream_id or not name:
        flash("Stream and name are required.", "error")
        return redirect(url_for("admin.subjects_list"))

    try:
        supabase_admin.table("subjects").insert({
            "stream_id": stream_id,
            "name": name,
            "slug": slug,
            "display_order": int(display_order),
        }).execute()
        flash(f'Subject "{name}" created.', "success")
    except Exception as exc:
        flash(f"Could not create subject — check the slug isn't already used for this stream. ({exc})", "error")

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
    try:
        supabase_admin.table("subjects").delete().eq("id", subject_id).execute()
        flash("Subject deleted.", "success")
    except Exception as exc:
        flash(f"Couldn't delete — it likely still has chapters attached. Delete those first. ({exc})", "error")
    return redirect(url_for("admin.subjects_list"))
