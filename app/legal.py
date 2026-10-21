# SANGAM STUDY v2.2 — Handcrafted UI Build (2026-10-21)
"""
Public legal pages: /terms  /privacy  /refund-policy

The wording lives in templates/legal/*.html -- edit the text THERE (plain HTML).
These pages need no login, so they can be linked from the payment page and the footer.
"""
from flask import Blueprint, render_template

legal_bp = Blueprint("legal", __name__)


@legal_bp.route("/terms")
def terms():
    return render_template("legal/terms.html")


@legal_bp.route("/privacy")
def privacy():
    return render_template("legal/privacy.html")


@legal_bp.route("/refund-policy")
def refund_policy():
    return render_template("legal/refund.html")
  
