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
