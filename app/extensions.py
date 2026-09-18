"""
Central place for the Supabase client(s).

Two clients on purpose:
  - `supabase_public` uses the ANON key — safe-ish permissions,
    respects Row Level Security. Used by user/guest-facing routes.
  - `supabase_admin` uses the SERVICE ROLE key — bypasses RLS
    entirely. Used ONLY inside app/admin/*. Never expose this
    client's results directly to a user-facing route.

Both are created once here and imported everywhere else, so we're
not spinning up a new client per-request.
"""
import os
from supabase import create_client, Client
from flask_caching import Cache

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase_public: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# Process-local in-memory cache (SimpleCache). Deliberately NOT Redis/
# Memcached -- on Render's free 512MB instance, adding a separate cache
# service is both an extra thing to pay for/manage and extra RAM
# pressure. SimpleCache lives inside this same Python process, costs
# next to nothing for the small, rarely-changing lookups it's used for
# here (active streams, difficulty levels, test categories -- see
# app/shared/models.py), and is reset automatically on every deploy.
# It does NOT share state across multiple gunicorn worker processes,
# which is fine for this use case: each worker independently caches
# the same small, slow-changing rows, so worst case is a handful of
# redundant Supabase reads right after a restart, not a correctness bug.
cache = Cache(config={"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 600})
