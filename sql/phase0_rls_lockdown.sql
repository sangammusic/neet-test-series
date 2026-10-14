-- =========================================================
-- SANGAM STUDY - PHASE 0: RLS LOCKDOWN  (deny-all for browser keys)
-- =========================================================
-- WHAT THIS DOES
--   Your Supabase "anon" key is PUBLIC by design (anyone can find it in a
--   browser). Until now, most of your tables (questions, mock_questions, tests,
--   streams ...) had no Row Level Security, so anyone holding that key could
--   read - or even change - the whole question bank, answers included.
--
--   After this script:
--     * every table in schema "public" has RLS ON
--     * the roles "anon" and "authenticated" (= anything a browser can use)
--       get ZERO access to every table, view, sequence and function
--     * only your Flask server, using the service_role key, can read/write
--       data - and Flask is where all permission checks live (access.py)
--
--   You do NOT need to write "policies" for this model. RLS ON + no permissive
--   policy already means "deny". The script still adds one explicit
--   "deny_all_browser_roles" policy per table so the Supabase Security Advisor
--   shows the tables as intentionally locked instead of "RLS enabled, no policy".
--
-- WHEN TO RUN  (very important)
--   Run this LAST - after the new code is deployed on Render and you have
--   checked that login + a free practice set work. The OLD code read content
--   with the anon key, so locking the tables before deploying makes the site
--   look empty. If that ever happens: do NOT turn RLS off - deploy the new
--   code (app/extensions.py) instead.
--
-- HOW TO RUN
--   Supabase Dashboard -> SQL Editor -> New query -> paste ALL -> Run.
--   Safe to run again whenever you add new tables.
-- =========================================================


-- ---------------------------------------------------------
-- 1. RLS ON for every table in public
-- ---------------------------------------------------------
do $$
declare
    r record;
begin
    for r in select tablename from pg_tables where schemaname = 'public'
    loop
        begin
            execute format('alter table public.%I enable row level security', r.tablename);
        exception when others then
            raise notice 'skipped enabling RLS on %: %', r.tablename, sqlerrm;
        end;
    end loop;
end $$;


-- ---------------------------------------------------------
-- 2. Remove every old policy (including ones created by hand in the dashboard)
-- ---------------------------------------------------------
do $$
declare
    r record;
begin
    for r in select tablename, policyname from pg_policies where schemaname = 'public'
    loop
        begin
            execute format('drop policy if exists %I on public.%I', r.policyname, r.tablename);
        exception when others then
            raise notice 'skipped dropping policy % on %: %', r.policyname, r.tablename, sqlerrm;
        end;
    end loop;
end $$;


-- ---------------------------------------------------------
-- 3. One explicit deny-all policy per table
-- ---------------------------------------------------------
do $$
declare
    r record;
begin
    for r in select tablename from pg_tables where schemaname = 'public'
    loop
        begin
            execute format(
                'create policy deny_all_browser_roles on public.%I as restrictive for all to anon, authenticated using (false) with check (false)',
                r.tablename
            );
        exception when others then
            raise notice 'skipped policy on %: %', r.tablename, sqlerrm;
        end;
    end loop;
end $$;


-- ---------------------------------------------------------
-- 4. Belt and braces: take the privileges away too
--    (even if RLS were switched off by mistake one day, the browser roles
--     would still be refused)
-- ---------------------------------------------------------
revoke all on all tables    in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke execute on all functions in schema public from public, anon, authenticated;

-- your server keeps full access
grant all on all tables    in schema public to service_role;
grant all on all sequences in schema public to service_role;
grant execute on all functions in schema public to service_role;

-- tables you create in future should start closed as well
alter default privileges in schema public revoke all on tables    from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;


-- ---------------------------------------------------------
-- 5. CHECK: every row below must say rls_enabled = true
-- ---------------------------------------------------------
select c.relname  as table_name,
       c.relrowsecurity as rls_enabled
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
 where n.nspname = 'public'
   and c.relkind = 'r'
 order by c.relrowsecurity, c.relname;
