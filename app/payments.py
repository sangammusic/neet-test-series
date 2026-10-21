# STUDY SANGAM HUB v3.0 — Student Per-Test-Series Checkout + Telegram Bot Webhook (/setqr + Approve/Disapprove)
"""
payments.py -- student-facing payment pages for Paid Test Series + Telegram Bot webhook.
"""
import hmac
import logging
import re
from datetime import datetime, timezone

from flask import (
    Blueprint, Response, abort, flash, jsonify, redirect, render_template,
    render_template_string, request, url_for,
)
from jinja2 import TemplateNotFound

from app.shared import payments_core as pay
from app.shared.access import (
    DEFAULT_SEASON_PASS_PLAN,
    extract_series_id_from_code,
    format_ist_date,
    get_active_plans,
    get_active_subscription,
    parse_ts,
    premium_status,
)
from app.shared.security import minutes_text, rl_hit
from app.shared.utils import current_user_id, is_logged_in

logger = logging.getLogger(__name__)

payments_bp = Blueprint("payments", __name__, template_folder="../templates/payments")


def _render_pay_template(name, **ctx):
    for candidate in (f"payments/{name}", name):
        try:
            return render_template(candidate, **ctx)
        except TemplateNotFound:
            continue
    return render_template(name, **ctx)


def _login_gate():
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    return None


# ======================================================================
# Student Pages: Purchase Test Series, Checkout, QR Image, My Purchases
# ======================================================================

@payments_bp.route("/pay")
def plans():
    gate = _login_gate()
    if gate:
        return gate
    user_id = current_user_id()
    db_plans = get_active_plans()
    return _render_pay_template(
        "plans.html",
        plans=db_plans if db_plans else [dict(DEFAULT_SEASON_PASS_PLAN)],
        db_plans_ready=bool(db_plans),
        ps=premium_status(),
        open_order=pay.get_open_order_for_user(user_id),
        ready=pay.payments_ready(),
    )


@payments_bp.route("/pay/series/<series_id>", methods=["GET", "POST"])
def buy_series(series_id):
    """
    Direct Per-Test-Series Purchase endpoint.
    When a student clicks 'Purchase' on a Paid Test Series (e.g., Achiever Test Series — ₹99),
    this creates/reuses an order for that specific Test Series and opens the QR + UPI ID + 'I Have Paid' page.
    """
    gate = _login_gate()
    if gate:
        return gate
    user_id = current_user_id()

    if not pay.payments_ready():
        flash("Payments aren't available yet. Please contact support.", "error")
        return redirect(url_for("user.my_dashboard"))

    wait = rl_hit("pay-start", user_id, 15, 3600)
    if wait:
        flash(f"Too many attempts. Please try again in {minutes_text(wait)}.", "error")
        return redirect(url_for("user.my_dashboard"))

    result = pay.create_series_order(user_id, series_id)
    if result.get("ok") and pay.is_uuid(result.get("order_id")):
        return redirect(url_for("payments.checkout", order_id=result["order_id"]))

    if result.get("error") == "already_active":
        flash("This Test Series is already unlocked for your account!", "success")
        return redirect(url_for("user.my_dashboard"))

    flash("We couldn't start the payment for this Test Series right now. Please try again.", "error")
    return redirect(url_for("user.my_dashboard"))


@payments_bp.route("/pay/start", methods=["POST"])
def start():
    gate = _login_gate()
    if gate:
        return gate
    user_id = current_user_id()

    if not pay.payments_ready():
        flash("Payments aren't available yet. Please try again later or contact support.", "error")
        return redirect(url_for("payments.plans"))

    wait = rl_hit("pay-start", user_id, 10, 3600)
    if wait:
        flash(f"Too many attempts. Please try again in {minutes_text(wait)}.", "error")
        return redirect(url_for("payments.plans"))

    series_id = (request.form.get("series_id") or "").strip()
    if series_id:
        result = pay.create_series_order(user_id, series_id)
    else:
        plan_code = (request.form.get("plan_code") or "season_pass_2027").strip()
        result = pay.create_order(user_id, plan_code)

    if result.get("ok") and pay.is_uuid(result.get("order_id")):
        return redirect(url_for("payments.checkout", order_id=result["order_id"]))

    messages = {
        "already_active": "You already have an active plan, so there's nothing to pay for.",
        "plan_not_found": "That plan isn't available right now.",
        "busy": "A lot of people are paying at this moment. Please try again in a few minutes.",
    }
    flash(messages.get(result.get("error"), "We couldn't start the payment. Please try again."), "error")
    return redirect(url_for("payments.plans"))


@payments_bp.route("/pay/qr-image")
def bot_qr_image():
    """Serves the custom QR code image uploaded by Admin via Telegram Bot `/setqr`."""
    if not is_logged_in():
        abort(401)
    raw = pay.get_bot_qr_image_bytes()
    if not raw:
        abort(404)
    resp = Response(raw, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


@payments_bp.route("/pay/preview")
def preview_checkout():
    gate = _login_gate()
    if gate:
        return gate
    plan = (get_active_plans(include_fallback=True) or [dict(DEFAULT_SEASON_PASS_PLAN)])[0]
    demo_order_id = "00000000-0000-4000-8000-000000000299"
    demo_upi = pay.upi_id() if pay.upi_id() else "yourname@upi"
    base_amt = float(plan.get("price_inr") or 99)
    payable_amt = round(base_amt + 0.37, 2)
    order = {
        "id": demo_order_id,
        "base_amount": base_amt,
        "payable_amount": payable_amt,
        "status": "created",
        "plans": plan,
    }
    upi_link = f"upi://pay?pa={demo_upi}&pn=Study%20Sangam%20Hub&am={payable_amt:.2f}&cu=INR&tn=SSP-PREVIEW"
    qr = pay.qr_svg(upi_link)
    bot_qr_cfg = pay.get_bot_qr_config()
    static_qr = url_for("payments.bot_qr_image") if bot_qr_cfg.get("has_qr") else None
    valid_until = format_ist_date(plan.get("valid_until"))
    cfg = {
        "state": "pay",
        "status": "created",
        "secondsLeft": 2700,
        "submitUrl": url_for("payments.submit", order_id=demo_order_id),
        "statusUrl": url_for("payments.status_json", order_id=demo_order_id),
        "upiLink": upi_link,
        "isPreview": True,
    }
    return _render_pay_template(
        "checkout.html",
        order=order, plan=plan, state="pay", cfg=cfg,
        upi_id=demo_upi, upi_link=upi_link, qr_svg=qr, static_qr=static_qr,
        has_bot_qr=bool(bot_qr_cfg.get("has_qr")),
        ref="PREVIEW1", valid_until=valid_until, is_preview=True,
        ready=pay.payments_ready(),
    )


def _view_state(order, seconds_left):
    status = order.get("status")
    if status == "created" and seconds_left > 0:
        return "pay"
    if status in ("created", "expired"):
        return "late"
    if status == "submitted":
        return "waiting"
    if status == "approved":
        return "approved"
    if status == "rejected":
        return "rejected"
    return "ended"


@payments_bp.route("/pay/order/<order_id>")
def checkout(order_id):
    gate = _login_gate()
    if gate:
        return gate
    user_id = current_user_id()

    order = pay.get_order_for_user(order_id, user_id)
    if not order:
        abort(404)

    expires = parse_ts(order.get("expires_at"))
    seconds_left = 0
    if expires:
        seconds_left = max(0, int((expires - datetime.now(timezone.utc)).total_seconds()))
    state = _view_state(order, seconds_left)
    plan = order.get("plans") if isinstance(order.get("plans"), dict) else {}

    upi_link = qr = static_qr = None
    bot_qr_cfg = pay.get_bot_qr_config()
    if state == "pay":
        upi_link = pay.build_upi_link(order["payable_amount"], order["id"])
        qr = pay.qr_svg(upi_link)
        if bot_qr_cfg.get("has_qr"):
            static_qr = url_for("payments.bot_qr_image")
        elif pay.static_qr_file():
            static_qr = url_for("static", filename=pay.static_qr_file())

    valid_until = None
    if state == "approved":
        sub = get_active_subscription(user_id)
        valid_until = format_ist_date(sub.get("expires_at")) if sub else format_ist_date(plan.get("valid_until"))
    elif plan.get("valid_until"):
        valid_until = format_ist_date(plan.get("valid_until"))

    cfg = {
        "state": state,
        "status": order.get("status"),
        "secondsLeft": seconds_left,
        "submitUrl": url_for("payments.submit", order_id=order["id"]),
        "statusUrl": url_for("payments.status_json", order_id=order["id"]),
        "upiLink": upi_link,
    }
    return _render_pay_template(
        "checkout.html",
        order=order, plan=plan, state=state, cfg=cfg,
        upi_id=pay.upi_id(), upi_link=upi_link, qr_svg=qr, static_qr=static_qr,
        has_bot_qr=bool(bot_qr_cfg.get("has_qr")),
        ref=pay.short_ref(order["id"]), valid_until=valid_until,
    )


@payments_bp.route("/pay/order/<order_id>/status.json")
def status_json(order_id):
    if not is_logged_in():
        return jsonify({"ok": False, "error": "Please log in again."}), 401
    order = pay.get_order_for_user(order_id, current_user_id())
    if not order:
        return jsonify({"ok": False, "error": "Not found."}), 404
    return jsonify({
        "ok": True,
        "status": order.get("status"),
        "reject_reason": order.get("reject_reason"),
    })


@payments_bp.route("/pay/history")
def history():
    gate = _login_gate()
    if gate:
        return gate
    uid = current_user_id()
    orders = pay.list_orders_for_user(uid) or []
    ps = premium_status()
    return _render_pay_template(
        "history.html",
        orders=orders,
        ps=ps,
    )


# ======================================================================
# Submit Payment ("I Have Paid" / UTR + optional screenshot)
# ======================================================================

@payments_bp.route("/pay/order/<order_id>/submit", methods=["POST"])
def submit(order_id):
    if not is_logged_in():
        return jsonify({"ok": False, "error": "Please log in again."}), 401
    user_id = current_user_id()

    wait = rl_hit("pay-submit", user_id, 12, 3600)
    if wait:
        return jsonify({"ok": False, "error": f"Too many attempts. Please try again in {minutes_text(wait)}."}), 429

    order = pay.get_order_for_user(order_id, user_id)
    if not order:
        return jsonify({"ok": False, "error": pay.SUBMIT_ERRORS["not_found"]}), 404

    if request.content_length is None or request.content_length > pay.MAX_UPLOAD_BYTES + 256 * 1024:
        return jsonify({"ok": False, "error": pay.SCREENSHOT_MESSAGES["too_large"]}), 413

    raw_utr = (request.form.get("utr") or "").strip()
    i_have_paid_mode = (request.form.get("i_have_paid") == "1")
    utr = re.sub(r"\D", "", raw_utr)

    if i_have_paid_mode and not utr:
        utr = pay.generate_auto_utr(order_id)
    elif len(utr) != 12:
        return jsonify({
            "ok": False,
            "error": "Enter the 12-digit UTR (UPI reference number) shown in your payment app.",
        }), 400

    photo_bytes = None
    shot = request.files.get("screenshot")
    if shot and shot.filename:
        try:
            photo_bytes = pay.sanitize_screenshot(shot.read(pay.MAX_UPLOAD_BYTES + 1))
        except pay.ScreenshotError as e:
            return jsonify({"ok": False, "error": pay.SCREENSHOT_MESSAGES.get(str(e), pay.SCREENSHOT_MESSAGES["unreadable"])}), 400

    result = pay.submit_order(order_id, user_id, utr)
    if not result.get("ok"):
        code = result.get("error")
        http = 409 if code in ("utr_used", "amount_reassigned", "already_submitted") else 400
        if code == "server":
            http = 500
        return jsonify({"ok": False, "error": pay.SUBMIT_ERRORS.get(code, pay.SUBMIT_ERRORS["server"])}), http

    next_url = url_for("payments.checkout", order_id=order_id)
    if result.get("already"):
        return jsonify({"ok": True, "redirect": next_url})

    fresh = pay.get_order(order_id)
    sent = pay.tg_send_order(fresh, photo_bytes) if fresh else None
    if sent:
        pay.save_telegram_refs(order_id, sent.get("message_id"), sent.get("file_id"))
    else:
        logger.warning("Order %s submitted; Telegram notification skipped or not configured", order_id)
    return jsonify({"ok": True, "redirect": next_url})


# ======================================================================
# Telegram Bot Webhook (POST /tg-webhook)
# Supports:
#   * /start, /id
#   * /setqr (+ photo upload to set website payment QR code dynamically)
#   * /pending
#   * Inline Approve (ord:a:<id>) and Disapprove/Reject (ord:d:<id>, ord:r:<id>, ord:x:<id>)
# ======================================================================

_APPROVE_ERRORS = {
    "not_found": "Order not found.",
    "bad_status": "This order can't be approved now (it is {status}).",
    "user_deleted": "That student deleted their account.",
    "plan_expired": "This plan has already ended.",
    "server": "Server error. Try again.",
}
_REJECT_ERRORS = {
    "not_found": "Order not found.",
    "bad_status": "This order can't be rejected now (it is {status}).",
    "server": "Server error. Try again.",
}


@payments_bp.route("/tg-webhook", methods=["POST"])
def tg_webhook():
    if not pay.tg_token():
        abort(404)
    sent = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(sent.encode("utf-8"), pay.tg_webhook_secret().encode("utf-8")):
        abort(403)

    update = request.get_json(silent=True) or {}
    try:
        _process_update(update)
    except Exception:
        logger.exception("Could not process a Telegram update")
    return "ok", 200


def _process_update(update):
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        return _handle_callback(callback)
    message = update.get("message")
    if isinstance(message, dict):
        return _handle_message(message)
    return None


def _answer(callback_id, text, alert=False):
    pay.tg_call("answerCallbackQuery", json_body={
        "callback_query_id": callback_id, "text": text[:190], "show_alert": alert,
    })


def _error_text(table, result):
    template = table.get(result.get("error"), table["server"])
    return template.format(status=result.get("status", "closed"))


def _handle_callback(cq):
    callback_id = cq.get("id")
    from_id = (cq.get("from") or {}).get("id")

    if not pay.is_tg_admin(from_id):
        _answer(callback_id, "You are not allowed to do this.", alert=True)
        return

    parts = (cq.get("data") or "").split(":")
    if len(parts) < 3 or parts[0] != "ord" or not pay.is_uuid(parts[2]):
        _answer(callback_id, "Unknown action.")
        return
    action, order_id = parts[1], parts[2]

    message = cq.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    is_photo = bool(message.get("photo"))

    if action in ("r", "b"):
        markup = pay.reject_keyboard(order_id) if action == "r" else pay.approval_keyboard(order_id)
        pay.tg_call("editMessageReplyMarkup", json_body={
            "chat_id": chat_id, "message_id": message_id, "reply_markup": markup,
        })
        _answer(callback_id, "Pick a reason" if action == "r" else "Cancelled")
        return

    reviewer = f"telegram:{from_id}"
    if action == "a":
        result = pay.approve_order(order_id, reviewer)
        decision = "✅ <b>APPROVED</b>"
        failure = _error_text(_APPROVE_ERRORS, result)
    elif action in ("x", "d"):
        code = parts[3] if len(parts) > 3 else ""
        label, reason_text = pay.REJECT_REASONS.get(code, ("Disapproved", "Payment could not be verified."))
        result = pay.reject_order(order_id, reviewer, reason_text)
        decision = f"❌ <b>REJECTED</b> — {label}"
        failure = _error_text(_REJECT_ERRORS, result)
    else:
        _answer(callback_id, "Unknown action.")
        return

    if not result.get("ok"):
        _answer(callback_id, failure, alert=True)
        return

    _answer(callback_id, "Done ✅" if action == "a" else "Disapproved ❌")
    order = pay.get_order(order_id)
    if order:
        text = pay.format_admin_message(order, decision_text=decision)
        if chat_id is not None and message_id is not None:
            pay.tg_edit_card(chat_id, message_id, text, is_photo)
        if order.get("tg_message_id") and order.get("tg_message_id") != message_id:
            pay.tg_update_order_card(order, decision)


def _handle_message(msg):
    chat = msg.get("chat") or {}
    if chat.get("type") != "private":
        return
    chat_id = chat.get("id")
    sender = (msg.get("from") or {}).get("id")
    text = (msg.get("text") or msg.get("caption") or "").strip()
    command = text.split()[0].split("@")[0].lower() if text else ""
    photos = msg.get("photo") or []

    # 1. Admin uploading a QR code photo (either with caption /setqr or after running /setqr)
    if pay.is_tg_admin(sender) and photos and (command == "/setqr" or pay.is_bot_awaiting_qr()):
        file_id = photos[-1].get("file_id")
        if file_id:
            raw_bytes = pay.tg_fetch_file(file_id)
            pay.save_bot_qr_code(file_id, raw_bytes)
            pay.tg_send_text(
                chat_id,
                "✅ <b>QR Code Updated on Website!</b>\n\n"
                "Students purchasing any Paid Test Series will now see this QR code on the checkout screen.",
            )
            return

    if command in ("/start", "/id"):
        if pay.is_tg_admin(sender):
            pay.tg_send_text(
                chat_id,
                "✅ <b>Connected to STUDY SANGAM HUB Bot.</b>\n\n"
                "• <b>/setqr</b> — Set or update the Payment QR Code shown on the website\n"
                "• <b>/pending</b> — Resend payments waiting for approval",
            )
        else:
            pay.tg_send_text(
                chat_id,
                f"Your chat ID is <code>{chat_id}</code>.\n"
                "If you are the site owner, put this number in the <code>TG_ADMIN_CHAT_ID</code> setting.",
            )
        return

    if command == "/setqr" and pay.is_tg_admin(sender):
        pay.set_bot_awaiting_qr(True)
        pay.tg_send_text(
            chat_id,
            "📷 <b>Set Website QR Code</b>\n\n"
            "Send me your UPI QR code image (as a Photo) right now, and I will set it on the website!",
        )
        return

    if command == "/pending" and pay.is_tg_admin(sender):
        waiting = pay.list_orders_admin(statuses=["submitted"], limit=5)
        if not waiting:
            pay.tg_send_text(chat_id, "🎉 Nothing is waiting for approval.")
            return
        for order in waiting:
            sent = pay.tg_resend_existing(order)
            if sent and not order.get("tg_message_id"):
                pay.save_telegram_refs(order["id"], sent.get("message_id"), sent.get("file_id"))
