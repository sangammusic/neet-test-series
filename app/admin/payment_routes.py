"""
Admin: payments panel  (/admin/payments)

  * revenue today / this month / all time, and how many payments are waiting
  * the queue of payments waiting for you, with the screenshot
  * Approve / Reject / Revoke buttons (same actions as the Telegram buttons)
  * one-click "Connect Telegram bot" and "Send test message"

Works even if Telegram is down -- the database is the source of truth.
Every route is protected by @admin_required (checks the role in the database
on every request).
"""
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import (
    Response, abort, flash, redirect, render_template, request, session, url_for,
)

from app.admin import admin_bp
from app.admin.decorators import admin_required
from app.extensions import supabase_admin
from app.shared import payments_core as pay

logger = logging.getLogger(__name__)

_RESULT_TEXT = {
    "not_found": "That payment no longer exists.",
    "bad_status": "That payment can't be changed now (it is {status}).",
    "user_deleted": "That student deleted their account, so access can't be granted.",
    "plan_expired": "That plan has already ended.",
    "server": "Database error. Please try again.",
}


def _back(default_url):
    """Back to the page the admin came from, but only if it is one of OUR pages."""
    ref = request.referrer or ""
    if ref and urlparse(ref).netloc.lower() == request.host.lower():
        return ref
    return default_url


def _reviewer():
    return f"web:{session.get('user_id')}"


def _explain(result):
    return _RESULT_TEXT.get(result.get("error"), _RESULT_TEXT["server"]).format(status=result.get("status", "closed"))


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sweep_expired():
    """Frees amounts held by abandoned orders (the student's page does this too, lazily)."""
    try:
        supabase_admin.table("orders").update({"status": "expired"}) \
            .eq("status", "created").lt("expires_at", _now_iso()).execute()
    except Exception as e:
        logger.warning(f"Could not expire stale orders: {e}")


@admin_bp.route("/payments")
@admin_required
def payments_panel():
    _sweep_expired()
    show = request.args.get("show", "pending")
    statuses = {
        "pending": ["submitted"],
        "approved": ["approved"],
        "problems": ["rejected", "cancelled", "expired"],
        "all": None,
    }.get(show, ["submitted"])
    if show not in ("pending", "approved", "problems", "all"):
        show = "pending"

    webhook = pay.tg_webhook_info() if pay.tg_token() else None      # works even before TG_ADMIN_CHAT_ID is set
    return render_template(
        "admin_payments.html",
        summary=pay.payments_summary(),
        orders=pay.list_orders_admin(statuses=statuses, limit=100),
        show=show,
        ready=pay.payments_ready(),
        upi_id=pay.upi_id(),
        tg_enabled=pay.tg_enabled(),
        tg_token_set=bool(pay.tg_token()),
        tg_admin_set=pay.tg_admin_id() is not None,
        webhook=webhook,
        webhook_target=(request.url_root.rstrip("/") + "/tg-webhook"),
    )


@admin_bp.route("/payments/<order_id>/approve", methods=["POST"])
@admin_required
def payments_approve(order_id):
    if not pay.is_uuid(order_id):
        abort(404)
    result = pay.approve_order(order_id, _reviewer())
    if result.get("ok"):
        flash("Approved. The student now has access.", "success")
        order = pay.get_order(order_id)
        if order:
            pay.tg_update_order_card(order, "✅ <b>APPROVED</b> (web panel)")
    else:
        flash(_explain(result), "error")
    return redirect(_back(url_for("admin.payments_panel")))


@admin_bp.route("/payments/<order_id>/reject", methods=["POST"])
@admin_required
def payments_reject(order_id):
    if not pay.is_uuid(order_id):
        abort(404)
    code = request.form.get("reason", "")
    label, text = pay.REJECT_REASONS.get(code, ("Rejected", "Payment could not be verified."))
    result = pay.reject_order(order_id, _reviewer(), text)
    if result.get("ok"):
        flash(f"Rejected ({label}). The student can see the reason.", "success")
        order = pay.get_order(order_id)
        if order:
            pay.tg_update_order_card(order, f"❌ <b>REJECTED</b> — {label} (web panel)")
    else:
        flash(_explain(result), "error")
    return redirect(_back(url_for("admin.payments_panel")))


@admin_bp.route("/payments/<order_id>/revoke", methods=["POST"])
@admin_required
def payments_revoke(order_id):
    if not pay.is_uuid(order_id):
        abort(404)
    result = pay.revoke_order(order_id, _reviewer(), "Access revoked by admin.")
    flash("Access revoked." if result.get("ok") else _explain(result),
          "success" if result.get("ok") else "error")
    return redirect(_back(url_for("admin.payments_panel", show="approved")))


@admin_bp.route("/payments/<order_id>/resend", methods=["POST"])
@admin_required
def payments_resend(order_id):
    """Sends the approval card to Telegram again (e.g. Telegram was down when the student submitted)."""
    if not pay.is_uuid(order_id):
        abort(404)
    order = pay.get_order(order_id)
    if not order or order.get("status") != "submitted":
        flash("Only payments that are waiting can be re-sent.", "error")
    elif not pay.tg_enabled():
        flash("Telegram isn't set up yet (TG_TOKEN / TG_ADMIN_CHAT_ID).", "error")
    else:
        sent = pay.tg_resend_existing(order)
        if sent:
            pay.save_telegram_refs(order_id, sent.get("message_id"), sent.get("file_id"))
            flash("Sent to your Telegram chat.", "success")
        else:
            flash("Telegram didn't accept the message. Check the bot token and chat id.", "error")
    return redirect(_back(url_for("admin.payments_panel")))


@admin_bp.route("/payments/<order_id>/screenshot")
@admin_required
def payments_screenshot(order_id):
    """Shows the payment screenshot (stored on Telegram) without ever exposing the bot token."""
    order = pay.get_order(order_id) if pay.is_uuid(order_id) else None
    if not order or not order.get("tg_file_id"):
        abort(404)
    data = pay.tg_fetch_file(order["tg_file_id"])
    if not data:
        abort(404)
    resp = Response(data, mimetype="image/jpeg")
    resp.headers["Content-Disposition"] = "inline; filename=payment.jpg"
    return resp


@admin_bp.route("/payments/telegram/connect", methods=["POST"])
@admin_required
def payments_tg_connect():
    if not pay.tg_token():
        flash("TG_TOKEN is not set on the server.", "error")
        return redirect(url_for("admin.payments_panel"))
    ok, url = pay.tg_set_webhook(request.url_root)
    flash(f"Telegram connected. Webhook: {url}" if ok else
          "Telegram refused the webhook. Check TG_TOKEN and that your site is reachable over https.",
          "success" if ok else "error")
    return redirect(url_for("admin.payments_panel"))


@admin_bp.route("/payments/telegram/test", methods=["POST"])
@admin_required
def payments_tg_test():
    if not pay.tg_enabled():
        flash("Set TG_TOKEN and TG_ADMIN_CHAT_ID first.", "error")
    elif pay.tg_send_text(pay.tg_admin_id(), "✅ Test message from SANGAM STUDY. Payment alerts will arrive here."):
        flash("Test message sent. Check your Telegram.", "success")
    else:
        flash("Telegram didn't accept the message. Open the bot and press Start, then try again.", "error")
    return redirect(url_for("admin.payments_panel"))
