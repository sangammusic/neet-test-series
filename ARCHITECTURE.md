# SANGAM STUDY — Architecture

## Why Blueprints?

Flask "Blueprints" let you split one app into self-contained modules —
each with its own routes, templates, and (optionally) static files —
that get registered onto the main app. This is exactly what you need
to keep admin logic from ever touching user/guest logic:

- `app/admin/` never imports from `app/user/`
- `app/user/` never imports from `app/admin/`
- Both share only `app/shared/` (db client, auth helpers, decorators)

If you ever need to hand off the admin panel to a different developer,
or rip it out entirely, it's one folder — not scattered `if is_admin`
checks across every route file.

## Folder Structure

## Phase 0 / Phase 1 additions (security + payments)

**Rule of the house:** browsers never talk to the database. Every query goes through
Flask using the Supabase *service-role* key, and every permission check lives in ONE
place: `app/shared/access.py`. The database itself is locked for browser keys
(`sql/phase0_rls_lockdown.sql`: RLS on, deny-all), so a leaked anon key reads nothing.

| File | What it does |
|---|---|
| `app/extensions.py` | `supabase_admin` (service role, used for ALL data). `supabase_public` is a legacy alias of it. `new_auth_client()` = throw-away anon client used only to check a password. |
| `app/shared/access.py` | The single "may this person see premium content?" rule: admin OR active subscription. Fails closed. Also `premium_status()` for templates. |
| `app/shared/security.py` | CSRF origin guard, rate limiter, Turnstile check, security headers, friendly error pages. No third-party dependencies. |
| `app/shared/payments_core.py` | Everything about money that isn't a web page: SQL-function wrappers, UPI link + QR, screenshot cleaning, Telegram helpers. |
| `app/payments.py` | Student pages (`/pay`, checkout, submit UTR, history) and the Telegram webhook `/tg-webhook`. |
| `app/admin/payment_routes.py` | `/admin/payments`: approve / reject / revoke, revenue, Telegram setup. |
| `app/legal.py` | `/terms`, `/privacy`, `/refund-policy` (text lives in `templates/legal/`). |
| `sql/phase1_payments.sql` | `plans`, `orders`, `subscriptions` + atomic functions (`create_order`, `submit_order`, `approve_order`, ...). |
| `tests/` | `python -m unittest discover -s tests -v` (uses an in-memory fake, never touches real data). |

Dependency direction is unchanged: `app/admin` and `app/payments` both use `app/shared`,
never each other.
