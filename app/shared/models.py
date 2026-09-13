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


# ---------- Added in Phase 3: chapter-wise practice + mock test feed ----------

def get_chapter_by_id(chapter_id):
    res = (
        supabase_public.table("chapters")
        .select("*, subjects(name, slug, streams(name, slug))")
        .eq("id", chapter_id).single().execute()
    )
    return res.data


def get_topics_for_chapter(chapter_id, is_pyq=False):
    res = (
        supabase_public.table("questions")
        .select("topic_name, is_premium")
        .eq("chapter_id", chapter_id)
        .eq("is_pyq", is_pyq)
        .execute()
    )
    topics = {}
    for row in res.data:
        name = row["topic_name"] or "General"
        entry = topics.setdefault(name, {"name": name, "is_premium": False})
        entry["is_premium"] = entry["is_premium"] or row["is_premium"]
    return list(topics.values())


def get_questions_for_practice(chapter_id, is_pyq=False, topic_name=None):
    q = supabase_public.table("questions").select("*").eq("chapter_id", chapter_id).eq("is_pyq", is_pyq)
    if topic_name:
        q = q.eq("topic_name", topic_name)
    return q.execute().data


def get_all_tests_for_stream(stream_id):
    res = (
        supabase_public.table("tests")
        .select("id, title, description, duration_minutes, total_marks, is_premium, price_inr")
        .eq("stream_id", stream_id)
        .eq("is_active", True)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data


def get_test_by_id(test_id):
    res = supabase_public.table("tests").select("*").eq("id", test_id).single().execute()
    return res.data


def get_test_syllabus(test_id):
    res = (
        supabase_public.table("test_questions")
        .select("questions(chapters(name, subjects(name)))")
        .eq("test_id", test_id)
        .execute()
    )
    syllabus = {}
    for row in res.data:
        q = row.get("questions")
        ch = q.get("chapters") if q else None
        if not ch:
            continue
        subj_name = ch["subjects"]["name"] if ch.get("subjects") else "General"
        syllabus.setdefault(subj_name, set()).add(ch["name"])
    return {subj: sorted(chs) for subj, chs in syllabus.items()}


def user_has_access_to_test(test, user_id):
    if not test.get("is_premium"):
        return True
    if not user_id:
        return False
    res = (
        supabase_public.table("test_access_grants")
        .select("test_id")
        .eq("test_id", test["id"])
        .eq("user_id", user_id)
        .execute()
    )
    return len(res.data) > 0
