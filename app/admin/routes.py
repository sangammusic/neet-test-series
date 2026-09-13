from flask import render_template

from app.admin import admin_bp
from app.admin.decorators import admin_required


@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    """
    Landing page after admin login. Stream/Question/Test/Transaction
    CRUD routes (stream_routes.py, question_routes.py, etc. from the
    architecture doc) plug in as separate files registered the same
    way — this file stays as just the dashboard + shared admin nav.
    """
    return render_template("dashboard.html")
