# SANGAM STUDY v2.2 — Handcrafted UI Build (2026-10-21)
"""
Central place for the Supabase client(s) and the cache.

SECURITY MODEL (Phase 0)
------------------------
Browsers never talk to the database. Every query goes through THIS Flask
server, and every permission check lives in Flask (see app/shared/access.py).
The database itself is locked for browser keys by sql/phase0_rls_lockdown.sql
(RLS deny-all), so even a leaked anon key can read nothing.

  * `supabase_admin`  -- SERVICE ROLE key. Used for ALL database reads/writes.
                         It must NEVER sign a user in: calling sign_in on a
                         client changes the key that client sends afterwards.
  * `supabase_public` -- LEGACY NAME, kept so old imports keep working.
                         It is the very same object as `supabase_admin`.
                         (It used to be an anon-key client that every login
                         mutated -- one user's login could change what other
                         users' queries ran as. That is gone.)
  * `new_auth_client()` -- a brand-new ANON-key client, created per call, used
                         ONLY to check a username/password with Supabase Auth.
                         It is thrown away right after, so no login state is
                         ever shared between requests.
"""
import os
from supabase import create_client, Client
from flask_caching import Cache

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# Legacy alias (see docstring). Do not create a second client here.
supabase_public: Client = supabase_admin


def new_auth_client() -> Client:
    """Fresh, isolated anon-key client. Use only for sign_in_with_password()."""
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


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
