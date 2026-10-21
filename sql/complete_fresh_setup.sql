create extension if not exists "uuid-ossp";
create extension if not exists pgcrypto;
drop table if exists public.guest_streams cascade;
drop table if exists public.guests cascade;
create table if not exists public.roles (
    id          smallint primary key,
    name        text unique not null
);
insert into public.roles (id, name) values
    (1, 'student'),
    (2, 'admin'),
    (3, 'moderator')
on conflict (id) do nothing;
create table if not exists public.profiles (
    id          uuid primary key references auth.users(id) on delete cascade,
    username    text,
    full_name   text,
    phone       text,
    role_id     smallint not null default 1 references public.roles(id),
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
alter table public.profiles add column if not exists username text;
create unique index if not exists profiles_username_unique_idx on public.profiles (username);
create table if not exists public.streams (
    id              uuid primary key default uuid_generate_v4(),
    name            text unique not null,
    slug            text unique not null,
    description     text,
    icon_url        text,
    display_order   int not null default 0,
    is_active       boolean not null default true,
    created_by      uuid references public.profiles(id) on delete set null,
    created_at      timestamptz not null default now()
);
insert into public.streams (name, slug, description, display_order, is_active) values
    ('NEET', 'neet', 'National Eligibility cum Entrance Test (UG) - Physics, Chemistry & Biology', 1, true),
    ('JEE',  'jee',  'Joint Entrance Examination (Main & Advanced) - Physics, Chemistry & Mathematics', 2, true)
on conflict (slug) do nothing;
create table if not exists public.user_streams (
    user_id     uuid not null references public.profiles(id) on delete cascade,
    stream_id   uuid not null references public.streams(id) on delete cascade,
    joined_at   timestamptz not null default now(),
    primary key (user_id, stream_id)
);
create table if not exists public.subjects (
    id              uuid primary key default uuid_generate_v4(),
    stream_id       uuid not null references public.streams(id) on delete cascade,
    name            text not null,
    slug            text not null,
    display_order   int not null default 0,
    is_active       boolean not null default true,
    unique (stream_id, slug)
);
insert into public.subjects (stream_id, name, slug, display_order, is_active)
select s.id, v.name, v.slug, v.ord, true
  from public.streams s
  cross join (values
      ('Physics',   'physics',   1),
      ('Chemistry', 'chemistry', 2),
      ('Biology',   'biology',   3)
  ) as v(name, slug, ord)
 where s.slug = 'neet'
on conflict (stream_id, slug) do nothing;
insert into public.subjects (stream_id, name, slug, display_order, is_active)
select s.id, v.name, v.slug, v.ord, true
  from public.streams s
  cross join (values
      ('Physics',     'physics',     1),
      ('Chemistry',   'chemistry',   2),
      ('Mathematics', 'mathematics', 3)
  ) as v(name, slug, ord)
 where s.slug = 'jee'
on conflict (stream_id, slug) do nothing;
create table if not exists public.chapters (
    id              uuid primary key default uuid_generate_v4(),
    subject_id      uuid not null references public.subjects(id) on delete cascade,
    name            text not null,
    slug            text not null,
    display_order   int not null default 0,
    is_active       boolean not null default true,
    unique (subject_id, slug)
);
create table if not exists public.difficulty_levels (
    id              smallint primary key,
    name            text unique not null,
    display_order   int not null default 0
);
insert into public.difficulty_levels (id, name, display_order) values
    (1, 'Easy',   1),
    (2, 'Medium', 2),
    (3, 'Hard',   3)
on conflict (id) do nothing;
create table if not exists public.questions (
    id              uuid primary key default uuid_generate_v4(),
    chapter_id      uuid not null references public.chapters(id) on delete cascade,
    difficulty_id   smallint not null references public.difficulty_levels(id),
    category        text default 'topic',
    folder_name     text default 'General / Uncategorized',
    set_name        text default 'Practice Set 1',
    topic_name      text,
    question_text   text not null,
    option_a        text not null,
    option_b        text not null,
    option_c        text not null,
    option_d        text not null,
    correct_option  char(1) not null check (correct_option in ('A','B','C','D')),
    explanation     text,
    marks           numeric(5,2) default 4,
    negative_marks  numeric(5,2) default 1,
    is_pyq          boolean not null default false,
    pyq_year        int,
    has_image       boolean not null default false,
    image_url       text,
    is_premium      boolean not null default false,
    created_by      uuid references public.profiles(id) on delete set null,
    created_at      timestamptz not null default now()
);
alter table public.questions add column if not exists category       text default 'topic';
alter table public.questions add column if not exists folder_name    text default 'General / Uncategorized';
alter table public.questions add column if not exists set_name       text default 'Practice Set 1';
alter table public.questions add column if not exists topic_name     text;
alter table public.questions add column if not exists marks          numeric(5,2) default 4;
alter table public.questions add column if not exists negative_marks numeric(5,2) default 1;
alter table public.questions add column if not exists has_image      boolean not null default false;
alter table public.questions add column if not exists is_premium     boolean not null default false;
create table if not exists public.mock_test_series (
    id          uuid primary key default uuid_generate_v4(),
    name        text not null,
    stream_id   uuid references public.streams(id) on delete cascade,
    created_at  timestamptz not null default now()
);
alter table public.mock_test_series add column if not exists stream_id uuid references public.streams(id) on delete cascade;
create table if not exists public.test_categories (
    id              uuid primary key default uuid_generate_v4(),
    stream_id       uuid not null references public.streams(id) on delete cascade,
    name            text not null,
    slug            text not null,
    is_premium      boolean not null default false,
    display_order   int not null default 0,
    unique (stream_id, slug)
);
create table if not exists public.tests (
    id                  uuid primary key default uuid_generate_v4(),
    series_id           uuid references public.mock_test_series(id) on delete cascade,
    category_id         uuid not null references public.test_categories(id) on delete cascade,
    stream_id           uuid not null references public.streams(id) on delete cascade,
    subject_id          uuid references public.subjects(id) on delete set null,
    chapter_id          uuid references public.chapters(id) on delete set null,
    title               text not null,
    description         text,
    duration_minutes    int not null default 180,
    total_marks         int default 720,
    negative_marking    numeric(4,2) default 1,
    is_premium          boolean not null default false,
    price_inr           numeric(8,2) default 0,
    is_active           boolean not null default true,
    created_by          uuid references public.profiles(id) on delete set null,
    created_at          timestamptz not null default now()
);
alter table public.tests add column if not exists series_id uuid references public.mock_test_series(id) on delete cascade;
create table if not exists public.mock_questions (
    id              uuid primary key default uuid_generate_v4(),
    stream_id       uuid not null references public.streams(id) on delete cascade,
    subject_id      uuid not null references public.subjects(id) on delete cascade,
    topic_name      text not null,
    difficulty_id   smallint references public.difficulty_levels(id),
    question_text   text not null,
    option_a        text not null,
    option_b        text not null,
    option_c        text not null,
    option_d        text not null,
    correct_option  char(1) not null check (correct_option in ('A','B','C','D')),
    explanation     text,
    is_pyq          boolean not null default false,
    pyq_year        int,
    has_image       boolean not null default false,
    image_url       text,
    is_premium      boolean not null default false,
    created_by      uuid references public.profiles(id) on delete set null,
    created_at      timestamptz not null default now()
);
alter table public.mock_questions add column if not exists has_image boolean not null default false;
create table if not exists public.mock_test_questions (
    test_id           uuid not null references public.tests(id) on delete cascade,
    mock_question_id  uuid not null references public.mock_questions(id) on delete cascade,
    question_order    int not null default 0,
    primary key (test_id, mock_question_id)
);
create table if not exists public.test_attempts (
    id                  uuid primary key default uuid_generate_v4(),
    test_id             uuid not null references public.tests(id) on delete cascade,
    user_id             uuid not null references public.profiles(id) on delete cascade,
    attempt_kind        text not null default 'full'
                        check (attempt_kind in ('full', 'wrong_only', 'marked_and_wrong_only')),
    parent_attempt_id   uuid references public.test_attempts(id) on delete cascade,
    started_at          timestamptz not null default now(),
    submitted_at        timestamptz,
    score               numeric(6,2),
    total_questions     int,
    correct_count       int,
    wrong_count         int,
    skipped_count       int
);
alter table public.test_attempts add column if not exists attempt_kind text not null default 'full';
alter table public.test_attempts add column if not exists parent_attempt_id uuid references public.test_attempts(id) on delete cascade;
do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'test_attempts_attempt_kind_check') then
        alter table public.test_attempts add constraint test_attempts_attempt_kind_check
            check (attempt_kind in ('full', 'wrong_only', 'marked_and_wrong_only'));
    end if;
end $$;
create table if not exists public.attempt_answers (
    id                uuid primary key default uuid_generate_v4(),
    attempt_id        uuid not null references public.test_attempts(id) on delete cascade,
    question_id       uuid references public.questions(id) on delete cascade,
    mock_question_id  uuid references public.mock_questions(id) on delete cascade,
    selected_option   char(1) check (selected_option in ('A','B','C','D')),
    is_correct        boolean,
    time_taken_sec    int default 0,
    status            text check (status in ('answered', 'marked', 'answered_marked', 'not_answered', 'not_visited')),
    mistake_note      text,
    was_replayed      boolean not null default true,
    constraint attempt_answers_source_check check (
        (question_id is not null and mock_question_id is null) or
        (question_id is null and mock_question_id is not null)
    ),
    constraint uq_attempt_answers_chapter_conflict unique (attempt_id, question_id),
    constraint uq_attempt_answers_mock_conflict unique (attempt_id, mock_question_id)
);
alter table public.attempt_answers add column if not exists mistake_note text;
alter table public.attempt_answers add column if not exists was_replayed boolean not null default true;
create table if not exists public.practice_attempts (
    id                  uuid primary key default uuid_generate_v4(),
    user_id             uuid not null references public.profiles(id) on delete cascade,
    chapter_id          uuid not null references public.chapters(id) on delete cascade,
    mode                text not null check (mode in ('mcq', 'pyq')),
    category            text not null,
    folder_name         text not null,
    set_name            text not null,
    attempt_kind        text not null default 'full'
                        check (attempt_kind in ('full', 'wrong_only', 'marked_and_wrong_only')),
    parent_attempt_id   uuid references public.practice_attempts(id) on delete cascade,
    started_at          timestamptz not null default now(),
    submitted_at        timestamptz,
    score               numeric(6,2),
    total_questions     int,
    correct_count       int,
    wrong_count         int,
    skipped_count       int,
    total_time_sec      int default 0
);
alter table public.practice_attempts add column if not exists parent_attempt_id uuid references public.practice_attempts(id) on delete cascade;
alter table public.practice_attempts drop constraint if exists uq_practice_attempt_quiz;
create table if not exists public.practice_attempt_answers (
    id              uuid primary key default uuid_generate_v4(),
    attempt_id      uuid not null references public.practice_attempts(id) on delete cascade,
    question_id     uuid not null references public.questions(id) on delete cascade,
    selected_option char(1) check (selected_option in ('A','B','C','D')),
    is_correct      boolean,
    time_taken_sec  int not null default 0,
    status          text check (status in ('answered','marked','answered_marked','not_answered','not_visited')),
    mistake_note    text,
    was_replayed    boolean not null default true,
    constraint uq_practice_answer unique (attempt_id, question_id)
);
create table if not exists public.transactions (
    id                      uuid primary key default uuid_generate_v4(),
    user_id                 uuid not null references public.profiles(id) on delete cascade,
    test_id                 uuid references public.tests(id) on delete set null,
    amount_inr              numeric(8,2) not null,
    utr_number              text not null,
    payment_screenshot_url  text,
    status                  text not null default 'pending' check (status in ('pending','approved','rejected')),
    reviewed_by             uuid references public.profiles(id) on delete set null,
    reviewed_at             timestamptz,
    admin_note              text,
    created_at              timestamptz not null default now()
);
create table if not exists public.test_access_grants (
    user_id     uuid not null references public.profiles(id) on delete cascade,
    test_id     uuid not null references public.tests(id) on delete cascade,
    granted_via uuid references public.transactions(id) on delete set null,
    granted_at  timestamptz not null default now(),
    primary key (user_id, test_id)
);
create table if not exists public.plans (
    id              uuid primary key default gen_random_uuid(),
    code            text not null unique,
    name            text not null,
    description     text,
    price_inr       numeric(10,2) not null check (price_inr > 0),
    valid_until     timestamptz,
    valid_days      int check (valid_days > 0),
    stream_id       uuid references public.streams(id) on delete set null,
    features        jsonb not null default '[]'::jsonb,
    is_active       boolean not null default true,
    display_order   int not null default 0,
    created_at      timestamptz not null default now(),
    constraint plans_validity_check check (valid_until is not null or valid_days is not null)
);
insert into public.plans (code, name, description, price_inr, valid_until, features, display_order)
values (
    'season_pass_2027',
    'Season Pass',
    'Every premium mock test and practice set, until your NEET / JEE 2027 exam season ends.',
    299,
    timestamptz '2027-06-30 23:59:59+05:30',
    '["All premium mock tests", "All premium chapter-wise practice sets", "Detailed explanations and analysis", "One payment, no renewals"]'::jsonb,
    1
)
on conflict (code) do nothing;
create table if not exists public.orders (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid references public.profiles(id) on delete set null,
    plan_id         uuid not null references public.plans(id),
    base_amount     numeric(10,2) not null,
    payable_amount  numeric(10,2) not null,
    status          text not null default 'created'
                    check (status in ('created','submitted','approved','rejected','expired','cancelled')),
    utr             text,
    tg_file_id      text,
    tg_message_id   bigint,
    reject_reason   text,
    reviewed_by     text,
    reviewed_at     timestamptz,
    submitted_at    timestamptz,
    expires_at      timestamptz not null,
    created_at      timestamptz not null default now(),
    constraint uq_orders_utr unique (utr),
    constraint orders_utr_format check (utr is null or utr ~ '^[0-9]{12}$'),
    constraint orders_amount_check check (payable_amount >= base_amount)
);
create unique index if not exists uq_pending_amount
    on public.orders (payable_amount)
    where status in ('created', 'submitted');
create table if not exists public.subscriptions (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid not null references public.profiles(id) on delete cascade,
    plan_id         uuid not null references public.plans(id),
    stream_id       uuid references public.streams(id) on delete set null,
    order_id        uuid unique references public.orders(id) on delete set null,
    source          text not null default 'payment'
                    check (source in ('payment', 'coupon', 'admin', 'referral')),
    status          text not null default 'active' check (status in ('active', 'revoked')),
    starts_at       timestamptz not null default now(),
    expires_at      timestamptz not null,
    created_at      timestamptz not null default now(),
    constraint subscriptions_dates_check check (expires_at > starts_at)
);
create index if not exists idx_subjects_stream on public.subjects(stream_id);
create index if not exists idx_chapters_subject on public.chapters(subject_id);
create index if not exists idx_questions_chapter on public.questions(chapter_id);
create index if not exists idx_questions_difficulty on public.questions(difficulty_id);
create index if not exists idx_questions_quiz on public.questions (chapter_id, is_pyq, category, folder_name, set_name);
create index if not exists idx_questions_needs_image on public.questions(chapter_id) where has_image = true and (image_url is null or image_url = '');
create index if not exists idx_tests_series on public.tests(series_id);
create index if not exists idx_tests_category on public.tests(category_id);
create index if not exists idx_tests_stream on public.tests(stream_id);
create index if not exists idx_attempts_test on public.test_attempts(test_id);
create index if not exists idx_attempts_user on public.test_attempts(user_id);
create index if not exists idx_attempts_parent on public.test_attempts(parent_attempt_id);
create index if not exists idx_practice_attempts_user on public.practice_attempts(user_id);
create index if not exists idx_practice_attempts_parent on public.practice_attempts(parent_attempt_id);
create index if not exists idx_practice_answers_attempt on public.practice_attempt_answers(attempt_id);
create index if not exists idx_practice_answers_question on public.practice_attempt_answers(question_id);
create index if not exists idx_mock_questions_stream on public.mock_questions(stream_id);
create index if not exists idx_mock_questions_subject on public.mock_questions(subject_id);
create index if not exists idx_mock_questions_needs_image on public.mock_questions(stream_id) where has_image = true and (image_url is null or image_url = '');
create index if not exists idx_mock_test_questions_test on public.mock_test_questions(test_id);
create index if not exists idx_attempt_answers_mock_question on public.attempt_answers(mock_question_id);
create index if not exists idx_transactions_status on public.transactions(status);
create index if not exists idx_transactions_user on public.transactions(user_id);
create index if not exists idx_orders_user on public.orders (user_id, created_at desc);
create index if not exists idx_orders_status on public.orders (status, created_at desc);
create index if not exists idx_subscriptions_user on public.subscriptions (user_id, expires_at desc);
create or replace function public.create_order(p_user_id uuid, p_plan_code text)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_plan    public.plans%rowtype;
    v_order   public.orders%rowtype;
    v_offset  int;
begin
    select * into v_plan
      from public.plans
     where code = p_plan_code and is_active = true;
    if not found then
        return jsonb_build_object('ok', false, 'error', 'plan_not_found');
    end if;
    update public.orders
       set status = 'expired'
     where status = 'created' and expires_at < now();
    if exists (
        select 1 from public.subscriptions s
         where s.user_id = p_user_id
           and s.plan_id = v_plan.id
           and s.status = 'active'
           and s.starts_at <= now()
           and s.expires_at > now()
    ) then
        return jsonb_build_object('ok', false, 'error', 'already_active');
    end if;
    select * into v_order
      from public.orders
     where user_id = p_user_id
       and plan_id = v_plan.id
       and status in ('created', 'submitted')
     order by created_at desc
     limit 1;
    if found then
        return jsonb_build_object('ok', true, 'reused', true, 'order_id', v_order.id);
    end if;
    for i in 1..5 loop
        select o into v_offset
          from generate_series(1, 99) as o
         where not exists (
                 select 1 from public.orders x
                  where x.status in ('created', 'submitted')
                    and x.payable_amount = v_plan.price_inr + (o / 100.0)
               )
         order by random()
         limit 1;
        if v_offset is null then
            return jsonb_build_object('ok', false, 'error', 'busy');
        end if;
        begin
            insert into public.orders (user_id, plan_id, base_amount, payable_amount, status, expires_at)
            values (p_user_id, v_plan.id, v_plan.price_inr, v_plan.price_inr + (v_offset / 100.0),
                    'created', now() + interval '45 minutes')
            returning * into v_order;
            return jsonb_build_object('ok', true, 'reused', false, 'order_id', v_order.id);
        exception when unique_violation then
            null;
        end;
    end loop;
    return jsonb_build_object('ok', false, 'error', 'busy');
end;
$$;
create or replace function public.submit_order(p_order_id uuid, p_user_id uuid, p_utr text)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_order public.orders%rowtype;
begin
    select * into v_order
      from public.orders
     where id = p_order_id and user_id = p_user_id
       for update;
    if not found then
        return jsonb_build_object('ok', false, 'error', 'not_found');
    end if;
    if v_order.status = 'submitted' then
        if v_order.utr = p_utr then
            return jsonb_build_object('ok', true, 'already', true);
        end if;
        return jsonb_build_object('ok', false, 'error', 'already_submitted');
    end if;
    if v_order.status = 'approved' then
        return jsonb_build_object('ok', false, 'error', 'already_approved');
    end if;
    if v_order.status in ('rejected', 'cancelled') then
        return jsonb_build_object('ok', false, 'error', 'closed');
    end if;
    begin
        update public.orders
           set status = 'submitted',
               utr = p_utr,
               submitted_at = now()
         where id = v_order.id;
    exception when unique_violation then
        if exists (select 1 from public.orders where utr = p_utr and id <> v_order.id) then
            return jsonb_build_object('ok', false, 'error', 'utr_used');
        end if;
        return jsonb_build_object('ok', false, 'error', 'amount_reassigned');
    end;
    return jsonb_build_object('ok', true, 'already', false);
end;
$$;
create or replace function public.approve_order(p_order_id uuid, p_reviewer text)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_order    public.orders%rowtype;
    v_plan     public.plans%rowtype;
    v_expires  timestamptz;
    v_sub_id   uuid;
begin
    select * into v_order from public.orders where id = p_order_id for update;
    if not found then
        return jsonb_build_object('ok', false, 'error', 'not_found');
    end if;
    if v_order.status = 'approved' then
        return jsonb_build_object('ok', true, 'already', true, 'user_id', v_order.user_id);
    end if;
    if v_order.status not in ('submitted', 'rejected') then
        return jsonb_build_object('ok', false, 'error', 'bad_status', 'status', v_order.status);
    end if;
    if v_order.user_id is null then
        return jsonb_build_object('ok', false, 'error', 'user_deleted');
    end if;
    select * into v_plan from public.plans where id = v_order.plan_id;
    v_expires := coalesce(v_plan.valid_until, now() + make_interval(days => coalesce(v_plan.valid_days, 0)));
    if v_expires <= now() then
        return jsonb_build_object('ok', false, 'error', 'plan_expired');
    end if;
    update public.orders
       set status = 'approved',
           reviewed_by = p_reviewer,
           reviewed_at = now(),
           reject_reason = null
     where id = v_order.id;
    insert into public.subscriptions (user_id, plan_id, stream_id, order_id, source, status, starts_at, expires_at)
    values (v_order.user_id, v_plan.id, v_plan.stream_id, v_order.id, 'payment', 'active', now(), v_expires)
    on conflict (order_id) do nothing
    returning id into v_sub_id;
    return jsonb_build_object(
        'ok', true,
        'already', false,
        'user_id', v_order.user_id,
        'expires_at', v_expires,
        'subscription_id', v_sub_id
    );
end;
$$;
create or replace function public.reject_order(p_order_id uuid, p_reviewer text, p_reason text)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_order public.orders%rowtype;
begin
    select * into v_order from public.orders where id = p_order_id for update;
    if not found then
        return jsonb_build_object('ok', false, 'error', 'not_found');
    end if;
    if v_order.status = 'rejected' then
        return jsonb_build_object('ok', true, 'already', true, 'user_id', v_order.user_id);
    end if;
    if v_order.status <> 'submitted' then
        return jsonb_build_object('ok', false, 'error', 'bad_status', 'status', v_order.status);
    end if;
    update public.orders
       set status = 'rejected',
           reject_reason = left(coalesce(nullif(trim(p_reason), ''), 'Payment could not be verified.'), 200),
           reviewed_by = p_reviewer,
           reviewed_at = now()
     where id = v_order.id;
    return jsonb_build_object('ok', true, 'already', false, 'user_id', v_order.user_id);
end;
$$;
create or replace function public.revoke_order(p_order_id uuid, p_reviewer text, p_reason text)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_order public.orders%rowtype;
begin
    select * into v_order from public.orders where id = p_order_id for update;
    if not found then
        return jsonb_build_object('ok', false, 'error', 'not_found');
    end if;
    if v_order.status <> 'approved' then
        return jsonb_build_object('ok', false, 'error', 'bad_status', 'status', v_order.status);
    end if;
    update public.subscriptions set status = 'revoked' where order_id = v_order.id;
    update public.orders
       set status = 'cancelled',
           reject_reason = left(coalesce(nullif(trim(p_reason), ''), 'Access revoked by admin.'), 200),
           reviewed_by = p_reviewer,
           reviewed_at = now()
     where id = v_order.id;
    return jsonb_build_object('ok', true, 'user_id', v_order.user_id);
end;
$$;
create or replace function public.payments_summary()
returns jsonb
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select jsonb_build_object(
        'revenue_today',
            coalesce(sum(payable_amount) filter (
                where status = 'approved'
                  and reviewed_at >= (date_trunc('day', timezone('Asia/Kolkata', now())) at time zone 'Asia/Kolkata')
            ), 0),
        'revenue_month',
            coalesce(sum(payable_amount) filter (
                where status = 'approved'
                  and reviewed_at >= (date_trunc('month', timezone('Asia/Kolkata', now())) at time zone 'Asia/Kolkata')
            ), 0),
        'revenue_total',   coalesce(sum(payable_amount) filter (where status = 'approved'), 0),
        'approved_count',  count(1) filter (where status = 'approved'),
        'pending_count',   count(1) filter (where status = 'submitted'),
        'rejected_count',  count(1) filter (where status = 'rejected')
    )
    from public.orders;
$$;
do $$
begin
    if exists (
        select 1 from information_schema.tables
        where table_schema = 'storage' and table_name = 'buckets'
    ) then
        insert into storage.buckets (id, name, public)
        values ('question-images', 'question-images', true)
        on conflict (id) do update set public = true;
    end if;
exception when others then
    raise notice 'Storage bucket setup skipped (can be created manually in Dashboard -> Storage -> question-images): %', sqlerrm;
end $$;
do $$
declare
    r record;
begin
    for r in select tablename from pg_tables where schemaname = 'public'
    loop
        execute format('alter table public.%I enable row level security', r.tablename);
    end loop;
    for r in select tablename, policyname from pg_policies where schemaname = 'public'
    loop
        execute format('drop policy if exists %I on public.%I', r.policyname, r.tablename);
    end loop;
    for r in select tablename from pg_tables where schemaname = 'public'
    loop
        execute format(
            'create policy deny_all_browser_roles on public.%I as restrictive for all to anon, authenticated using (false) with check (false)',
            r.tablename
        );
    end loop;
end $$;
revoke all on all tables    in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke execute on all functions in schema public from public, anon, authenticated;
grant all on all tables    in schema public to service_role;
grant all on all sequences in schema public to service_role;
grant execute on all functions in schema public to service_role;
alter default privileges in schema public revoke all on tables    from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;
update public.questions set category = 'topic' where category is null or btrim(category) = '';
update public.questions set set_name = btrim(coalesce(nullif(btrim(set_name), ''), nullif(btrim(topic_name), ''), 'Practice Set 1')) where set_name is null or btrim(set_name) = '' or set_name <> btrim(set_name);
update public.questions set folder_name = btrim(coalesce(nullif(btrim(folder_name), ''), 'General / Uncategorized')) where folder_name is null or btrim(folder_name) = '' or folder_name <> btrim(folder_name);
notify pgrst, 'reload schema';
select code, name, price_inr, valid_until, is_active
  from public.plans
 order by display_order;
