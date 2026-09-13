-- =========================================================
-- SANGAM STUDY — Core Database Schema (Supabase / Postgres)
-- =========================================================
-- Design notes:
-- 1. Everything content-related hangs off `streams`, and streams
--    are just rows — Admin can add "JEE", "NEET", "Class 11" etc.
--    without touching code.
-- 2. Users can belong to MULTIPLE streams (many-to-many), so a
--    student prepping for Class 12 + JEE isn't forced to pick one.
-- 3. Guests (no login) still need a stable identity across a
--    session so we can remember their stream choice + free-test
--    attempts. We use a `guest_id` (UUID stored in a cookie) for
--    this instead of forcing login.
-- 4. Difficulty is a lookup table, not a hardcoded enum, so Admin
--    can rename/add levels later without a migration.
-- =========================================================

-- ---------- EXTENSIONS ----------
create extension if not exists "uuid-ossp";
create extension if not exists pgcrypto;

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
create table if not exists profiles (
    id              uuid primary key references auth.users(id) on delete cascade,
    full_name       text,
    phone           text,
    role_id         smallint not null default 1 references roles(id),
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- ---------- GUESTS ----------
-- Anonymous/no-login users. guest_id is generated client-side (or on
-- first hit) and stored in a long-lived cookie. Lets us persist
-- stream choice + free attempts without forcing registration.
create table if not exists guests (
    guest_id        uuid primary key default uuid_generate_v4(),
    created_at      timestamptz not null default now(),
    last_seen_at    timestamptz not null default now()
);

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

-- Same, for guests
create table if not exists guest_streams (
    guest_id        uuid not null references guests(guest_id) on delete cascade,
    stream_id       uuid not null references streams(id) on delete cascade,
    joined_at       timestamptz not null default now(),
    primary key (guest_id, stream_id)
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

-- ---------- QUESTIONS ----------
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
    chapter_id      uuid references chapters(id),       -- nullable: only for chapter-wise
    title           text not null,
    description     text,
    duration_minutes int not null default 60,
    total_marks     int,
    negative_marking numeric(4,2) default 0,
    is_premium      boolean not null default false,
    price_inr       numeric(8,2) default 0,             -- relevant if is_premium
    is_active       boolean not null default true,
    created_by      uuid references profiles(id),
    created_at      timestamptz not null default now()
);

-- Which questions belong to which test, in what order
create table if not exists test_questions (
    test_id         uuid not null references tests(id) on delete cascade,
    question_id     uuid not null references questions(id) on delete cascade,
    question_order  int not null default 0,
    primary key (test_id, question_id)
);

-- ---------- ATTEMPTS (both users and guests can attempt free tests) ----------
create table if not exists test_attempts (
    id              uuid primary key default uuid_generate_v4(),
    test_id         uuid not null references tests(id) on delete cascade,
    user_id         uuid references profiles(id) on delete cascade,
    guest_id        uuid references guests(guest_id) on delete cascade,
    started_at      timestamptz not null default now(),
    submitted_at    timestamptz,
    score           numeric(6,2),
    total_questions int,
    correct_count   int,
    wrong_count     int,
    skipped_count   int,
    -- Exactly one of user_id / guest_id must be set
    constraint attempt_owner_check check (
        (user_id is not null and guest_id is null) or
        (user_id is null and guest_id is not null)
    )
);

create table if not exists attempt_answers (
    attempt_id      uuid not null references test_attempts(id) on delete cascade,
    question_id     uuid not null references questions(id) on delete cascade,
    selected_option char(1) check (selected_option in ('A','B','C','D')),
    is_correct      boolean,
    time_taken_sec  int,
    primary key (attempt_id, question_id)
);

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
create index if not exists idx_attempts_guest on test_attempts(guest_id);
create index if not exists idx_transactions_status on transactions(status);
create index if not exists idx_transactions_user on transactions(user_id);

-- =========================================================
-- ROW LEVEL SECURITY (RLS) — Supabase requires this to be explicit
-- =========================================================
alter table profiles enable row level security;
alter table transactions enable row level security;
alter table test_attempts enable row level security;
alter table attempt_answers enable row level security;
alter table test_access_grants enable row level
