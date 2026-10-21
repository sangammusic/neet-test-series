# SANGAM STUDY v2.2 — Handcrafted UI Build (2026-10-21)
from flask import Blueprint

user_bp = Blueprint(
    "user",
    __name__,
    template_folder="../../templates/user",
)

from app.user import routes  # noqa: E402,F401
