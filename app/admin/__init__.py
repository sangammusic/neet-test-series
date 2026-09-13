from flask import Blueprint

admin_bp = Blueprint(
    "admin",
    __name__,
    url_prefix="/admin",
    template_folder="../../templates/admin",
)

from app.admin import decorators  # noqa: E402,F401
from app.admin import routes  # noqa: E402,F401
