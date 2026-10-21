# SANGAM STUDY v2.2 — Handcrafted UI Build (2026-10-21)
"""
payments.py -- student-facing payment pages + the Telegram approval webhook.
"""
import hmac
import logging
import re
import threading
from datetime import datetime, timezone

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, render_template_string, request, url_for
from jinja2 import TemplateNotFound

from app.shared import payments_core as pay
from app.shared.access import (
    DEFAULT_SEASON_PASS_PLAN,
    get_active_plans, get_active_subscription, format_ist_date, parse_ts, premium_status,
)
from app.shared.security import minutes_text, rl_hit
from app.shared.utils import current_user_id, is_logged_in

logger = logging.getLogger(__name__)

payments_bp = Blueprint("payments", __name__, template_folder="../templates/payments")


_FALLBACK_PAY_TEMPLATES = {
    "plans.html": '{% extends "shared/base.html" %}\n{% block title %}Season Pass — SANGAM STUDY{% endblock %}\n\n{% block content %}\n<section class="relative z-10 mx-auto max-w-2xl px-5 pt-8 pb-20">\n\n  <div class="mb-6 flex flex-wrap items-center justify-between gap-3 border-b border-white/10 pb-4">\n    <div>\n      <a href="{{ url_for(\'user.landing\') }}" data-back-url="{{ url_for(\'user.landing\') }}" class="text-xs font-bold text-gray-400 hover:text-gold mb-2 inline-flex items-center gap-1.5 transition-colors">\n        &larr; Back to Workspace\n      </a>\n      <h1 class="font-display text-2xl sm:text-3xl font-bold text-white tracking-tight">Season Pass</h1>\n      <p class="mt-1 text-xs sm:text-sm text-gray-400">One-time access to every premium mock test and practice set until the exam season ends.</p>\n    </div>\n    <a href="{{ url_for(\'payments.history\') }}" class="text-xs font-bold text-gray-300 hover:text-gold bg-white/5 border border-white/10 px-3.5 py-2 rounded-lg transition-colors">\n      🧾 My Payments\n    </a>\n  </div>\n\n  {# ---- Admin Control Bar / Active Status ---- #}\n  {% if ps.active %}\n    <div class="mb-6 rounded-xl border border-emerald-400/30 bg-[#12161f]/90 p-4 backdrop-blur-xl">\n      <div class="flex flex-wrap items-center justify-between gap-3">\n        <div>\n          <p class="font-display text-base font-bold text-white">\n            {{ \'⚡ Admin Access Active\' if ps.is_admin else (\'✓ \' ~ (ps.plan_name or \'Season Pass\') ~ \' Active\') }}\n          </p>\n          <p class="text-xs text-gray-400 mt-0.5">\n            {% if ps.is_admin %}\n              All quizzes and mock tests are unlocked for you. Use the shortcuts below to manage content or preview checkout.\n            {% elif ps.expires_label %}\n              Valid until {{ ps.expires_label }}.\n            {% else %}\n              Every premium mock test and practice set is unlocked.\n            {% endif %}\n          </p>\n        </div>\n      </div>\n      <div class="mt-3.5 pt-3 border-t border-white/5 flex flex-wrap gap-2">\n        {% if ps.is_admin %}\n        <a href="{{ url_for(\'admin.dashboard\') }}" class="inline-flex items-center gap-1.5 rounded-lg bg-gold px-3.5 py-2 text-xs font-bold text-ink hover:bg-gold-light transition-colors">\n          🛠️ Admin Dashboard\n        </a>\n        <a href="{{ url_for(\'admin.questions_list\') }}" class="inline-flex items-center gap-1.5 rounded-lg border border-white/15 bg-white/5 px-3.5 py-2 text-xs font-semibold text-white hover:bg-white/10 transition-colors">\n          ➕ Add Quiz / Material\n        </a>\n        <a href="{{ url_for(\'admin.payments_panel\') }}" class="inline-flex items-center gap-1.5 rounded-lg border border-emerald-400/30 bg-emerald-500/10 px-3.5 py-2 text-xs font-semibold text-emerald-300 hover:bg-emerald-500/20 transition-colors">\n          ✅ Payment Approvals\n        </a>\n        <a href="{{ url_for(\'payments.preview_checkout\') }}" class="inline-flex items-center gap-1.5 rounded-lg border border-gold/40 bg-gold/10 px-3.5 py-2 text-xs font-bold text-gold hover:bg-gold/20 transition-colors">\n          🔍 Preview QR Page\n        </a>\n        {% else %}\n        <a href="{{ url_for(\'user.landing\') }}" class="inline-flex items-center justify-center rounded-lg bg-gold px-4 py-2 text-xs font-bold text-ink hover:bg-gold-light transition-colors">Go to Workspace</a>\n        <a href="{{ url_for(\'payments.history\') }}" class="inline-flex items-center justify-center rounded-lg border border-white/15 px-4 py-2 text-xs font-semibold text-gray-200 hover:bg-white/5 transition-colors">My Payments</a>\n        {% endif %}\n      </div>\n    </div>\n  {% endif %}\n\n  {# ---- Unfinished Order ---- #}\n  {% if open_order and (not ps.active or ps.is_admin) %}\n    <a href="{{ url_for(\'payments.checkout\', order_id=open_order.id) }}"\n       class="mb-6 flex items-center justify-between gap-4 rounded-xl border border-gold/40 bg-gold/[0.08] p-4 transition-colors hover:bg-gold/12">\n      <div>\n        <p class="font-display text-sm font-bold text-white">{{ \'Payment verification in progress\' if open_order.status == \'submitted\' else \'Resume your pending payment\' }}</p>\n        <p class="mt-0.5 text-xs text-gray-400">{{ open_order.payable_amount | inr(true) }}{% if open_order.status == \'created\' %} reserved for you{% endif %}</p>\n      </div>\n      <span class="shrink-0 text-xs font-bold text-gold">{{ \'Check Status →\' if open_order.status == \'submitted\' else \'Continue →\' }}</span>\n    </a>\n  {% endif %}\n\n  {# ---- Compact Plan Card ---- #}\n  {% for plan in plans %}\n  <article class="mb-6 rounded-xl border border-gold/30 bg-[#12141d]/95 p-5 sm:p-6 backdrop-blur-xl shadow-xl">\n    <div class="flex items-start justify-between gap-4 border-b border-white/10 pb-4">\n      <div>\n        <span class="text-[10px] font-extrabold uppercase tracking-widest text-gold bg-gold/10 border border-gold/30 px-2 py-0.5 rounded">All-Access Pass</span>\n        <h2 class="font-display text-xl font-bold text-white mt-1.5">{{ plan.name }}</h2>\n        {% if plan.description %}<p class="mt-1 text-xs leading-relaxed text-gray-400 max-w-md">{{ plan.description }}</p>{% endif %}\n      </div>\n      <div class="text-right shrink-0">\n        <p class="font-display text-3xl font-bold text-white tabular-nums">{{ plan.price_inr | inr }}</p>\n        {% if plan.valid_until %}<p class="mt-0.5 text-[11px] text-gray-400">Until {{ plan.valid_until | ist_date }}</p>\n        {% elif plan.valid_days %}<p class="mt-0.5 text-[11px] text-gray-400">{{ plan.valid_days }} days</p>{% endif %}\n      </div>\n    </div>\n\n    <ul class="mt-4 grid sm:grid-cols-2 gap-2.5 text-xs">\n      {% for item in plan.features or [] %}\n      <li class="flex items-center gap-2 text-gray-200">\n        <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 shrink-0 text-gold" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>\n        <span>{{ item }}</span>\n      </li>\n      {% endfor %}\n    </ul>\n\n    {% if not ps.active or ps.is_admin %}\n      {% if ready and (db_plans_ready is not defined or db_plans_ready) %}\n      <form method="POST" action="{{ url_for(\'payments.start\') }}" class="mt-5">\n        <input type="hidden" name="plan_code" value="{{ plan.code }}">\n        <button type="submit"\n                class="w-full rounded-xl bg-gold py-3 text-sm font-bold text-ink shadow-[0_4px_14px_0_rgba(212,160,23,0.2)] transition-all hover:bg-gold-light">\n          Get {{ plan.name }} for {{ plan.price_inr | inr }} &rarr;\n        </button>\n      </form>\n      {% else %}\n      <div class="mt-5">\n        <a href="{{ url_for(\'payments.preview_checkout\') }}"\n           class="flex w-full items-center justify-center gap-2 rounded-xl bg-gold py-3 text-sm font-bold text-ink shadow-[0_4px_14px_0_rgba(212,160,23,0.2)] transition-all hover:bg-gold-light">\n          <span>Continue to Payment QR ({{ plan.price_inr | inr }})</span>\n          <span>&rarr;</span>\n        </a>\n        {% if ps.is_admin and not ready %}\n        <p class="mt-2 text-center text-[11px] text-amber-300/90">\n          Note: Set <code class="bg-black/40 px-1 py-0.5 rounded font-mono">UPI_ID</code> in Render Environment Variables when you are ready to accept live student payments.\n        </p>\n        {% endif %}\n      </div>\n      {% endif %}\n    {% endif %}\n  </article>\n  {% endfor %}\n\n  {# ---- Compact FAQ ---- #}\n  <div class="mt-8">\n    <h2 class="mb-3 font-display text-base font-bold text-white">Frequently Asked Questions</h2>\n    <div class="divide-y divide-white/10 rounded-xl border border-white/10 bg-[#12141d]/80 text-xs">\n      <details class="group p-3.5">\n        <summary class="cursor-pointer list-none font-semibold text-white flex items-center justify-between">\n          <span>How do I pay?</span>\n          <span class="text-gold">+</span>\n        </summary>\n        <p class="mt-2 leading-relaxed text-gray-400">Scan the UPI QR code with PhonePe, Google Pay, Paytm, or FamPay, then submit your 12-digit UTR number.</p>\n      </details>\n      <details class="group p-3.5">\n        <summary class="cursor-pointer list-none font-semibold text-white flex items-center justify-between">\n          <span>What if all quizzes are currently free?</span>\n          <span class="text-gold">+</span>\n        </summary>\n        <p class="mt-2 leading-relaxed text-gray-400">Free quizzes and mock tests can be attempted directly without any payment. The Season Pass is only required for sets marked Premium.</p>\n      </details>\n      <details class="group p-3.5">\n        <summary class="cursor-pointer list-none font-semibold text-white flex items-center justify-between">\n          <span>Need help with a payment?</span>\n          <span class="text-gold">+</span>\n        </summary>\n        <p class="mt-2 leading-relaxed text-gray-400">Click the support headset button in the bottom-right corner or <a href="{{ support_url }}" target="_blank" rel="noopener" class="font-semibold text-gold hover:underline">contact support</a> with your UTR.</p>\n      </details>\n    </div>\n  </div>\n</section>\n{% endblock %}\n<!-- SANGAM STUDY v2.2 — Handcrafted UI Build (2026-10-21) -->\n',
    "history.html": '{% extends "shared/base.html" %}\n{% block title %}My Payments — SANGAM STUDY{% endblock %}\n\n{% block content %}\n{% set chips = {\n  \'approved\':  (\'Approved\',        \'border-emerald-400/30 bg-emerald-400/10 text-emerald-300\'),\n  \'submitted\': (\'Verifying\',       \'border-amber-400/30 bg-amber-400/10 text-amber-200\'),\n  \'created\':   (\'Not paid yet\',    \'border-white/15 bg-white/5 text-gray-300\'),\n  \'rejected\':  (\'Not verified\',    \'border-red-400/30 bg-red-500/10 text-red-300\'),\n  \'expired\':   (\'Expired\',         \'border-white/15 bg-white/5 text-gray-400\'),\n  \'cancelled\': (\'Cancelled\',       \'border-white/15 bg-white/5 text-gray-400\')\n} %}\n<section class="relative z-10 mx-auto max-w-2xl px-5 pt-8 pb-20">\n  <div class="mb-6 flex flex-wrap items-end justify-between gap-3 border-b border-white/10 pb-4">\n    <div>\n      <a href="{{ url_for(\'user.my_dashboard\') }}" data-back-url="{{ url_for(\'user.my_dashboard\') }}" class="text-xs font-bold text-gray-400 hover:text-gold mb-2 inline-flex items-center gap-1.5 transition-colors">\n        &larr; Back to Workspace\n      </a>\n      <h1 class="font-display text-2xl sm:text-3xl font-bold text-white tracking-tight">My Payments</h1>\n    </div>\n    <a href="{{ url_for(\'payments.plans\') }}" class="text-xs font-bold text-gold bg-gold/10 border border-gold/30 px-3.5 py-2 rounded-lg hover:bg-gold/20 transition-colors">\n      💎 Season Pass &rarr;\n    </a>\n  </div>\n\n  {% if ps and ps.active %}\n  <div class="mb-5 rounded-xl border border-emerald-400/30 bg-[#12161f]/90 p-4 text-xs text-gray-200">\n    <div class="flex flex-wrap items-center justify-between gap-3">\n      <span class="font-semibold">{{ \'⚡ Admin Access Active\' if ps.is_admin else \'✓ Your \' ~ (ps.plan_name or \'Season Pass\') ~ \' is active\' ~ (\' until \' ~ ps.expires_label ~ \'.\' if ps.expires_label else \'.\') }}</span>\n      {% if ps.is_admin %}\n      <div class="flex flex-wrap gap-2">\n        <a href="{{ url_for(\'admin.dashboard\') }}" class="rounded-lg bg-gold px-3 py-1.5 text-xs font-bold text-ink hover:bg-gold-light transition-colors">🛠️ Admin Dashboard</a>\n        <a href="{{ url_for(\'admin.payments_panel\') }}" class="rounded-lg border border-emerald-400/30 bg-emerald-500/10 px-3 py-1.5 text-xs font-semibold text-emerald-300 hover:bg-emerald-500/20 transition-colors">✅ Approvals</a>\n        <a href="{{ url_for(\'payments.preview_checkout\') }}" class="rounded-lg border border-gold/40 bg-gold/10 px-3 py-1.5 text-xs font-bold text-gold hover:bg-gold/20 transition-colors">🔍 Preview QR</a>\n      </div>\n      {% endif %}\n    </div>\n  </div>\n  {% endif %}\n\n  {% for o in orders or [] %}\n    {% set st = (o.get(\'status\') if o is mapping else o.status) or \'created\' %}\n    {% set chip = chips.get(st, (\'Unknown\', \'border-white/15 bg-white/5 text-gray-300\')) %}\n    {% set plan_obj = o.get(\'plans\') if (o is mapping and o.get(\'plans\') is mapping) else {} %}\n    <a href="{{ url_for(\'payments.checkout\', order_id=o.id) }}"\n       class="mb-3 flex items-center justify-between gap-4 rounded-xl border border-white/10 bg-[#12141d]/90 p-4 transition-colors hover:border-gold/40">\n      <div class="min-w-0">\n        <p class="font-semibold text-sm text-white">{{ plan_obj.get(\'name\') or \'Season Pass\' }}</p>\n        <p class="mt-0.5 text-xs text-gray-400">{{ o.created_at | ist_datetime }}</p>\n      </div>\n      <div class="flex shrink-0 flex-col items-end gap-1.5">\n        <p class="font-display text-base font-bold text-white tabular-nums">{{ o.payable_amount | inr(true) }}</p>\n        <span class="rounded-full border px-2 py-0.5 text-[10px] font-semibold {{ chip[1] }}">{{ chip[0] }}</span>\n      </div>\n    </a>\n  {% else %}\n    <div class="rounded-xl border border-dashed border-white/15 bg-[#12141d]/80 p-8 text-center">\n      <p class="font-display text-base font-bold text-white">No payment records yet</p>\n      <p class="mt-1 text-xs text-gray-400">When you purchase a Season Pass, your payment status and receipt appear here.</p>\n      <div class="mt-4 flex flex-wrap justify-center gap-2.5">\n        <a href="{{ url_for(\'payments.plans\') }}" class="inline-flex rounded-lg bg-gold px-5 py-2.5 text-xs font-bold text-ink hover:bg-gold-light transition-colors">View Season Pass</a>\n        {% if ps and ps.is_admin %}\n        <a href="{{ url_for(\'admin.dashboard\') }}" class="inline-flex rounded-lg border border-white/15 bg-white/5 px-4 py-2.5 text-xs font-semibold text-white hover:bg-white/10 transition-colors">🛠️ Admin Dashboard</a>\n        <a href="{{ url_for(\'payments.preview_checkout\') }}" class="inline-flex rounded-lg border border-gold/40 bg-gold/10 px-4 py-2.5 text-xs font-bold text-gold hover:bg-gold/20 transition-colors">🔍 Preview QR Checkout</a>\n        {% endif %}\n      </div>\n    </div>\n  {% endfor %}\n</section>\n{% endblock %}\n<!-- SANGAM STUDY v2.2 — Handcrafted UI Build (2026-10-21) -->\n',
    "checkout.html": '{% extends "shared/base.html" %}\n{% block title %}{% if state == \'approved\' %}Payment approved{% elif state == \'waiting\' %}Verifying payment{% else %}Complete your payment{% endif %} — SANGAM STUDY{% endblock %}\n\n{% block extra_head %}\n<style>\n  /* The QR is an inline SVG with a viewBox: let CSS size it. */\n  .qr-box svg { display: block; width: 100%; height: 100%; }\n  @media (prefers-reduced-motion: reduce) { .pay-pulse { animation: none !important; } }\n</style>\n{% endblock %}\n\n{# One reusable block for "enter your UTR + optional screenshot" (used while paying and for late payers) #}\n{% macro proof_form(heading, intro) %}\n<div class="rounded-2xl border border-white/10 bg-[#161616]/90 backdrop-blur-xl p-6 sm:p-7 mt-5">\n  <h2 class="font-display text-xl font-bold text-white mb-1">{{ heading }}</h2>\n  <p class="text-sm text-gray-400 leading-relaxed mb-5">{{ intro }}</p>\n\n  <label for="utr" class="block text-sm font-semibold text-gray-300 mb-2">UTR (UPI reference number)</label>\n  <input id="utr" type="text" inputmode="numeric" autocomplete="off" maxlength="14" placeholder="12-digit number"\n         aria-describedby="utr-help"\n         class="w-full bg-[#0f0f0f] border border-white/10 rounded-xl px-4 py-3.5 text-white text-lg tracking-widest tabular-nums placeholder-gray-600 placeholder:tracking-normal placeholder:text-base focus:outline-none focus:border-gold/50 focus:ring-1 focus:ring-gold/50 transition-all">\n  <p id="utr-help" class="text-xs text-gray-500 mt-2 leading-relaxed">\n    Open your payment app, then the payment details. Look for "UTR", "UPI Ref No." or "Transaction ID" — it has 12 digits.\n  </p>\n\n  <div class="mt-6">\n    <p class="text-sm font-semibold text-gray-300 mb-2">Payment screenshot <span class="font-normal text-gray-500">(optional, but it speeds up approval)</span></p>\n    <input id="shot" type="file" accept="image/*" class="sr-only">\n    <label for="shot" id="shot-pick"\n           class="flex items-center justify-center gap-2 w-full cursor-pointer rounded-xl border border-dashed border-white/20 px-4 py-4 text-sm font-semibold text-gray-300 hover:border-gold/50 hover:text-white transition-colors">\n      <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 text-gold" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"/></svg>\n      Choose screenshot\n    </label>\n    <div id="shot-preview" class="hidden mt-3 flex items-center gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-3">\n      <img id="shot-img" alt="Screenshot preview" class="h-16 w-16 rounded-lg object-cover bg-white/5">\n      <div class="min-w-0 flex-1">\n        <p id="shot-name" class="truncate text-sm font-semibold text-gray-200">screenshot</p>\n        <p id="shot-size" class="text-xs text-gray-500"></p>\n      </div>\n      <button type="button" id="shot-remove" class="rounded-lg px-3 py-2 text-sm font-semibold text-gray-400 hover:text-white hover:bg-white/10 transition-colors">Remove</button>\n    </div>\n  </div>\n\n  <p id="pay-error" role="alert" class="hidden mt-5 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm font-medium text-red-300"></p>\n\n  <button type="button" id="pay-submit" disabled\n          class="mt-6 w-full rounded-xl bg-gold py-3.5 font-bold text-ink shadow-[0_4px_14px_0_rgba(212,160,23,0.2)] transition-all hover:bg-gold-light disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-gold">\n    <span id="pay-submit-label">Submit for verification</span>\n  </button>\n  <p class="mt-4 text-center text-xs leading-relaxed text-gray-500">Your plan unlocks as soon as we match this payment in our account.</p>\n</div>\n{% endmacro %}\n\n{% block content %}\n<section class="relative z-10 mx-auto max-w-xl px-5 sm:px-6 pt-10 pb-24" id="pay-root">\n\n  <a href="{{ url_for(\'payments.plans\') }}" class="inline-flex items-center gap-1.5 text-sm font-semibold text-gray-400 hover:text-white transition-colors">\n    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M15 19l-7-7 7-7"/></svg>\n    Plans\n  </a>\n\n  {% if is_preview is defined and is_preview %}\n  <div class="mt-5 rounded-2xl border border-amber-400/30 bg-amber-400/10 p-4 text-xs leading-relaxed text-amber-200">\n    <strong>🔍 Payment Page Preview Mode:</strong> This is how the payment screen looks to students.\n    {% if not ready %}Set <code class="bg-black/40 px-1.5 py-0.5 rounded font-mono">UPI_ID=yourname@fam</code> in Render Environment Variables and run <code class="bg-black/40 px-1.5 py-0.5 rounded font-mono">sql/phase1_payments.sql</code> in Supabase SQL Editor to accept real orders.{% endif %}\n  </div>\n  {% endif %}\n\n  <header class="mt-6 mb-8">\n    <h1 class="font-display text-3xl font-bold text-white">{{ plan.name or \'Season Pass\' }}</h1>\n    <p class="mt-1 text-gray-400">\n      {% if valid_until %}{% if state == \'approved\' %}Active until {{ valid_until }}{% else %}Valid until {{ valid_until }}{% endif %}{% endif %}\n    </p>\n    <p class="mt-0.5 text-xs text-gray-500">Payment reference {{ ref }}</p>\n  </header>\n\n  {# ============================ PAY ============================ #}\n  {% if state == \'pay\' %}\n    {% set paise = (((order.payable_amount | float(299.0)) - (order.base_amount | float(299.0))) * 100) | round | int %}\n    <div class="rounded-2xl border border-gold/30 bg-gold/[0.06] p-6 sm:p-7">\n      <p class="text-sm text-gray-400">Pay exactly</p>\n      <div class="mt-1 flex items-end justify-between gap-4">\n        <p class="font-display text-5xl font-bold text-white tabular-nums" id="pay-amount">{{ order.payable_amount | inr(true) }}</p>\n        <button type="button" data-copy="{{ \'%.2f\' | format(order.payable_amount | float(299.0)) }}"\n                class="mb-1 shrink-0 rounded-lg border border-white/15 px-3 py-2 text-sm font-semibold text-gray-200 hover:bg-white/10 transition-colors">Copy amount</button>\n      </div>\n      <p class="mt-4 text-sm leading-relaxed text-gray-400">\n        The {{ paise }} paise on top of {{ order.base_amount | inr }} is how we recognise your payment.\n        You pay {{ order.payable_amount | inr(true) }} in total, and nothing else is charged.\n      </p>\n    </div>\n\n    <div class="mt-5 rounded-2xl border border-white/10 bg-[#161616]/90 backdrop-blur-xl p-6 sm:p-7">\n      <div class="flex items-center gap-3 mb-5">\n        <span class="grid h-7 w-7 shrink-0 place-items-center rounded-full border border-gold/40 text-sm font-semibold text-gold" aria-hidden="true">1</span>\n        <h2 class="font-display text-xl font-bold text-white">Pay with any UPI app</h2>\n      </div>\n\n      <div class="flex flex-col items-center">\n        {% if qr_svg %}\n          <div class="qr-box h-60 w-60 overflow-hidden rounded-2xl bg-white p-2 sm:h-64 sm:w-64 shadow-lg">{{ qr_svg | safe }}</div>\n          <p class="mt-3 text-center text-sm text-gray-400">Scan with PhonePe, Google Pay, Paytm, FamPay or any UPI app. The amount is already filled in.</p>\n          {% if static_qr %}\n          <details class="mt-3 text-center text-xs text-gray-400">\n            <summary class="cursor-pointer font-semibold text-gold hover:underline">Or view backup static QR image</summary>\n            <div class="mx-auto mt-2 h-52 w-52 overflow-hidden rounded-2xl bg-white p-2"><img src="{{ static_qr }}" alt="Backup UPI QR code" class="h-full w-full object-contain"></div>\n          </details>\n          {% endif %}\n        {% elif static_qr %}\n          <div class="h-60 w-60 overflow-hidden rounded-2xl bg-white p-2 sm:h-64 sm:w-64 shadow-lg"><img src="{{ static_qr }}" alt="UPI QR code" class="h-full w-full object-contain"></div>\n          <p class="mt-3 text-center text-sm text-amber-300">Enter the exact amount yourself: {{ order.payable_amount | inr(true) }}</p>\n        {% else %}\n          <p class="rounded-xl border border-amber-400/30 bg-amber-400/10 px-4 py-3 text-center text-sm text-amber-200">\n            The QR code isn\'t available right now. Use the UPI ID below and pay exactly {{ order.payable_amount | inr(true) }}.\n          </p>\n        {% endif %}\n\n        <a id="upi-open" href="{{ upi_link }}" class="hidden mt-5 w-full items-center justify-center rounded-xl border border-gold/40 py-3 font-bold text-gold hover:bg-gold/10 transition-colors">\n          Open my UPI app\n        </a>\n      </div>\n\n      <div class="mt-6 flex items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3">\n        <div class="min-w-0">\n          <p class="text-xs text-gray-500">UPI ID</p>\n          <p class="truncate font-semibold text-white">{{ upi_id }}</p>\n        </div>\n        <button type="button" data-copy="{{ upi_id }}"\n                class="shrink-0 rounded-lg border border-white/15 px-3 py-2 text-sm font-semibold text-gray-200 hover:bg-white/10 transition-colors">Copy</button>\n      </div>\n\n      <p class="mt-5 text-center text-sm text-gray-400">\n        This amount is reserved for you for <span id="pay-timer" class="font-semibold text-white tabular-nums" aria-live="off">--:--</span>\n      </p>\n    </div>\n\n    <div class="mt-8 flex items-center gap-3">\n      <span class="grid h-7 w-7 shrink-0 place-items-center rounded-full border border-gold/40 text-sm font-semibold text-gold" aria-hidden="true">2</span>\n      <p class="text-sm text-gray-400">After paying, come back here and send us the proof.</p>\n    </div>\n    {{ proof_form(\'Send your payment proof\', \'We check your UTR against our account. Entering it correctly is the fastest way to get approved.\') }}\n\n  {# ============================ LATE ============================ #}\n  {% elif state == \'late\' %}\n    <div class="rounded-2xl border border-amber-400/30 bg-amber-400/[0.06] p-6 sm:p-7">\n      <h2 class="font-display text-xl font-bold text-white mb-2">This payment window has ended</h2>\n      <p class="text-sm leading-relaxed text-gray-300">\n        If you already paid {{ order.payable_amount | inr(true) }}, enter your UTR below and we will still check it.\n        If you haven\'t paid yet, start a new payment so you get a fresh amount.\n      </p>\n      <form method="POST" action="{{ url_for(\'payments.start\') }}" class="mt-5">\n        <input type="hidden" name="plan_code" value="{{ plan.code }}">\n        <button type="submit" class="w-full rounded-xl border border-gold/40 py-3 font-bold text-gold hover:bg-gold/10 transition-colors">Start a new payment</button>\n      </form>\n    </div>\n    {{ proof_form(\'I already paid\', \'Enter the UTR of the payment you made.\') }}\n\n  {# ============================ WAITING ============================ #}\n  {% elif state == \'waiting\' %}\n    <div class="rounded-2xl border border-white/10 bg-[#161616]/90 backdrop-blur-xl p-6 sm:p-7">\n      <div class="flex items-center gap-3">\n        <span class="pay-pulse relative flex h-3 w-3" aria-hidden="true">\n          <span class="absolute inline-flex h-full w-full rounded-full bg-gold opacity-60 motion-safe:animate-ping"></span>\n          <span class="relative inline-flex h-3 w-3 rounded-full bg-gold"></span>\n        </span>\n        <h2 class="font-display text-xl font-bold text-white">We\'re verifying your payment</h2>\n      </div>\n      <p class="mt-3 text-sm leading-relaxed text-gray-400">\n        You get access the moment it\'s approved. This page refreshes by itself, so you can keep it open or come back later.\n      </p>\n      <dl class="mt-6 divide-y divide-white/5 text-sm">\n        <div class="flex justify-between gap-4 py-3"><dt class="text-gray-500">Amount</dt><dd class="font-semibold text-white tabular-nums">{{ order.payable_amount | inr(true) }}</dd></div>\n        <div class="flex justify-between gap-4 py-3"><dt class="text-gray-500">UTR</dt><dd class="font-semibold text-white tabular-nums">{{ order.utr }}</dd></div>\n        <div class="flex justify-between gap-4 py-3"><dt class="text-gray-500">Submitted</dt><dd class="font-semibold text-white">{{ order.submitted_at | ist_datetime }}</dd></div>\n      </dl>\n      <p id="poll-note" class="mt-4 hidden text-xs text-gray-500">Still waiting? Refresh this page to check again.</p>\n    </div>\n\n  {# ============================ APPROVED ============================ #}\n  {% elif state == \'approved\' %}\n    <div class="rounded-2xl border border-emerald-400/30 bg-emerald-400/[0.07] p-6 sm:p-8 text-center">\n      <div class="mx-auto mb-4 grid h-14 w-14 place-items-center rounded-full bg-emerald-400/15 text-emerald-300" aria-hidden="true">\n        <svg xmlns="http://www.w3.org/2000/svg" class="h-7 w-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>\n      </div>\n      <h2 class="font-display text-2xl font-bold text-white">Payment approved</h2>\n      <p class="mt-2 text-gray-300">Your {{ plan.name or \'plan\' }} is active{% if valid_until %} until {{ valid_until }}{% endif %}. Every premium test and practice set is unlocked.</p>\n      <a href="{{ url_for(\'user.landing\') }}" class="mt-6 inline-flex w-full items-center justify-center rounded-xl bg-gold py-3.5 font-bold text-ink hover:bg-gold-light transition-colors">Start practising</a>\n      <a href="{{ url_for(\'payments.history\') }}" class="mt-3 inline-block text-sm font-semibold text-gray-400 hover:text-white transition-colors">View my payments</a>\n    </div>\n\n  {# ============================ REJECTED ============================ #}\n  {% elif state == \'rejected\' %}\n    <div class="rounded-2xl border border-red-400/30 bg-red-400/[0.06] p-6 sm:p-7">\n      <h2 class="font-display text-xl font-bold text-white mb-2">We couldn\'t verify this payment</h2>\n      <p class="text-sm leading-relaxed text-gray-300">{{ order.reject_reason or \'The payment could not be matched with our account.\' }}</p>\n      <form method="POST" action="{{ url_for(\'payments.start\') }}" class="mt-5">\n        <input type="hidden" name="plan_code" value="{{ plan.code }}">\n        <button type="submit" class="w-full rounded-xl bg-gold py-3.5 font-bold text-ink hover:bg-gold-light transition-colors">Pay again</button>\n      </form>\n      <p class="mt-4 text-center text-sm text-gray-400">Money deducted? <a href="{{ support_url }}" target="_blank" rel="noopener" class="font-semibold text-gold hover:underline">Contact support</a> with your UTR.</p>\n    </div>\n\n  {# ============================ ENDED ============================ #}\n  {% else %}\n    <div class="rounded-2xl border border-white/10 bg-[#161616]/90 backdrop-blur-xl p-6 sm:p-7">\n      <h2 class="font-display text-xl font-bold text-white mb-2">This payment was cancelled</h2>\n      <p class="text-sm leading-relaxed text-gray-400">{{ order.reject_reason or \'This payment is no longer active.\' }}</p>\n      <form method="POST" action="{{ url_for(\'payments.start\') }}" class="mt-5">\n        <input type="hidden" name="plan_code" value="{{ plan.code }}">\n        <button type="submit" class="w-full rounded-xl border border-gold/40 py-3 font-bold text-gold hover:bg-gold/10 transition-colors">Start a new payment</button>\n      </form>\n    </div>\n  {% endif %}\n\n  <p class="mt-10 text-center text-sm text-gray-500">\n    Need help? <a href="{{ support_url }}" target="_blank" rel="noopener" class="font-semibold text-gold hover:underline">Contact support</a>\n    <br>\n    <span class="text-xs">By paying you agree to our <a href="{{ url_for(\'legal.terms\') }}" class="underline hover:text-gray-300">Terms</a> and <a href="{{ url_for(\'legal.refund_policy\') }}" class="underline hover:text-gray-300">Refund policy</a>.</span>\n  </p>\n</section>\n{% endblock %}\n\n{% block extra_scripts %}\n<script>window.PAY_CFG = {{ cfg | tojson }};</script>\n<script>\n(function () {\n  \'use strict\';\n  var cfg = window.PAY_CFG || {};\n  var $ = function (id) { return document.getElementById(id); };\n\n  /* ---------- copy buttons ---------- */\n  document.querySelectorAll(\'[data-copy]\').forEach(function (btn) {\n    btn.addEventListener(\'click\', function () {\n      var text = btn.getAttribute(\'data-copy\');\n      var label = btn.textContent;\n      function done() { btn.textContent = \'Copied\'; setTimeout(function () { btn.textContent = label; }, 1400); }\n      function fallback() {\n        var t = document.createElement(\'textarea\');\n        t.value = text; t.setAttribute(\'readonly\', \'\');\n        t.style.position = \'fixed\'; t.style.opacity = \'0\';\n        document.body.appendChild(t); t.select();\n        try { document.execCommand(\'copy\'); done(); } catch (e) { /* ignore */ }\n        document.body.removeChild(t);\n      }\n      if (navigator.clipboard && window.isSecureContext) {\n        navigator.clipboard.writeText(text).then(done, fallback);\n      } else { fallback(); }\n    });\n  });\n\n  /* ---------- "Open my UPI app" only makes sense on a phone ---------- */\n  var openBtn = $(\'upi-open\');\n  if (openBtn && /Android|iPhone|iPad|iPod/i.test(navigator.userAgent)) {\n    openBtn.classList.remove(\'hidden\');\n    openBtn.classList.add(\'flex\');\n  }\n\n  /* ---------- reservation countdown ---------- */\n  var timerEl = $(\'pay-timer\');\n  var left = parseInt(cfg.secondsLeft, 10) || 0;\n  function tick() {\n    if (!timerEl) { return; }\n    if (left <= 0) { timerEl.textContent = \'a moment longer. Refresh the page if it has ended\'; return; }\n    var m = Math.floor(left / 60), s = left % 60;\n    timerEl.textContent = (m < 10 ? \'0\' : \'\') + m + \':\' + (s < 10 ? \'0\' : \'\') + s;\n    left -= 1;\n    setTimeout(tick, 1000);\n  }\n  tick();\n\n  /* ---------- proof form ---------- */\n  var utr = $(\'utr\'), submit = $(\'pay-submit\'), errBox = $(\'pay-error\');\n  var shotInput = $(\'shot\'), shotBlob = null;\n\n  function showError(msg) { if (errBox) { errBox.textContent = msg; errBox.classList.remove(\'hidden\'); } }\n  function clearError() { if (errBox) { errBox.classList.add(\'hidden\'); errBox.textContent = \'\'; } }\n  function setBusy(b) {\n    if (!submit) { return; }\n    submit.disabled = b || (utr.value.length !== 12);\n    $(\'pay-submit-label\').textContent = b ? \'Submitting…\' : \'Submit for verification\';\n    if (utr) { utr.disabled = b; }\n  }\n\n  if (utr && submit) {\n    utr.addEventListener(\'input\', function () {\n      utr.value = utr.value.replace(/\\D/g, \'\').slice(0, 12);\n      submit.disabled = utr.value.length !== 12;\n      clearError();\n    });\n    utr.addEventListener(\'keydown\', function (e) {\n      if (e.key === \'Enter\' && !submit.disabled) { e.preventDefault(); submit.click(); }\n    });\n  }\n\n  /* shrink big phone screenshots in the browser before uploading (saves mobile data) */\n  function compress(file, cb) {\n    var img = new Image();\n    var url = URL.createObjectURL(file);\n    img.onload = function () {\n      try {\n        var max = 1280, w = img.naturalWidth, h = img.naturalHeight;\n        var r = Math.min(1, max / Math.max(w, h));\n        var c = document.createElement(\'canvas\');\n        c.width = Math.max(1, Math.round(w * r)); c.height = Math.max(1, Math.round(h * r));\n        var ctx = c.getContext(\'2d\');\n        ctx.fillStyle = \'#fff\'; ctx.fillRect(0, 0, c.width, c.height);\n        ctx.drawImage(img, 0, 0, c.width, c.height);\n        URL.revokeObjectURL(url);\n        c.toBlob(function (blob) { cb(blob || file); }, \'image/jpeg\', 0.82);\n      } catch (e) { URL.revokeObjectURL(url); cb(file); }\n    };\n    img.onerror = function () { URL.revokeObjectURL(url); cb(file); };\n    img.src = url;\n  }\n\n  function kb(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + \' MB\' : Math.max(1, Math.round(n / 1024)) + \' KB\'; }\n\n  if (shotInput) {\n    shotInput.addEventListener(\'change\', function () {\n      clearError();\n      var f = shotInput.files && shotInput.files[0];\n      if (!f) { return; }\n      if (f.type && f.type.indexOf(\'image/\') !== 0) { showError(\'Please choose an image (JPG or PNG).\'); shotInput.value = \'\'; return; }\n      if (f.size > 25 * 1048576) { showError(\'That image is too large. Please choose a smaller screenshot.\'); shotInput.value = \'\'; return; }\n      compress(f, function (blob) {\n        shotBlob = blob;\n        $(\'shot-img\').src = URL.createObjectURL(blob);\n        $(\'shot-name\').textContent = f.name || \'screenshot\';\n        $(\'shot-size\').textContent = kb(blob.size);\n        $(\'shot-preview\').classList.remove(\'hidden\');\n        $(\'shot-pick\').classList.add(\'hidden\');\n      });\n    });\n    $(\'shot-remove\').addEventListener(\'click\', function () {\n      shotBlob = null; shotInput.value = \'\';\n      $(\'shot-preview\').classList.add(\'hidden\');\n      $(\'shot-pick\').classList.remove(\'hidden\');\n    });\n  }\n\n  if (submit) {\n    submit.addEventListener(\'click\', function () {\n      clearError();\n      if (cfg.isPreview) {\n        showError(\'Preview Mode: Set UPI_ID in Render Environment Variables and run sql/phase1_payments.sql in Supabase to submit real orders.\');\n        return;\n      }\n      if (utr.value.length !== 12) { showError(\'Enter the 12-digit UTR from your payment app.\'); utr.focus(); return; }\n      var fd = new FormData();\n      fd.append(\'utr\', utr.value);\n      if (shotBlob) { fd.append(\'screenshot\', shotBlob, \'payment.jpg\'); }\n      setBusy(true);\n      fetch(cfg.submitUrl, {\n        method: \'POST\', body: fd, credentials: \'same-origin\',\n        headers: { \'Accept\': \'application/json\' }\n      })\n        .then(function (r) {\n          return r.json().catch(function () { return { ok: false, error: \'Unexpected response. Please try again.\' }; });\n        })\n        .then(function (j) {\n          if (j && j.ok) { window.location.replace(j.redirect || window.location.href); return; }\n          setBusy(false);\n          showError((j && j.error) || \'Could not submit. Please try again.\');\n        })\n        .catch(function () {\n          setBusy(false);\n          showError(\'Network problem. Check your connection and try again.\');\n        });\n    });\n  }\n\n  /* ---------- waiting: refresh by itself once you approve ---------- */\n  if (cfg.state === \'waiting\') {\n    var polls = 0;\n    var poll = function () {\n      fetch(cfg.statusUrl, { headers: { \'Accept\': \'application/json\' }, credentials: \'same-origin\', cache: \'no-store\' })\n        .then(function (r) { return r.json(); })\n        .then(function (j) {\n          if (j && j.status && j.status !== \'submitted\') { window.location.reload(); return; }\n          next();\n        })\n        .catch(next);\n    };\n    var next = function () {\n      polls += 1;\n      if (polls >= 60) { var n = $(\'poll-note\'); if (n) { n.classList.remove(\'hidden\'); } return; }\n      setTimeout(poll, polls < 20 ? 8000 : 20000);\n    };\n    setTimeout(poll, 8000);\n  }\n})();\n</script>\n{% endblock %}\n<!-- SANGAM STUDY v2.1 — Final Fixed Build (2026-10-21) -->\n',
}


def _render_pay_template(name, **ctx):
    """
    Renders `payments/<name>` first, then `<name>`, and finally falls back to
    an embedded template string so `/pay`, `/pay/history`, and `/pay/order/...`
    never crash with TemplateNotFound even if `templates/payments/` was not uploaded.
    """
    for candidate in (f"payments/{name}", name):
        try:
            return render_template(candidate, **ctx)
        except TemplateNotFound:
            continue
    if name in _FALLBACK_PAY_TEMPLATES:
        return render_template_string(_FALLBACK_PAY_TEMPLATES[name], **ctx)
    return render_template(name, **ctx)


def _login_gate():
    """A redirect to the login page if nobody is logged in, else None."""
    if not is_logged_in():
        return redirect(url_for("auth.login"))
    return None


# ======================================================================
# Pages
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


@payments_bp.route("/pay/preview")
def preview_checkout():
    """
    Direct checkout preview page so the site owner/Admin can inspect the QR code,
    UPI link, and 12-digit UTR + screenshot form even before SQL tables or UPI_ID
    are configured on Render.
    """
    gate = _login_gate()
    if gate:
        return gate
    plan = (get_active_plans(include_fallback=True) or [dict(DEFAULT_SEASON_PASS_PLAN)])[0]
    demo_order_id = "00000000-0000-4000-8000-000000000299"
    demo_upi = pay.upi_id() if pay.payments_ready() else "yourname@fam"
    base_amt = float(plan.get("price_inr") or 299)
    payable_amt = round(base_amt + 0.37, 2)
    order = {
        "id": demo_order_id,
        "base_amount": base_amt,
        "payable_amount": payable_amt,
        "status": "created",
        "plans": plan,
    }
    upi_link = f"upi://pay?pa={demo_upi}&pn=Sangam%20Study&am={payable_amt:.2f}&cu=INR&tn=SSP-PREVIEW"
    qr = pay.qr_svg(upi_link)
    static_qr = url_for("static", filename=pay.static_qr_file()) if pay.static_qr_file() else None
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
    if state == "pay":
        upi_link = pay.build_upi_link(order["payable_amount"], order["id"])
        qr = pay.qr_svg(upi_link)
        if pay.static_qr_file():
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
# Submit UTR (+ optional screenshot)
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

    utr = re.sub(r"\D", "", request.form.get("utr", ""))
    if len(utr) != 12:
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
# Telegram webhook   (POST /tg-webhook)
# ======================================================================

_APPROVE_ERRORS = {
    "not_found": "Order not found.",
    "bad_status": "This order can't be approved now (it is {status}).",
    "user_deleted": "That student deleted their account.",
    "plan_expired": "This plan has already ended.",
    "server": "Server error. Try again, or use the web panel.",
}
_REJECT_ERRORS = {
    "not_found": "Order not found.",
    "bad_status": "This order can't be rejected now (it is {status}).",
    "server": "Server error. Try again, or use the web panel.",
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
    elif action == "x":
        code = parts[3] if len(parts) > 3 else ""
        label, reason_text = pay.REJECT_REASONS.get(code, ("Rejected", "Payment could not be verified."))
        result = pay.reject_order(order_id, reviewer, reason_text)
        decision = f"❌ <b>REJECTED</b> — {label}"
        failure = _error_text(_REJECT_ERRORS, result)
    else:
        _answer(callback_id, "Unknown action.")
        return

    if not result.get("ok"):
        _answer(callback_id, failure, alert=True)
        return

    _answer(callback_id, "Done ✅" if action == "a" else "Rejected ❌")
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
    text = (msg.get("text") or "").strip()
    command = text.split()[0].split("@")[0].lower() if text else ""

    if command in ("/start", "/id"):
        if pay.is_tg_admin(sender):
            pay.tg_send_text(
                chat_id,
                "✅ <b>Connected.</b> Payment approvals will arrive here.\n\n/pending — send me the payments still waiting",
            )
        else:
            pay.tg_send_text(
                chat_id,
                f"Your chat ID is <code>{chat_id}</code>.\n"
                "If you are the site owner, put this number in the <code>TG_ADMIN_CHAT_ID</code> setting.",
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
