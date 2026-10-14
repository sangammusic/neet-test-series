-- =========================================================
-- SANGAM STUDY - PHASE 1: PAYMENTS
-- plans | orders | subscriptions + atomic payment functions
-- =========================================================
-- HOW TO RUN
--   Supabase Dashboard -> SQL Editor -> New query -> paste ALL of this -> Run.
--   Safe to run more than once (it only creates what is missing).
--
-- ORDER OF OPERATIONS (important):
--   1) Run THIS file first            (only ADDS new tables/functions)
--   2) Deploy the new code to Render  (set the new env vars first)
--   3) Run sql/phase0_rls_lockdown.sql LAST
--
-- DESIGN IN ONE PARAGRAPH
--   A student clicks "Get Season Pass"  -> create_order()  makes an order with
--   a UNIQUE payable amount (e.g. 299.37). The student pays that exact amount,
--   submits the 12-digit UTR (+ optional screenshot) -> submit_order(). You
--   tap Approve in Telegram -> approve_order() flips the order AND creates the
--   subscription in ONE database transaction, so you can never end up with an
--   "approved but no access" order. Every function below can only be called by
--   your server (service_role key) - never from a browser.
-- =========================================================


-- ---------------------------------------------------------
-- 1. PLANS  (what you sell)
-- ---------------------------------------------------------
create table if not exists public.plans (
    id              uuid primary key default gen_random_uuid(),
    code            text not null unique,                 -- 'season_pass_2027'
    name            text not null,                        -- 'Season Pass'
    description     text,
    price_inr       numeric(10,2) not null check (price_inr > 0),
    valid_until     timestamptz,                          -- fixed end date (exam season) ...
    valid_days      int check (valid_days > 0),           -- ... OR N days from approval
    stream_id       uuid references public.streams(id) on delete set null,  -- null = all streams
    features        jsonb not null default '[]'::jsonb,   -- bullet points shown on the plan card
    is_active       boolean not null default true,
    display_order   int not null default 0,
    created_at      timestamptz not null default now(),
    constraint plans_validity_check check (valid_until is not null or valid_days is not null)
);

-- Your first plan: Season Pass, Rs 299, valid till NEET/JEE 2027 season is over.
-- To change the end date later:  update public.plans set valid_until = '...' where code = 'season_pass_2027';
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


-- ---------------------------------------------------------
-- 2. ORDERS  (one row per payment attempt)
-- ---------------------------------------------------------
create table if not exists public.orders (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid references public.profiles(id) on delete set null,   -- keep the money record even if the account is deleted
    plan_id         uuid not null references public.plans(id),
    base_amount     numeric(10,2) not null,               -- plan price, e.g. 299.00
    payable_amount  numeric(10,2) not null,               -- what the student pays, e.g. 299.37 (unique while open)
    status          text not null default 'created'
                    check (status in ('created','submitted','approved','rejected','expired','cancelled')),
    utr             text,                                 -- 12-digit UPI reference typed by the student
    tg_file_id      text,                                 -- screenshot lives on Telegram, not in our database
    tg_message_id   bigint,                               -- the approval message in your Telegram chat
    reject_reason   text,
    reviewed_by     text,                                 -- 'telegram:<id>' or 'web:<admin uuid>'
    reviewed_at     timestamptz,
    submitted_at    timestamptz,
    expires_at      timestamptz not null,                 -- how long the payable amount stays reserved
    created_at      timestamptz not null default now(),
    constraint uq_orders_utr unique (utr),                -- the same UTR can never be used twice
    constraint orders_utr_format check (utr is null or utr ~ '^[0-9]{12}$'),
    constraint orders_amount_check check (payable_amount >= base_amount)
);

-- While an order is 'created' or 'submitted' its payable amount is reserved:
-- no two open orders can ever ask for the same rupees+paise.
create unique index if not exists uq_pending_amount
    on public.orders (payable_amount)
    where status in ('created', 'submitted');

create index if not exists idx_orders_user   on public.orders (user_id, created_at desc);
create index if not exists idx_orders_status on public.orders (status, created_at desc);


-- ---------------------------------------------------------
-- 3. SUBSCRIPTIONS  (who has premium access, until when)
-- ---------------------------------------------------------
create table if not exists public.subscriptions (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid not null references public.profiles(id) on delete cascade,
    plan_id         uuid not null references public.plans(id),
    stream_id       uuid references public.streams(id) on delete set null,   -- null = all streams
    order_id        uuid unique references public.orders(id) on delete set null,  -- one subscription per order
    source          text not null default 'payment'
                    check (source in ('payment', 'coupon', 'admin', 'referral')),
    status          text not null default 'active' check (status in ('active', 'revoked')),
    starts_at       timestamptz not null default now(),
    expires_at      timestamptz not null,
    created_at      timestamptz not null default now(),
    constraint subscriptions_dates_check check (expires_at > starts_at)
);

create index if not exists idx_subscriptions_user on public.subscriptions (user_id, expires_at desc);


-- ---------------------------------------------------------
-- 4. LOCK THE NEW TABLES (deny-all for browser keys)
--    The app talks to the database only through the server's service_role key.
-- ---------------------------------------------------------
do $$
declare
    t text;
begin
    foreach t in array array['plans', 'orders', 'subscriptions']
    loop
        execute format('alter table public.%I enable row level security', t);
        execute format('revoke all on table public.%I from anon, authenticated', t);
        execute format('drop policy if exists deny_all_browser_roles on public.%I', t);
        execute format(
            'create policy deny_all_browser_roles on public.%I as restrictive for all to anon, authenticated using (false) with check (false)',
            t
        );
    end loop;
end $$;


-- ---------------------------------------------------------
-- 5. FUNCTIONS  (each one is a single atomic transaction)
-- ---------------------------------------------------------

-- 5a. create_order: reserve a unique amount for this student
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

    -- free the amounts held by abandoned orders that were never submitted
    update public.orders
       set status = 'expired'
     where status = 'created' and expires_at < now();

    -- already owns this plan?
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

    -- reuse the student's open order for this plan (page refresh / double click)
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

    -- pick a free paise value (.01 to .99); retry if two students grab the same one at once
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
            null;   -- somebody took that amount a split second earlier -> loop and pick again
        end;
    end loop;

    return jsonb_build_object('ok', false, 'error', 'busy');
end;
$$;


-- 5b. submit_order: student sends the UTR
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
            return jsonb_build_object('ok', true, 'already', true);      -- double click: fine
        end if;
        return jsonb_build_object('ok', false, 'error', 'already_submitted');
    end if;
    if v_order.status = 'approved' then
        return jsonb_build_object('ok', false, 'error', 'already_approved');
    end if;
    if v_order.status in ('rejected', 'cancelled') then
        return jsonb_build_object('ok', false, 'error', 'closed');
    end if;

    -- status is 'created' or 'expired'. A late payer can still submit as long as
    -- nobody else has been given the same amount in the meantime.
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


-- 5c. approve_order: order -> approved AND subscription created, together
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


-- 5d. reject_order
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


-- 5e. revoke_order: take back an approved payment (refund / mistake)
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


-- 5f. payments_summary: numbers for the admin dashboard (India time)
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
        'approved_count',  count(*) filter (where status = 'approved'),
        'pending_count',   count(*) filter (where status = 'submitted'),
        'rejected_count',  count(*) filter (where status = 'rejected')
    )
    from public.orders;
$$;


-- ---------------------------------------------------------
-- 6. ONLY YOUR SERVER MAY CALL THESE FUNCTIONS
-- ---------------------------------------------------------
revoke all on function public.create_order(uuid, text)          from public, anon, authenticated;
revoke all on function public.submit_order(uuid, uuid, text)    from public, anon, authenticated;
revoke all on function public.approve_order(uuid, text)         from public, anon, authenticated;
revoke all on function public.reject_order(uuid, text, text)    from public, anon, authenticated;
revoke all on function public.revoke_order(uuid, text, text)    from public, anon, authenticated;
revoke all on function public.payments_summary()                from public, anon, authenticated;

grant execute on function public.create_order(uuid, text)       to service_role;
grant execute on function public.submit_order(uuid, uuid, text) to service_role;
grant execute on function public.approve_order(uuid, text)      to service_role;
grant execute on function public.reject_order(uuid, text, text) to service_role;
grant execute on function public.revoke_order(uuid, text, text) to service_role;
grant execute on function public.payments_summary()             to service_role;

-- make the API see the new functions immediately
notify pgrst, 'reload schema';


-- ---------------------------------------------------------
-- 7. CHECK: you should see ONE row - your Season Pass plan.
-- ---------------------------------------------------------
select code, name, price_inr, valid_until, is_active from public.plans order by display_order;
