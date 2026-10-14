-- =========================================================
-- MIGRATION: per-question image support (diagrams for
-- Physics/Biology/Chemistry questions)
-- =========================================================
-- Context:
--   Bulk-paste JSON already supported a free-text `image_url` column
--   on both `questions` and `mock_questions`, but there was no actual
--   upload mechanism anywhere — admin had to already have the image
--   hosted somewhere else and paste a URL by hand, which doesn't
--   scale to hundreds of diagram-based questions.
--
--   This migration adds a `has_image` boolean flag, set from the
--   bulk-paste JSON (see admin/question_routes.py and
--   admin/mock_question_routes.py), which drives a per-question
--   "Upload Image" prompt in the admin UI immediately after a batch
--   is pasted. The existing `image_url` column keeps storing the
--   final Supabase Storage public URL once the image is uploaded —
--   nothing about that column changes, this only adds a way to know
--   WHICH questions are still waiting on an image.
--
--   Run this once against Supabase. Safe to re-run.
-- =========================================================

do $$
begin
    if not exists (
        select 1 from information_schema.columns
        where table_name = 'questions' and column_name = 'has_image'
    ) then
        alter table questions add column has_image boolean not null default false;
    end if;
end $$;

do $$
begin
    if not exists (
        select 1 from information_schema.columns
        where table_name = 'mock_questions' and column_name = 'has_image'
    ) then
        alter table mock_questions add column has_image boolean not null default false;
    end if;
end $$;

-- Backfill: any row that already has an image_url set clearly "has an
-- image" even though it predates this column — mark those true so
-- they don't show up in the "needs upload" list.
update questions set has_image = true where image_url is not null and image_url != '';
update mock_questions set has_image = true where image_url is not null and image_url != '';

-- Index to make "which questions still need an image" fast to query
-- (used by the admin pending-uploads view).
create index if not exists idx_questions_needs_image
    on questions(chapter_id) where has_image = true and (image_url is null or image_url = '');
create index if not exists idx_mock_questions_needs_image
    on mock_questions(stream_id) where has_image = true and (image_url is null or image_url = '');

-- =========================================================
-- MANUAL STEP — Supabase Storage bucket (cannot be created via SQL,
-- do this once in the Supabase Dashboard):
--
-- 1. Go to Storage in the Supabase Dashboard.
-- 2. Create a new bucket named exactly:  question-images
-- 3. Set it to PUBLIC (so image_url can be a plain public link the
--    <img> tag can load directly, no signed-URL logic needed).
-- 4. That's it — no size limit needs to be set manually; the app
--    compresses every image to well under 200KB before upload (see
--    app/admin/image_routes.py), so a 1GB Free-tier project can hold
--    several thousand images even without a bucket-level cap.
-- =========================================================
