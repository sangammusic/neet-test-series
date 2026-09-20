-- =========================================================
-- MIGRATION: Persistent chapter-wise PRACTICE attempts
--            + "mistake note" + reattempt tracking
--            (+ mock-test parity columns)
-- =========================================================
-- Why a NEW table instead of reusing test_attempts?
--   test_attempts.test_id is NOT NULL and references tests(id).
--   A chapter-wise practice quiz has no `tests` row -- its identity is
--   the natural key  (chapter_id, mode, category, folder_name, set_name)
--   stored on the `questions` rows themselves. So practice gets its own
--   pair of tables that mirror test_attempts / attempt_answers.
--
-- "Latest attempt only" (same as mock tests, whose create_test_attempt
-- deletes the previous attempt): enforced by a UNIQUE constraint on the
-- quiz identity, so even a double-click can never leave two "latest"
-- rows behind.
--
-- Safe to run more than once (everything is `if not exists` / guarded).
-- Run once in the Supabase SQL editor, then add these tables to
-- schema.sql if you keep it as the source of truth.
-- =========================================================

create extension if not exists "uuid-ossp";

-- ---------- PRACTICE ATTEMPTS (one row = the user's latest run of one quiz) ----------
create table if not exists practice_attempts (
    id                uuid primary key default uuid_generate_v4(),
    user_id           uuid not null references profiles(id) on delete cascade,
    chapter_id        uuid not null references chapters(id) on delete cascade,
    mode              text not null check (mode in ('mcq', 'pyq')),
    category          text not null,                 -- 'topic' | 'random'
    folder_name       text not null,
    set_name          text not null,

    -- 'full'                  -> every question in the quiz was (re)played
    -- 'wrong_only'            -> only questions that were wrong last time
    -- 'marked_and_wrong_only' -> only questions that were marked OR wrong last time
    attempt_kind      text not null default 'full'
                      check (attempt_kind in ('full', 'wrong_only', 'marked_and_wrong_only')),

    started_at        timestamptz not null default now(),
    submitted_at      timestamptz,

    -- Summary is computed SERVER-SIDE at submit time (never trusted from the browser).
    score             numeric(6,2),                  -- marks incl. negative marking
    total_questions   int,                           -- questions in THIS run
    correct_count     int,
    wrong_count       int,
    skipped_count     int,
    total_time_sec    int default 0,

    -- One "latest" attempt per user per quiz.
    constraint uq_practice_attempt_quiz
        unique (user_id, chapter_id, mode, category, folder_name, set_name)
);

create index if not exists idx_practice_attempts_user on practice_attempts(user_id);

-- ---------- PRACTICE ATTEMPT ANSWERS ----------
create table if not exists practice_attempt_answers (
    id              uuid primary key default uuid_generate_v4(),
    attempt_id      uuid not null references practice_attempts(id) on delete cascade,
    question_id     uuid not null references questions(id) on delete cascade,
    selected_option char(1) check (selected_option in ('A','B','C','D')),
    is_correct      boolean,
    time_taken_sec  int not null default 0,
    status          text check (status in ('answered','marked','answered_marked','not_answered','not_visited')),

    -- Free-text "what mistake did I make" note the student writes.
    mistake_note    text,

    -- TRUE  -> this question was actually played in the run that produced this attempt row.
    -- FALSE -> row exists only to CARRY FORWARD the previous answer/note of a question that
    --          a partial reattempt (wrong_only / marked_and_wrong_only) did not replay.
    was_replayed    boolean not null default true,

    -- Real UNIQUE constraint (not a partial index) so supabase upsert(on_conflict=...) works.
    constraint uq_practice_answer unique (attempt_id, question_id)
);

create index if not exists idx_practice_answers_attempt on practice_attempt_answers(attempt_id);
create index if not exists idx_practice_answers_question on practice_attempt_answers(question_id);

-- ---------- RLS (same posture as test_attempts: only the service role touches these) ----------
alter table practice_attempts enable row level security;
alter table practice_attempt_answers enable row level security;

-- ---------- MOCK-TEST PARITY ----------
-- Same "mistake note" + "reattempt kind" concept for mock test series.
alter table attempt_answers add column if not exists mistake_note text;
alter table attempt_answers add column if not exists was_replayed boolean not null default true;

alter table test_attempts add column if not exists attempt_kind text not null default 'full';
do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'test_attempts_attempt_kind_check') then
        alter table test_attempts add constraint test_attempts_attempt_kind_check
            check (attempt_kind in ('full', 'wrong_only', 'marked_and_wrong_only'));
    end if;
end $$;

-- =========================================================
-- NOTE ON UNDOCUMENTED COLUMNS (found while reading the code)
-- questions.category / folder_name / set_name / topic_name / marks /
-- negative_marks / has_image are used all over the app but are NOT
-- defined in schema.sql or any migration -- they were added by hand in
-- the Supabase dashboard. The idempotent block below documents them so
-- a fresh database matches production. It is a no-op where they exist.
-- =========================================================
alter table questions add column if not exists category       text;
alter table questions add column if not exists folder_name    text;
alter table questions add column if not exists set_name       text;
alter table questions add column if not exists topic_name     text;
alter table questions add column if not exists marks          numeric(5,2) default 4;
alter table questions add column if not exists negative_marks numeric(5,2) default 1;
alter table questions add column if not exists has_image      boolean not null default false;
alter table questions add column if not exists is_premium     boolean not null default false;

create index if not exists idx_questions_quiz
    on questions (chapter_id, is_pyq, category, folder_name, set_name);
