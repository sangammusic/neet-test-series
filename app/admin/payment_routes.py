# SANGAM STUDY v2.2 — Handcrafted UI Build (2026-10-21)
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
    Response, abort, flash, redirect, render_template, render_template_string, request, session, url_for,
)
from jinja2 import TemplateNotFound

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
    ctx = dict(
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
    for tpl in ("admin_payments.html", "admin/admin_payments.html"):
        try:
            return render_template(tpl, **ctx)
        except TemplateNotFound:
            continue
    return render_template_string('{% extends "shared/base.html" %}\n{% block title %}Payments & Approvals — Admin{% endblock %}\n\n{% block nav %}\n    {% include "admin/_admin_nav.html" %}\n{% endblock %}\n\n{% block content %}\n{% set chips = {\n  \'approved\':  (\'Approved\',  \'bg-emerald-50 text-emerald-700 border-emerald-200\'),\n  \'submitted\': (\'Waiting\',   \'bg-amber-50 text-amber-800 border-amber-200\'),\n  \'created\':   (\'Unpaid\',    \'bg-slate-100 text-slate-600 border-slate-200\'),\n  \'rejected\':  (\'Rejected\',  \'bg-red-50 text-red-700 border-red-200\'),\n  \'expired\':   (\'Expired\',   \'bg-slate-100 text-slate-500 border-slate-200\'),\n  \'cancelled\': (\'Revoked\',   \'bg-slate-100 text-slate-500 border-slate-200\')\n} %}\n<div class="max-w-6xl mx-auto px-5 py-8">\n\n  <div class="flex flex-wrap items-center justify-between gap-4 mb-6 border-b border-ink/10 pb-4">\n    <div>\n      <h1 class="font-display text-2xl sm:text-3xl font-bold text-ink">Payment Approvals</h1>\n      <p class="text-xs sm:text-sm text-ink/60 mt-0.5">Verify student UTR payments or manage content from your Admin Control Center.</p>\n    </div>\n    <div class="flex flex-wrap items-center gap-2">\n      <a href="{{ url_for(\'admin.dashboard\') }}" class="inline-flex items-center gap-1.5 text-xs font-bold bg-ink text-white px-4 py-2.5 rounded-lg hover:bg-ink-light transition-colors shadow-sm">\n        🛠️ Admin Dashboard\n      </a>\n      <a href="{{ url_for(\'admin.questions_list\') }}" class="inline-flex items-center gap-1.5 text-xs font-bold bg-white border border-ink/15 text-ink px-3.5 py-2.5 rounded-lg hover:bg-slate-50 transition-colors">\n        ➕ Add Chapter Quiz\n      </a>\n      <a href="{{ url_for(\'admin.test_series_list\') }}" class="inline-flex items-center gap-1.5 text-xs font-bold bg-white border border-ink/15 text-ink px-3.5 py-2.5 rounded-lg hover:bg-slate-50 transition-colors">\n        📦 Mock Tests\n      </a>\n    </div>\n  </div>\n\n  <!-- ===== Compact Revenue Metrics ===== -->\n  <div class="grid grid-cols-2 lg:grid-cols-4 gap-3.5 mb-6">\n    <div class="bg-white border border-ink/10 rounded-xl p-4 shadow-sm">\n      <p class="text-xs font-semibold text-ink/60">Today</p>\n      <p class="font-display text-2xl font-bold text-ink mt-1 tabular-nums">{{ summary.revenue_today | inr }}</p>\n    </div>\n    <div class="bg-white border border-ink/10 rounded-xl p-4 shadow-sm">\n      <p class="text-xs font-semibold text-ink/60">This Month</p>\n      <p class="font-display text-2xl font-bold text-ink mt-1 tabular-nums">{{ summary.revenue_month | inr }}</p>\n    </div>\n    <div class="bg-white border border-ink/10 rounded-xl p-4 shadow-sm">\n      <p class="text-xs font-semibold text-ink/60">All Time <span class="text-ink/40">({{ summary.approved_count }} paid)</span></p>\n      <p class="font-display text-2xl font-bold text-ink mt-1 tabular-nums">{{ summary.revenue_total | inr }}</p>\n    </div>\n    <a href="{{ url_for(\'admin.payments_panel\', show=\'pending\') }}"\n       class="bg-white border rounded-xl p-4 shadow-sm {{ \'border-amber-300 bg-amber-50\' if summary.pending_count else \'border-ink/10\' }}">\n      <p class="text-xs font-semibold text-ink/60">Waiting Approval</p>\n      <p class="font-display text-2xl font-bold text-ink mt-1 tabular-nums">{{ summary.pending_count }}</p>\n    </a>\n  </div>\n\n  <!-- ===== Setup Checklist ===== -->\n  <div class="bg-white border border-ink/10 rounded-xl p-5 shadow-sm mb-6">\n    <h2 class="font-bold text-ink text-sm uppercase tracking-wider mb-3">Payment Gateway &amp; Telegram Setup</h2>\n    <ul class="space-y-1.5 text-xs sm:text-sm">\n      <li class="flex items-center gap-2">\n        <span class="{{ \'text-emerald-600\' if ready else \'text-amber-600\' }} font-bold">{{ \'✓\' if ready else \'•\' }}</span>\n        <span class="text-ink">UPI ID {% if ready %}is set: <span class="font-semibold">{{ upi_id }}</span>{% else %}not configured yet (Optional if all quizzes are currently Free; set <code class="bg-slate-100 px-1 rounded">UPI_ID=yourname@fam</code> on Render when enabling paid plans).{% endif %}</span>\n      </li>\n      <li class="flex items-center gap-2">\n        <span class="{{ \'text-emerald-600\' if tg_token_set else \'text-amber-600\' }} font-bold">{{ \'✓\' if tg_token_set else \'•\' }}</span>\n        <span class="text-ink">Telegram Bot Token (<code class="bg-slate-100 px-1 rounded">TG_TOKEN</code>): {{ \'Configured\' if tg_token_set else \'Not set yet\' }}</span>\n      </li>\n      <li class="flex items-center gap-2">\n        <span class="{{ \'text-emerald-600\' if tg_admin_set else \'text-amber-600\' }} font-bold">{{ \'✓\' if tg_admin_set else \'•\' }}</span>\n        <span class="text-ink">Telegram Admin Chat ID (<code class="bg-slate-100 px-1 rounded">TG_ADMIN_CHAT_ID</code>): {{ \'Configured\' if tg_admin_set else \'Not set yet\' }}</span>\n      </li>\n      <li class="flex items-start gap-2">\n        {% set hooked = webhook and webhook.get(\'url\') %}\n        <span class="{{ \'text-emerald-600\' if hooked else \'text-amber-600\' }} font-bold">{{ \'✓\' if hooked else \'•\' }}</span>\n        <span class="text-ink">\n          {% if hooked %}Webhook active at <span class="break-all font-semibold">{{ webhook.get(\'url\') }}</span>.\n          {% else %}Webhook not connected yet ({{ webhook_target }}).{% endif %}\n        </span>\n      </li>\n    </ul>\n    <div class="flex flex-wrap gap-2.5 mt-4">\n      <form method="POST" action="{{ url_for(\'admin.payments_tg_connect\') }}" class="m-0">\n        <button type="submit" class="bg-ink text-paper font-bold px-4 py-2 rounded-lg hover:bg-ink-light text-xs">Connect Telegram Bot</button>\n      </form>\n      <form method="POST" action="{{ url_for(\'admin.payments_tg_test\') }}" class="m-0">\n        <button type="submit" class="border border-ink/20 text-ink font-semibold px-4 py-2 rounded-lg hover:bg-slate-50 text-xs">Send Test Message</button>\n      </form>\n      <a href="{{ url_for(\'payments.preview_checkout\') }}" class="border border-ink/20 text-ink font-semibold px-4 py-2 rounded-lg hover:bg-slate-50 text-xs">Preview Student QR Screen &rarr;</a>\n    </div>\n  </div>\n\n  <!-- ===== Filter Tabs ===== -->\n  <div class="flex flex-wrap gap-2 mb-4">\n    {% for key, label in [(\'pending\', \'Waiting\'), (\'approved\', \'Approved\'), (\'problems\', \'Rejected / Expired\'), (\'all\', \'All Orders\')] %}\n    <a href="{{ url_for(\'admin.payments_panel\', show=key) }}"\n       class="px-3.5 py-1.5 rounded-lg text-xs font-bold border {{ \'bg-ink text-paper border-ink\' if show == key else \'border-ink/15 text-ink/70 hover:bg-slate-50\' }}">{{ label }}</a>\n    {% endfor %}\n  </div>\n\n  <!-- ===== Orders Table ===== -->\n  <div class="bg-white border border-ink/10 rounded-xl shadow-sm overflow-x-auto">\n    <table class="w-full min-w-[780px] text-xs sm:text-sm">\n      <thead>\n        <tr class="text-left text-ink/60 border-b border-ink/10">\n          <th class="font-semibold px-4 py-3">Student</th>\n          <th class="font-semibold px-4 py-3">Amount &amp; UTR</th>\n          <th class="font-semibold px-4 py-3">Time</th>\n          <th class="font-semibold px-4 py-3">Status</th>\n          <th class="font-semibold px-4 py-3">Action</th>\n        </tr>\n      </thead>\n      <tbody class="divide-y divide-ink/5">\n        {% for o in orders %}\n        {% set chip = chips.get(o.status, (\'Unknown\', \'bg-slate-100 text-slate-600 border-slate-200\')) %}\n        <tr class="align-top">\n          <td class="px-4 py-3.5">\n            <p class="font-semibold text-ink">{{ (o.profiles or {}).get(\'full_name\') or \'—\' }}</p>\n            <p class="text-ink/50 text-xs">@{{ (o.profiles or {}).get(\'username\') or \'user\' }}</p>\n            <p class="text-[11px] text-ink/40 mt-0.5">{{ (o.plans or {}).get(\'name\', \'Season Pass\') }}</p>\n          </td>\n          <td class="px-4 py-3.5">\n            <p class="font-display text-base font-bold text-ink tabular-nums">{{ o.payable_amount | inr(true) }}</p>\n            <p class="font-mono text-xs text-ink/70 tabular-nums">{{ o.utr or \'no UTR yet\' }}</p>\n            {% if o.tg_file_id %}<a href="{{ url_for(\'admin.payments_screenshot\', order_id=o.id) }}" target="_blank" rel="noopener" class="text-xs font-semibold text-indigo-600 hover:underline">View screenshot</a>{% endif %}\n          </td>\n          <td class="px-4 py-3.5 text-ink/60 text-xs">\n            {{ (o.submitted_at or o.created_at) | ist_datetime }}\n            {% if o.reviewed_at %}<p class="text-[11px] text-ink/40 mt-0.5">Reviewed {{ o.reviewed_at | ist_datetime }}</p>{% endif %}\n          </td>\n          <td class="px-4 py-3.5">\n            <span class="inline-block rounded-full border px-2.5 py-0.5 text-[11px] font-semibold {{ chip[1] }}">{{ chip[0] }}</span>\n            {% if o.reject_reason %}<p class="text-xs text-ink/50 mt-1.5 max-w-[200px]">{{ o.reject_reason }}</p>{% endif %}\n          </td>\n          <td class="px-4 py-3.5">\n            {% if o.status == \'submitted\' %}\n              <div class="flex flex-col gap-1.5 min-w-[180px]">\n                <form method="POST" action="{{ url_for(\'admin.payments_approve\', order_id=o.id) }}" class="m-0"\n                      onsubmit="return confirm(\'Approve {{ o.payable_amount | inr(true) }} and unlock access?\');">\n                  <button type="submit" class="w-full bg-emerald-600 text-white text-xs font-bold px-3 py-1.5 rounded-md hover:bg-emerald-700">Approve</button>\n                </form>\n                <form method="POST" action="{{ url_for(\'admin.payments_reject\', order_id=o.id) }}" class="m-0 flex gap-1.5">\n                  <select name="reason" class="flex-1 rounded-md border border-ink/15 px-2 py-1.5 text-xs" aria-label="Reason for rejecting">\n                    <option value="1">UTR not found</option>\n                    <option value="2">Wrong amount</option>\n                    <option value="3">Not received</option>\n                    <option value="4">Unclear proof</option>\n                  </select>\n                  <button type="submit" class="border border-red-300 text-red-700 text-xs font-semibold px-2.5 py-1.5 rounded-md hover:bg-red-50">Reject</button>\n                </form>\n              </div>\n            {% elif o.status == \'approved\' %}\n              <form method="POST" action="{{ url_for(\'admin.payments_revoke\', order_id=o.id) }}" class="m-0"\n                    onsubmit="return confirm(\'Revoke this student\\\'s access?\');">\n                <button type="submit" class="border border-red-300 text-red-700 text-xs font-semibold px-3 py-1.5 rounded-md hover:bg-red-50">Revoke Access</button>\n              </form>\n            {% elif o.status == \'rejected\' %}\n              <form method="POST" action="{{ url_for(\'admin.payments_approve\', order_id=o.id) }}" class="m-0"\n                    onsubmit="return confirm(\'Approve this payment after all?\');">\n                <button type="submit" class="border border-emerald-300 text-emerald-700 text-xs font-semibold px-3 py-1.5 rounded-md hover:bg-emerald-50">Approve Anyway</button>\n              </form>\n            {% else %}\n              <span class="text-ink/30">—</span>\n            {% endif %}\n          </td>\n        </tr>\n        {% else %}\n        <tr><td colspan="5" class="px-4 py-10 text-center text-ink/50 text-xs">\n          {% if show == \'pending\' %}No pending payments waiting for verification.{% else %}No payment records in this view yet.{% endif %}\n        </td></tr>\n        {% endfor %}\n      </tbody>\n    </table>\n  </div>\n</div>\n{% endblock %}\n<!-- SANGAM STUDY v2.2 — Handcrafted UI Build (2026-10-21) -->\n', **ctx)


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
