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

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase_public: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
