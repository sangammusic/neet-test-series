-- =========================================================
-- MIGRATION: Remove guest mode + add real username uniqueness
-- =========================================================
-- Run this ONCE against your existing live Supabase database.
-- It is written to be idempotent (safe to run more than once) and
-- non-destructive to real user data -- it only removes guest-mode
-- structures and tightens up the username column.
--
-- WHY THIS MIGRATION EXISTS (plain-language):
-- 1. Guest ("continue without login") mode has been permanently
--    removed from the app. Nothing will ever again create a
--    test_attempts row without a real user_id, or write to the old
--    guests / guest_streams tables.
-- 2. `profiles.username` was being read and written by the app code
--    (app/auth/routes.py, app/user/routes.py) but was NEVER actually
--    defined as a column with a UNIQUE constraint in schema.sql or
--    any earlier migration. That is the root cause of the login/
--    password "glitch" you saw -- there was no real database-level
--    guarantee that two accounts couldn't collide on the same
--    username, only a race-prone app-side check. This migration
--    fixes that for good.
--
-- ORDER OF OPERATIONS MATTERS -- run top to bottom, don't skip steps.
-- =========================================================

-- ---------------------------------------------------------
-- STEP 1: Purge any leftover guest test_attempts FIRST.
-- (You can also do this from the Admin Dashboard's
-- "Wipe Legacy Guests" button instead of running this manually --
-- either way accomplishes the same thing.)
-- ---------------------------------------------------------
delete from test_attempts where user_id is null;

-- ---------------------------------------------------------
-- STEP 2: Drop the guest_id column + its index + the old
-- either/or ownership constraint on test_attempts.
-- ---------------------------------------------------------
drop index if exists idx_attempts_guest;
alter table if exists test_attempts drop constraint if exists attempt_owner_check;
alter table if exists test_attempts drop column if exists guest_id;

-- ---------------------------------------------------------
-- STEP 3: Now that no NULL user_id rows remain (Step 1), make
-- user_id a hard requirement at the database level.
-- ---------------------------------------------------------
alter table test_attempts alter column user_id set not null;

-- ---------------------------------------------------------
-- STEP 4: Drop the guest_streams / guests tables entirely.
-- CASCADE cleans up their foreign keys automatically.
-- ---------------------------------------------------------
drop table if exists guest_streams cascade;
drop table if exists guests cascade;

-- ---------------------------------------------------------
-- STEP 5: Add `username` to profiles if it doesn't already exist
-- (it likely already exists on your live DB as a plain column added
-- by hand outside these SQL files -- this is safe either way), then
-- add the real UNIQUE index that was always missing.
--
-- NOTE: if this step fails with a "duplicate key" style error, it
-- means two existing profiles already share the same username live
-- in your database right now. You'll need to manually rename one of
-- them (UPDATE profiles SET username = '...' WHERE id = '...') before
-- re-running this step -- the unique index cannot be created while a
-- collision exists.
-- ---------------------------------------------------------
alter table profiles add column if not exists username text;
create unique index if not exists profiles_username_unique_idx on profiles (username);

-- ---------------------------------------------------------
-- STEP 6: Drop the now-unused index name from the old schema,
-- in case it lingered from before.
-- ---------------------------------------------------------
drop index if exists idx_attempts_guest;

-- =========================================================
-- Done. After this runs cleanly:
--  - Every test_attempts row is guaranteed to have a real user_id.
--  - guests / guest_streams tables no longer exist.
--  - profiles.username is guaranteed unique at the database level,
--    not just by an app-side check.
-- =========================================================
