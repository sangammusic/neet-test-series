from flask import render_template

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.extensions import supabase_admin
from app.admin.image_routes import BUCKET_NAME


@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    """
    Landing page after admin login. Shows quick content counts so it's
    obvious at a glance what's populated and what still needs data.
    """
    def _count(table):
        res = supabase_admin.table(table).select("id", count="exact").execute()
        return res.count or 0

    def _count_pending_images(table):
        res = (
            supabase_admin.table(table)
            .select("id", count="exact")
            .eq("has_image", True)
            .is_("image_url", "null")
            .execute()
        )
        return res.count or 0

    counts = {
        "streams": _count("streams"),
        "subjects": _count("subjects"),
        "chapters": _count("chapters"),
        "questions": _count("questions"),
        "tests": _count("tests"),
    }

    # Storage usage: every uploaded image is compressed to ~30-100KB
    # (see app/admin/image_routes.py compress_image()), so counting
    # objects and estimating at a conservative 100KB/image gives a
    # useful "how close to the 1GB free-tier limit am I" signal
    # without needing to sum actual byte sizes via the Storage API.
    uploaded_count = 0
    try:
        # list() on the bucket root only returns top-level "folders"
        # (questions/, mock_questions/) not a recursive file count, so
        # walk one level down per table prefix instead.
        for prefix in ("questions", "mock_questions"):
            folders = supabase_admin.storage.from_(BUCKET_NAME).list(prefix)
            for folder in folders or []:
                files = supabase_admin.storage.from_(BUCKET_NAME).list(f"{prefix}/{folder['name']}")
                uploaded_count += len(files or [])
    except Exception:
        # Bucket may not exist yet if the one-time Storage setup step
        # (sql/migration_question_images.sql) hasn't been done — don't
        # let that break the whole dashboard.
        uploaded_count = None

    storage_info = {
        "uploaded_count": uploaded_count,
        "estimated_mb": round((uploaded_count or 0) * 100 / 1024, 1) if uploaded_count is not None else None,
        "pending_questions": _count_pending_images("questions"),
        "pending_mock_questions": _count_pending_images("mock_questions"),
    }

    return render_template("admin_dashboard.html", counts=counts, storage_info=storage_info)
