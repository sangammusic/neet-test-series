from flask import Blueprint

admin_bp = Blueprint(
    "admin",
    __name__,
    url_prefix="/admin",
    template_folder="../../templates/admin",
)

from app.admin import decorators  # noqa: E402,F401
from app.admin import routes  # noqa: E402,F401
from app.admin import subject_routes  # noqa: E402,F401
from app.admin import chapter_routes  # noqa: E402,F401
from app.admin import question_routes  # noqa: E402,F401
from app.admin import test_routes  # noqa: E402,F401
