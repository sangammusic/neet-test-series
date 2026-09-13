"""
Thin data-access functions shared by user and admin blueprints.

Rule of thumb: if BOTH admin and user need to *read* the same data
shape (e.g. "list active streams"), it belongs here. Write operations
that only admin performs (create/edit/delete streams etc.) live in
app/admin/*_routes.py instead, using supabase_admin directly — no
need to funnel every admin write through this shared file.
"""
from app.extensions import supabase_public


def get_active_streams():
    """Public catalog read — used on the stream-selection page."""
    res = (
        supabase_public.table("streams")
        .select("id, name, slug, description, icon_url")
        .eq("is_active", True)
        .order("display_order")
        .execute()
    )
    return res.data


def get_stream_by_slug(slug: str):
    res = (
        supabase_public.table("streams")
        .select("*")
        .eq("slug", slug)
        .eq("is_active", True)
        .single()
        .execute()
    )
    return res.data


def get_subjects_for_stream(stream_id: str):
    res = (
        supabase_public.table("subjects")
        .select("id, name, slug")
        .eq("stream_id", stream_id)
        .eq("is_active", True)
        .order("display_order")
        .execute()
    )
    return res.data


def get_chapters_for_subject(subject_id: str):
    res = (
        supabase_public.table("chapters")
        .select("id, name, slug")
        .eq("subject_id", subject_id)
        .eq("is_active", True)
        .order("display_order")
        .execute()
    )
    return res.data


def get_test_categories_for_stream(stream_id: str):
    res = (
        supabase_public.table("test_categories")
        .select("id, name, slug, is_premium")
        .eq("stream_id", stream_id)
        .order("display_order")
        .execute()
    )
    return res.data


def get_difficulty_levels():
    res = (
        supabase_public.table("difficulty_levels")
        .select("id, name")
        .order("display_order")
        .execute()
    )
    return res.data
