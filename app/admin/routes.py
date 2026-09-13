from flask import render_template

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.extensions import supabase_admin


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

    counts = {
        "streams": _count("streams"),
        "subjects": _count("subjects"),
        "chapters": _count("chapters"),
        "questions": _count("questions"),
        "tests": _count("tests"),
    }

    return render_template("admin_dashboard.html", counts=counts)
