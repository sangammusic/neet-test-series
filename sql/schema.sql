-- =========================================================
-- SANGAM STUDY — Core Database Schema (Supabase / Postgres)
-- =========================================================
-- Design notes:
-- 1. Everything content-related hangs off `streams`, and streams
--    are just rows — Admin can add "JEE", "NEET", "Class 11" etc.
--    without touching code.
-- 2. Users can belong to MULTIPLE streams (many-to-many), so a
--    student prepping for Class 12 + JEE isn't forced to pick one.
-- 3. Guest (no-login) mode has been PERMANENTLY REMOVED from the
--    app. Every visitor must register/login before doing anything.
--    The `guests` / `guest_streams` tables and the `guest_id` column
--    on `test_attempts` that used to support that mode are dropped
--    below (see the DROP statements right after this notice) --
--    this schema file is the source of truth for the new, no-guest
--    shape of the database. If you're re-reading old code/docs that
--    still mention guest_id, that's expected leftover history.
-- 4. Difficulty is a lookup table, not a hardcoded enum, so Admin
--    can rename/add levels later without a migration.
-- 5. UPDATED: Schema now acts as the absolute Single Source of Truth,
--    incorporating mock test structures and durable attempt tracking.
-- =========================================================

-- ---------- EXTENSIONS ----------
create extension if not exists "uuid-ossp";
create extension if not exists pgcrypto;

-- ---------- GUEST MODE REMOVAL ----------
-- Guest mode is gone for good. Drop its tables outright (cascades
-- to guest_streams and clears any guest_id FK on test_attempts).
-- Safe to run repeatedly -- a fresh install has nothing to drop here.
drop table if exists guest_streams cascade;
drop table if exists guests cascade;

-- If test_attempts already exists from before this fix, drop its old
-- guest index, guest_id column, and the two-way owner-check
-- constraint that referenced it -- the table definition further
-- below recreates user_id as NOT NULL, which is the real fix.
drop index if exists idx_attempts_guest;
alter table if exists test_attempts drop column if exists guest_id;
alter table if exists test_attempts drop constraint if exists attempt_owner_check;

-- ---------- ENUM-LIKE LOOKUP: roles ----------
create table if not exists roles (
    id          smallint primary key,
    name        text unique not null   -- 'student', 'admin', 'moderator'
);
insert into roles (id, name) values
    (1, 'student'), (2, 'admin'), (3, 'moderator')
on conflict (id) do nothing;

-- ---------- USERS ----------
-- Extends Supabase auth.users (auth handled by Supabase Auth).
-- This table is 1:1 with auth.users via the same UUID primary key.
--
-- `username` is the app's real-world login handle (see
-- app/auth/routes.py -- it's turned into a dummy "<username>@sangam.local"
-- email under the hood for Supabase Auth). The UNIQUE constraint here
-- is the actual, DB-level guarantee that two people can never hold
-- the same username at the same time -- the app also does a
-- friendly pre-check before hitting this, but this constraint is
-- what makes that check trustworthy instead of just a race-prone
-- courtesy check. If a user changes their username or deletes their
-- account, the old value becomes free again immediately (there is no
-- separate "reserved/retired usernames" list by design).
create table if not exists profiles (
    id              uuid primary key references auth.users(id) on delete cascade,
    username        text,
    full_name       text,
    phone           text,
    role_id         smallint not null default 1 references roles(id),
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- Idempotent add + unique index, for databases where `profiles`
-- already existed before `username` was added to this schema file.
alter table profiles add column if not exists username text;
create unique index if not exists profiles_username_unique_idx on profiles (username);

-- ---------- STREAMS (dynamic, admin-managed) ----------
create table if not exists streams (
    id              uuid primary key default uuid_generate_v4(),
    name            text unique not null,       -- 'NEET', 'JEE', 'Class 11', 'Class 12'
    slug            text unique not null,       -- 'neet', 'jee', 'class-11'
    description     text,
    icon_url        text,
    display_order   int not null default 0,
    is_active       boolean not null default true,
    created_by      uuid references profiles(id),
    created_at      timestamptz not null default now()
);

-- Many-to-many: a user can prep for multiple streams
create table if not exists user_streams (
    user_id         uuid not null references profiles(id) on delete cascade,
    stream_id       uuid not null references streams(id) on delete cascade,
    joined_at       timestamptz not null default now(),
    primary key (user_id, stream_id)
);

-- ---------- SUBJECTS (per stream, admin-managed) ----------
create table if not exists subjects (
    id              uuid primary key default uuid_generate_v4(),
    stream_id       uuid not null references streams(id) on delete cascade,
    name            text not null,              -- 'Physics', 'Chemistry', 'Biology'
    slug            text not null,
    display_order   int not null default 0,
    is_active       boolean not null default true,
    unique (stream_id, slug)
);

-- ---------- CHAPTERS (per subject) ----------
create table if not exists chapters (
    id              uuid primary key default uuid_generate_v4(),
    subject_id      uuid not null references subjects(id) on delete cascade,
    name            text not null,              -- 'Laws of Motion'
    slug            text not null,
    display_order   int not null default 0,
    is_active       boolean not null default true,
    unique (subject_id, slug)
);

-- ---------- DIFFICULTY LEVELS (lookup, admin-editable) ----------
create table if not exists difficulty_levels (
    id              smallint primary key,
    name            text unique not null,       -- 'Easy', 'Medium', 'Hard'
    display_order   int not null default 0
);
insert into difficulty_levels (id, name, display_order) values
    (1, 'Easy', 1), (2, 'Medium', 2), (3, 'Hard', 3)
on conflict (id) do nothing;

-- ---------- QUESTIONS (Chapter-wise pool) ----------
-- question_text/options support LaTeX (MathJax renders client-side,
-- so we just store raw text containing \( ... \) delimiters).
create table if not exists questions (
    id              uuid primary key default uuid_generate_v4(),
    chapter_id      uuid not null references chapters(id) on delete cascade,
    difficulty_id   smallint not null references difficulty_levels(id),
    question_text   text not null,
    option_a        text not null,
    option_b        text not null,
    option_c        text not null,
    option_d        text not null,
    correct_option  char(1) not null check (correct_option in ('A','B','C','D')),
    explanation     text,
    is_pyq          boolean not null default false,   -- Previous Year Question flag
    pyq_year        int,
    image_url       text,                              -- optional diagram
    created_by      uuid references profiles(id),
    created_at      timestamptz not null default now()
);

-- ---------- MOCK QUESTIONS (Flat, mixed-subject pool) ----------
create table if not exists mock_questions (
    id              uuid primary key default uuid_generate_v4(),
    stream_id       uuid not null references streams(id) on delete cascade,
    subject_id      uuid not null references subjects(id),
    topic_name      text not null,
    difficulty_id   smallint references difficulty_levels(id),
    question_text   text not null,
    option_a        text not null,
    option_b        text not null,
    option_c        text not null,
    option_d        text not null,
    correct_option  char(1) not null check (correct_option in ('A','B','C','D')),
    explanation     text,
    is_pyq          boolean not null default false,
    pyq_year        int,
    image_url       text,
    is_premium      boolean not null default false,
    created_by      uuid references profiles(id),
    created_at      timestamptz not null default now()
);

-- ---------- TEST CATEGORIES (admin-managed groupings) ----------
-- e.g. 'Subject-wise', 'Full Syllabus Mock', 'Half Syllabus Mock', 'PYQ Set'
create table if not exists test_categories (
    id              uuid primary key default uuid_generate_v4(),
    stream_id       uuid not null references streams(id) on delete cascade,
    name            text not null,
    slug            text not null,
    is_premium      boolean not null default false,   -- Free vs Paid at category level
    display_order   int not null default 0,
    unique (stream_id, slug)
);

-- ---------- TESTS ----------
create table if not exists tests (
    id              uuid primary key default uuid_generate_v4(),
    category_id     uuid not null references test_categories(id) on delete cascade,
    stream_id       uuid not null references streams(id) on delete cascade,
    subject_id      uuid references subjects(id),      -- nullable: full mocks span subjects
    chapter_id      uuid references chapters(id),      -- nullable: only for chapter-wise
    title           text not null,
    description     text,
    duration_minutes int not null default 60,
    total_marks     int,
    negative_marking numeric(4,2) default 0,
    is_premium      boolean not null default false,
    price_inr       numeric(8,2) default 0,            -- relevant if is_premium
    is_active       boolean not null default true,
    created_by      uuid references profiles(id),
    created_at      timestamptz not null default now()
);

-- ---------- TEST <-> QUESTION MAPPING ----------
-- Mock tests only. There used to be a parallel `test_questions` table
-- here for chapter-wise tests, but the app never built that feature —
-- every real test (see app/admin/test_routes.py) is created and
-- populated through mock_test_questions/mock_questions. Removed as
-- dead leftover from that earlier, unused architecture.
create table if not exists mock_test_questions (
    test_id           uuid not null references tests(id) on delete cascade,
    mock_question_id  uuid not null references mock_questions(id) on delete cascade,
    question_order    int not null default 0,
    primary key (test_id, mock_question_id)
);

-- ---------- ATTEMPTS (registered users only -- guest mode removed) ----------
create table if not exists test_attempts (
    id              uuid primary key default uuid_generate_v4(),
    test_id         uuid not null references tests(id) on delete cascade,
    user_id         uuid not null references profiles(id) on delete cascade,
    started_at      timestamptz not null default now(),
    submitted_at    timestamptz,
    score           numeric(6,2),
    total_questions int,
    correct_count   int,
    wrong_count     int,
    skipped_count   int
);

-- For a live DB where test_attempts already existed with a nullable
-- user_id (from the old guest-mode days): after running the
-- "Wipe Legacy Guests" admin cleanup (or otherwise deleting/backfilling
-- any user_id IS NULL rows), enforce NOT NULL for real. This is safe
-- to run repeatedly and a no-op once already set.
do $$
begin
    if not exists (
        select 1 from test_attempts where user_id is null
    ) then
        alter table test_attempts alter column user_id set not null;
    end if;
end $$;

-- Unified answers table for both Chapter Questions and Mock Questions.
-- Contains durable state (`status`, `time_taken_sec`) to eliminate cookie session size crashes.
create table if not exists attempt_answers (
    id                uuid primary key default uuid_generate_v4(),
    attempt_id        uuid not null references test_attempts(id) on delete cascade,
    question_id       uuid references questions(id) on delete cascade,
    mock_question_id  uuid references mock_questions(id) on delete cascade,
    selected_option   char(1) check (selected_option in ('A','B','C','D')),
    is_correct        boolean,
    time_taken_sec    int default 0,
    status            text check (status in ('answered', 'marked', 'answered_marked', 'not_answered', 'not_visited')),
    
    -- Exactly one of question_id / mock_question_id must be set
    constraint attempt_answers_source_check check (
        (question_id is not null and mock_question_id is null) or
        (question_id is null and mock_question_id is not null)
    )
);

-- Unique CONSTRAINTS (not partial indexes) to support Supabase UPSERT
-- (on_conflict) logic. ON CONFLICT can only target a real unique
-- constraint or a non-partial unique index — a partial index (e.g.
-- "... where mock_question_id is not null") looks like it enforces
-- the same uniqueness but is invisible to ON CONFLICT's constraint
-- matching, and Postgres raises 42P10 ("no unique or exclusion
-- constraint matching the ON CONFLICT specification") on every upsert.
-- NULLs are naturally distinct under a unique constraint, so no WHERE
-- clause is needed even though question_id/mock_question_id are
-- nullable.
alter table attempt_answers
    add constraint uq_attempt_answers_chapter_conflict unique (attempt_id, question_id);

alter table attempt_answers
    add constraint uq_attempt_answers_mock_conflict unique (attempt_id, mock_question_id);

-- ---------- TRANSACTIONS (manual UTR-based payment verification) ----------
create table if not exists transactions (
    id              uuid primary key default uuid_generate_v4(),
    user_id         uuid not null references profiles(id) on delete cascade,
    test_id         uuid references tests(id),          -- what they're paying for
    amount_inr      numeric(8,2) not null,
    utr_number      text not null,
    payment_screenshot_url text,
    status          text not null default 'pending' check (status in ('pending','approved','rejected')),
    reviewed_by     uuid references profiles(id),
    reviewed_at     timestamptz,
    admin_note      text,
    created_at      timestamptz not null default now()
);

-- Grants access once a transaction is approved (so "has this user
-- paid for this test" is a simple lookup, not a status re-check)
create table if not exists test_access_grants (
    user_id         uuid not null references profiles(id) on delete cascade,
    test_id         uuid not null references tests(id) on delete cascade,
    granted_via     uuid references transactions(id),
    granted_at      timestamptz not null default now(),
    primary key (user_id, test_id)
);

-- =========================================================
-- INDEXES (the ones that actually get hit on every page load)
-- =========================================================
create index if not exists idx_subjects_stream on subjects(stream_id);
create index if not exists idx_chapters_subject on chapters(subject_id);
create index if not exists idx_questions_chapter on questions(chapter_id);
create index if not exists idx_questions_difficulty on questions(difficulty_id);
create index if not exists idx_tests_category on tests(category_id);
create index if not exists idx_tests_stream on tests(stream_id);
create index if not exists idx_attempts_test on test_attempts(test_id);
create index if not exists idx_attempts_user on test_attempts(user_id);
create index if not exists idx_transactions_status on transactions(status);
create index if not exists idx_transactions_user on transactions(user_id);
create index if not exists idx_mock_questions_stream on mock_questions(stream_id);
create index if not exists idx_mock_questions_subject on mock_questions(subject_id);
create index if not exists idx_mock_test_questions_test on mock_test_questions(test_id);
create index if not exists idx_attempt_answers_mock_question on attempt_answers(mock_question_id);

-- =========================================================
-- ROW LEVEL SECURITY (RLS) — Supabase requires this to be explicit
-- =========================================================
alter table profiles enable row level security;
alter table transactions enable row level security;
alter table test_attempts enable row level security;
alter table attempt_answers enable row level security;
alter table test_access_grants enable row level security;
