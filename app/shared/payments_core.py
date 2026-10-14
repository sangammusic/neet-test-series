"""
payments_core.py -- everything about money that is NOT a web page.

Both the student-facing blueprint (app/payments.py) and the admin panel
(app/admin/payment_routes.py) use this file, so neither has to import the other.

Contents
  1. Config            UPI id / name, Telegram token / admin id
  2. Order database    thin wrappers over the SQL functions in sql/phase1_payments.sql
  3. UPI link + QR     the QR is drawn on OUR server, with the exact amount inside it
  4. Screenshot        re-encoded to a small clean JPEG (EXIF/GPS stripped)
  5. Telegram          send approval card, edit it, fetch the screenshot, set the webhook

Nothing in here ever logs the bot token or the screenshot.
"""
import hashlib
import hmac
import html
import io
import json
import logging
import os
import re
import uuid
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import quote

from app.extensions import supabase_admin
from app.shared.access import forget_premium, format_inr, format_ist_datetime

try:  # both are listed in requirements.txt; if one is missing the feature degrades, the site still boots
    import httpx
except Exception:  # pragma: no cover
    httpx = None
try:
    import segno
except Exception:  # pragma: no cover
    segno = None

logger = logging.getLogger(__name__)


# ======================================================================
# 1. CONFIG
# ======================================================================

_UPI_RE = re.compile(r"^[A-Za-z0-9._\-]{2,100}@[A-Za-z][A-Za-z0-9]{1,50}$")


def upi_id():
    return (os.environ.get("UPI_ID") or "").strip()


def upi_payee_name():
    return (os.environ.get("UPI_PAYEE_NAME") or "Sangam Study").strip()[:40] or "Sangam Study"


def payments_ready():
    """True when a valid UPI id is configured (otherwise /pay shows 'not available yet')."""
    return bool(_UPI_RE.match(upi_id()))


def static_qr_file():
    """Optional fallback picture under /static, e.g. UPI_STATIC_QR_FILE=img/fampay_qr.png"""
    return (os.environ.get("UPI_STATIC_QR_FILE") or "").strip().lstrip("/")


def is_uuid(value):
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def short_ref(order_id):
    """8-character reference shown to students and admins, e.g. 'A1B2C3D4'."""
    return str(order_id).replace("-", "")[:8].upper()


# ======================================================================
# 2. ORDER DATABASE
# ======================================================================

SUBMIT_ERRORS = {
    "utr_used": "This UTR has already been submitted. Please check the number — or contact support if you think this is a mistake.",
    "amount_reassigned": "Your payment window ended and this amount was given to another payment. Please start a new payment, and contact support with your UTR if money was deducted.",
    "already_submitted": "You already submitted a different UTR for this payment. Please contact support if it needs to be changed.",
    "already_approved": "This payment is already approved.",
    "closed": "This payment is closed. Please start a new one.",
    "not_found": "We couldn't find this payment.",
    "server": "Something went wrong on our side. Please try again in a moment.",
}

_ORDER_COLS = (
    "id, user_id, plan_id, base_amount, payable_amount, status, utr, tg_file_id, tg_message_id, "
    "reject_reason, reviewed_by, reviewed_at, submitted_at, expires_at, created_at"
)
_ORDER_EMBED = _ORDER_COLS + ", plans(name, code, valid_until), profiles(username, full_name)"


def _rpc(name, params):
    """Calls a SQL function; always returns a dict ({'ok': False, 'error': 'server'} on any failure)."""
    try:
        data = supabase_admin.rpc(name, params).execute().data
        if isinstance(data, list) and data:
            data = data[0]
        if isinstance(data, dict):
            return data
        return {"ok": False, "error": "server"}
    except Exception as e:
        logger.error(f"RPC {name} failed: {e}")
        return {"ok": False, "error": "server"}


def create_order(user_id, plan_code):
    return _rpc("create_order", {"p_user_id": user_id, "p_plan_code": plan_code})


def submit_order(order_id, user_id, utr):
    return _rpc("submit_order", {"p_order_id": order_id, "p_user_id": user_id, "p_utr": utr})


def approve_order(order_id, reviewer):
    res = _rpc("approve_order", {"p_order_id": order_id, "p_reviewer": reviewer})
    if res.get("ok"):
        forget_premium(res.get("user_id"))
    return res


def reject_order(order_id, reviewer, reason):
    return _rpc("reject_order", {"p_order_id": order_id, "p_reviewer": reviewer, "p_reason": reason})


def revoke_order(order_id, reviewer, reason):
    res = _rpc("revoke_order", {"p_order_id": order_id, "p_reviewer": reviewer, "p_reason": reason})
    if res.get("ok"):
        forget_premium(res.get("user_id"))
    return res


def payments_summary():
    res = _rpc("payments_summary", {})
    keys = ("revenue_today", "revenue_month", "revenue_total", "approved_count", "pending_count", "rejected_count")
    return {k: res.get(k, 0) or 0 for k in keys}


def get_order(order_id):
    """Any order by id (admin / webhook use). None if missing."""
    if not is_uuid(order_id):
        return None
    try:
        rows = supabase_admin.table("orders").select(_ORDER_EMBED).eq("id", order_id).limit(1).execute().data
        return rows[0] if rows else None
    except Exception as e:
        logger.error(f"Error fetching order {order_id}: {e}")
        return None


def get_order_for_user(order_id, user_id):
    """The order, but ONLY if it belongs to this user (stops people opening each other's payments)."""
    if not is_uuid(order_id) or not user_id:
        return None
    try:
        rows = (
            supabase_admin.table("orders").select(_ORDER_EMBED)
            .eq("id", order_id).eq("user_id", user_id).limit(1).execute().data
        )
        return rows[0] if rows else None
    except Exception as e:
        logger.error(f"Error fetching order {order_id} for user {user_id}: {e}")
        return None


def list_orders_for_user(user_id, limit=50):
    if not user_id:
        return []
    try:
        return (
            supabase_admin.table("orders").select(_ORDER_EMBED)
            .eq("user_id", user_id).order("created_at", desc=True).limit(limit).execute().data or []
        )
    except Exception as e:
        logger.error(f"Error listing orders for user {user_id}: {e}")
        return []


def get_open_order_for_user(user_id):
    """The student's unfinished payment (created / submitted), newest first, or None."""
    if not user_id:
        return None
    try:
        rows = (
            supabase_admin.table("orders").select(_ORDER_EMBED)
            .eq("user_id", user_id).in_("status", ["created", "submitted"])
            .order("created_at", desc=True).limit(1).execute().data
        )
        return rows[0] if rows else None
    except Exception as e:
        logger.error(f"Error fetching open order for user {user_id}: {e}")
        return None


def list_orders_admin(statuses=None, limit=100):
    try:
        q = supabase_admin.table("orders").select(_ORDER_EMBED)
        if statuses:
            q = q.in_("status", list(statuses))
        return q.order("created_at", desc=True).limit(limit).execute().data or []
    except Exception as e:
        logger.error(f"Error listing orders for admin: {e}")
        return []


def save_telegram_refs(order_id, message_id, file_id):
    """Remembers which Telegram message / screenshot belongs to an order (best effort)."""
    patch = {}
    if message_id is not None:
        patch["tg_message_id"] = message_id
    if file_id:
        patch["tg_file_id"] = file_id
    if not patch:
        return
    try:
        supabase_admin.table("orders").update(patch).eq("id", order_id).execute()
    except Exception as e:
        logger.error(f"Could not save Telegram refs for order {order_id}: {e}")


# ======================================================================
# 3. UPI LINK + QR
# ======================================================================

def amount_text(amount):
    """299.37 -> '299.37' (exactly two decimals, no float surprises)."""
    return str(Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def build_upi_link(amount, order_id):
    """
    upi://pay?pa=<id>&pn=<name>&am=<amount>&cu=INR&tn=<note>
    Opening this on a phone launches the student's UPI app with everything pre-filled.
    """
    note = "SSP-" + short_ref(order_id)[:6]
    params = (
        ("pa", upi_id()),
        ("pn", upi_payee_name()),
        ("am", amount_text(amount)),
        ("cu", "INR"),
        ("tn", note),
    )
    return "upi://pay?" + "&".join(f"{k}={quote(str(v), safe='@._-')}" for k, v in params)


def matrix_to_svg(matrix, border=4, dark="#0F1729", light="#FFFFFF"):
    """Turns a QR matrix (rows of 0/1) into one compact inline <svg> that scales with CSS."""
    rows = len(matrix)
    cols = len(matrix[0]) if rows else 0
    width, height = cols + 2 * border, rows + 2 * border
    parts = []
    for y, row in enumerate(matrix):
        x = 0
        while x < cols:
            if row[x]:
                start = x
                while x < cols and row[x]:
                    x += 1
                parts.append(f"M{start + border},{y + border}h{x - start}v1h-{x - start}z")
            else:
                x += 1
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'shape-rendering="crispEdges" role="img" aria-label="UPI payment QR code">'
        f'<rect width="{width}" height="{height}" fill="{light}"/>'
        f'<path d="{"".join(parts)}" fill="{dark}"/></svg>'
    )


def qr_svg(data):
    """SVG string for `data`, or None if the QR library isn't available."""
    if segno is None:
        return None
    try:
        qr = segno.make(data, error="m", micro=False)
        return matrix_to_svg([list(row) for row in qr.matrix])
    except Exception as e:
        logger.error(f"QR generation failed: {type(e).__name__}")
        return None


# ======================================================================
# 4. SCREENSHOT
# ======================================================================

MAX_UPLOAD_BYTES = 6 * 1024 * 1024


class ScreenshotError(ValueError):
    """code: empty | too_large | unreadable | bad_type"""


SCREENSHOT_MESSAGES = {
    "empty": "The screenshot file was empty. Please choose it again.",
    "too_large": "That screenshot is too large. Please choose one under 6 MB.",
    "unreadable": "We couldn't read that image. Please upload a normal JPG or PNG screenshot.",
    "bad_type": "Please upload a JPG, PNG or WebP image.",
}


def sanitize_screenshot(raw):
    """
    Re-encodes an uploaded image as a small, clean JPEG:
      * proves it really is a JPEG/PNG/WebP picture (not a renamed file)
      * strips EXIF / GPS / other metadata
      * shrinks it to at most 1280 px so it forwards to Telegram quickly
    Returns JPEG bytes or raises ScreenshotError.
    """
    if not raw:
        raise ScreenshotError("empty")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ScreenshotError("too_large")
    from PIL import Image, ImageOps

    try:
        Image.open(io.BytesIO(raw)).verify()
        img = Image.open(io.BytesIO(raw))
    except Exception:
        raise ScreenshotError("unreadable")
    if (img.format or "").upper() not in ("JPEG", "PNG", "WEBP"):
        raise ScreenshotError("bad_type")
    if img.width * img.height > 50_000_000:           # refuse "decompression bomb" pictures
        raise ScreenshotError("too_large")
    try:
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.split()[-1])
            img = flat
        else:
            img = img.convert("RGB")
        img.thumbnail((1280, 1280))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=82, optimize=True)
        return out.getvalue()
    except Exception:
        raise ScreenshotError("unreadable")


# ======================================================================
# 5. TELEGRAM
# ======================================================================

def tg_token():
    return (os.environ.get("TG_TOKEN") or "").strip()


def tg_admin_id():
    """Your personal Telegram chat id (a number) from TG_ADMIN_CHAT_ID, or None."""
    try:
        return int((os.environ.get("TG_ADMIN_CHAT_ID") or "").strip())
    except ValueError:
        return None


def tg_enabled():
    return bool(httpx is not None and tg_token() and tg_admin_id() is not None)


def is_tg_admin(telegram_user_id):
    admin = tg_admin_id()
    return admin is not None and telegram_user_id == admin


def tg_webhook_secret():
    """
    Telegram echoes this in the X-Telegram-Bot-Api-Secret-Token header of every
    webhook call; we reject anything without it. Derived from SECRET_KEY so you
    don't need yet another env var (set TG_WEBHOOK_SECRET to override).
    """
    explicit = (os.environ.get("TG_WEBHOOK_SECRET") or "").strip()
    if explicit:
        return explicit
    key = (os.environ.get("SECRET_KEY") or "").encode("utf-8")
    return hmac.new(key, b"sangam-tg-webhook-v1", hashlib.sha256).hexdigest()


def tg_call(method, *, json_body=None, data=None, files=None, timeout=10):
    """
    Calls the Telegram Bot API. Returns the 'result' (which may be True) or None on any failure.
    Never logs the URL (it contains the bot token).
    """
    token = tg_token()
    if not token or httpx is None:
        return None
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        if files is not None or data is not None:
            resp = httpx.post(url, data=data, files=files, timeout=timeout)
        else:
            resp = httpx.post(url, json=json_body or {}, timeout=timeout)
        payload = resp.json()
    except Exception as e:
        logger.error("Telegram %s failed: %s", method, type(e).__name__)
        return None
    if not isinstance(payload, dict) or not payload.get("ok"):
        desc = payload.get("description") if isinstance(payload, dict) else ""
        logger.warning("Telegram %s was rejected: %s", method, str(desc)[:200])
        return None
    result = payload.get("result")
    return True if result is None else result


def _esc(value):
    return html.escape(str(value if value is not None else ""), quote=False)


REJECT_REASONS = {
    "1": ("UTR not found",
          "We couldn't find a payment with this UTR. Please check the 12-digit UTR and submit again."),
    "2": ("Wrong amount",
          "The amount paid doesn't match the amount shown on the payment page. Please pay the exact amount shown."),
    "3": ("Not received",
          "We haven't received this payment yet. If money was deducted, please contact support with your screenshot."),
    "4": ("Unclear proof",
          "The screenshot wasn't clear enough to verify. Please submit again with a clear screenshot."),
}


def approval_keyboard(order_id):
    return {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": f"ord:a:{order_id}"},
        {"text": "❌ Reject", "callback_data": f"ord:r:{order_id}"},
    ]]}


def reject_keyboard(order_id):
    rows = [
        [{"text": f"❌ {label}", "callback_data": f"ord:x:{order_id}:{code}"}]
        for code, (label, _text) in REJECT_REASONS.items()
    ]
    rows.append([{"text": "⬅ Back", "callback_data": f"ord:b:{order_id}"}])
    return {"inline_keyboard": rows}


def format_admin_message(order, decision_text=None, note=None):
    plan = order.get("plans") or {}
    prof = order.get("profiles") or {}
    lines = [
        "💰 <b>Payment to verify</b>",
        "",
        f"👤 <b>{_esc(prof.get('full_name') or '—')}</b> (@{_esc(prof.get('username') or '—')})",
        f"🎟 {_esc(plan.get('name') or 'Plan')}",
        f"💵 Amount: <b>{_esc(format_inr(order.get('payable_amount'), always_paise=True))}</b>",
        f"🔢 UTR: <code>{_esc(order.get('utr') or '—')}</code>",
        f"🕒 {_esc(format_ist_datetime(order.get('submitted_at') or order.get('created_at')))}",
        f"🧾 Order <code>{_esc(short_ref(order.get('id')))}</code>",
    ]
    if note:
        lines += ["", _esc(note)]
    if decision_text:
        lines += ["", decision_text]
    return "\n".join(lines)


def tg_send_order(order, photo_bytes=None):
    """
    Sends the approval card (with ✅/❌ buttons) to your Telegram chat.
    Returns {'message_id': int|None, 'file_id': str|None}, or None if nothing could be sent.
    """
    if not tg_enabled():
        return None
    admin = tg_admin_id()
    markup = approval_keyboard(order["id"])

    if photo_bytes:
        result = tg_call(
            "sendPhoto",
            data={
                "chat_id": str(admin),
                "caption": format_admin_message(order),
                "parse_mode": "HTML",
                "reply_markup": json.dumps(markup),
            },
            files={"photo": ("payment.jpg", photo_bytes, "image/jpeg")},
            timeout=25,
        )
        if isinstance(result, dict):
            photos = result.get("photo") or []
            return {
                "message_id": result.get("message_id"),
                "file_id": photos[-1].get("file_id") if photos else None,
            }
        note = "📎 The screenshot could not be forwarded."
    else:
        note = None

    result = tg_call("sendMessage", json_body={
        "chat_id": admin,
        "text": format_admin_message(order, note=note),
        "parse_mode": "HTML",
        "reply_markup": markup,
    })
    if isinstance(result, dict):
        return {"message_id": result.get("message_id"), "file_id": None}
    return None


def tg_resend_existing(order):
    """Re-sends an approval card for an order that is still waiting (re-uses the stored screenshot)."""
    if not tg_enabled():
        return None
    admin = tg_admin_id()
    markup = approval_keyboard(order["id"])
    if order.get("tg_file_id"):
        result = tg_call("sendPhoto", json_body={
            "chat_id": admin, "photo": order["tg_file_id"],
            "caption": format_admin_message(order), "parse_mode": "HTML", "reply_markup": markup,
        })
        if isinstance(result, dict):
            return {"message_id": result.get("message_id"), "file_id": order["tg_file_id"]}
    result = tg_call("sendMessage", json_body={
        "chat_id": admin, "text": format_admin_message(order),
        "parse_mode": "HTML", "reply_markup": markup,
    })
    if isinstance(result, dict):
        return {"message_id": result.get("message_id"), "file_id": None}
    return None


def tg_edit_card(chat_id, message_id, text, is_photo):
    """Replaces a card's text (caption for photo cards) and removes its buttons."""
    empty = {"inline_keyboard": []}
    if is_photo:
        return tg_call("editMessageCaption", json_body={
            "chat_id": chat_id, "message_id": message_id, "caption": text,
            "parse_mode": "HTML", "reply_markup": empty,
        })
    return tg_call("editMessageText", json_body={
        "chat_id": chat_id, "message_id": message_id, "text": text,
        "parse_mode": "HTML", "reply_markup": empty,
    })


def tg_update_order_card(order, decision_text):
    """Edits the ORIGINAL approval card (the one saved on the order) to show the final decision."""
    message_id = order.get("tg_message_id")
    if not (tg_enabled() and message_id):
        return
    tg_edit_card(
        tg_admin_id(), message_id,
        format_admin_message(order, decision_text=decision_text),
        bool(order.get("tg_file_id")),
    )


def tg_fetch_file(file_id):
    """Downloads a file from Telegram (used to show the screenshot to the admin). Bytes or None."""
    if not file_id or httpx is None:
        return None
    info = tg_call("getFile", json_body={"file_id": file_id})
    path = info.get("file_path") if isinstance(info, dict) else None
    if not path:
        return None
    try:
        resp = httpx.get(f"https://api.telegram.org/file/bot{tg_token()}/{path}", timeout=15)
        return resp.content if resp.status_code == 200 else None
    except Exception as e:
        logger.error("Telegram file download failed: %s", type(e).__name__)
        return None


def tg_set_webhook(base_url):
    """Points the bot at https://<your site>/tg-webhook, protected by the secret header."""
    url = base_url.rstrip("/") + "/tg-webhook"
    ok = tg_call("setWebhook", json_body={
        "url": url,
        "secret_token": tg_webhook_secret(),
        "allowed_updates": ["message", "callback_query"],
        "max_connections": 10,
    })
    return (ok is not None), url


def tg_webhook_info():
    info = tg_call("getWebhookInfo", timeout=5)
    return info if isinstance(info, dict) else None


def tg_send_text(chat_id, text):
    return tg_call("sendMessage", json_body={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
