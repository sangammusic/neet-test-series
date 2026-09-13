-- =========================================================
-- RESET SCRIPT — run this FIRST, then run schema.sql fresh
-- =========================================================
-- CASCADE removes dependent objects too (foreign keys, indexes,
-- policies, the test_categories -> tests -> test_questions chain
-- etc.) so we don't hit leftover-constraint errors on the next run.
--
-- Order doesn't actually matter here because CASCADE handles it,
-- but listed roughly leaf-to-root for readability.
-- =========================================================

drop table if exists test_access_grants cascade;
drop table if exists transactions cascade;
drop table if exists attempt_answers cascade;
drop table if exists test_attempts cascade;
drop table if exists test_questions cascade;
drop table if exists tests cascade;
drop table if exists test_categories cascade;
drop table if exists questions cascade;
drop table if exists difficulty_levels cascade;
drop table if exists chapters cascade;
drop table if exists subjects cascade;
drop table if exists guest_streams cascade;
drop table if exists user_streams cascade;
drop table if exists guests cascade;
drop table if exists streams cascade;
drop table if exists profiles cascade;
drop table if exists roles cascade;

-- Drop the helper function too — it gets recreated by schema.sql,
-- but `create or replace` will fail if the return type ever changes,
-- so cleanest to drop it here.
drop function if exists is_admin(uuid);
