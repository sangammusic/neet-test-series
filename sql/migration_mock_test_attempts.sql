-- =========================================================
-- MIGRATION: Mock Test pool tables (doc parity) + attempt
-- support for mock_questions
-- =========================================================
-- Context:
--   `mock_questions` and `mock_test_questions` were created
--   directly in Supabase in an earlier session and were never
--   added to schema.sql. This migration documents them here
--   (using `create table if not exists`, so it's a safe no-op
--   if they already exist) AND extends the existing
--   `attempt_answers` table so it can record answers against
--   `mock_questions` as well as the original chapter-wise
--   `questions` table.
--
--   Nothing here drops or rewrites existing data. Run this
--   once against Supabase, then this file (plus schema.sql)
--   fully describes the live DB.
-- =========================================================

-- ---------- MOCK QUESTIONS (flat, mixed-subject pool) ----------
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

create index if not exists idx_mock_questions_stream on mock_questions(stream_id);
create index if not exists idx_mock_questions_subject on mock_questions(subject_id);

-- ---------- MOCK TEST <-> QUESTION MAPPING ----------
create table if not exists mock_test_questions (
    test_id           uuid not null references tests(id) on delete cascade,
    mock_question_id  uuid not null references mock_questions(id) on delete cascade,
    question_order    int not null default 0,
    primary key (test_id, mock_question_id)
);

create index if not exists idx_mock_test_questions_test on mock_test_questions(test_id);

-- ---------- EXTEND attempt_answers TO SUPPORT MOCK QUESTIONS ----------
-- The original attempt_answers row only pointed at `questions`
-- (chapter-wise pool). We add a nullable mock_question_id column
-- and relax question_id to nullable, with a check constraint that
-- enforces "exactly one of the two is set" per row — mirroring the
-- existing user_id/guest_id pattern on test_attempts.

do $$
begin
    if not exists (
        select 1 from information_schema.columns
        where table_name = 'attempt_answers' and column_name = 'mock_question_id'
    ) then
        alter table attempt_answers
            add column mock_question_id uuid references mock_questions(id) on delete cascade;
    end if;
end $$;

-- question_id was NOT NULL originally; relax it so a mock-question
-- row can be inserted without a chapter-pool question_id.
alter table attempt_answers alter column question_id drop not null;

-- Primary key was (attempt_id, question_id) — that no longer works
-- once question_id can be null for mock rows. Replace it with a
-- surrogate id + a partial-uniqueness guard instead.
do $$
begin
    if exists (
        select 1 from pg_constraint where conname = 'attempt_answers_pkey'
    ) then
        alter table attempt_answers drop constraint attempt_answers_pkey;
    end if;
end $$;

do $$
begin
    if not exists (
        select 1 from information_schema.columns
        where table_name = 'attempt_answers' and column_name = 'id'
    ) then
        alter table attempt_answers
            add column id uuid primary key default uuid_generate_v4();
    end if;
end $$;

-- Enforce exactly one of question_id / mock_question_id is set.
alter table attempt_answers drop constraint if exists attempt_answers_source_check;
alter table attempt_answers add constraint attempt_answers_source_check check (
    (question_id is not null and mock_question_id is null) or
    (question_id is null and mock_question_id is not null)
);

-- Prevent duplicate answer rows for the same attempt+question.
create unique index if not exists uq_attempt_answers_chapter
    on attempt_answers(attempt_id, question_id) where question_id is not null;
create unique index if not exists uq_attempt_answers_mock
    on attempt_answers(attempt_id, mock_question_id) where mock_question_id is not null;

create index if not exists idx_attempt_answers_mock_question on attempt_answers(mock_question_id);
