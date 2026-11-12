-- LOCAL PREPARATION ONLY. 운영/실제 PostgreSQL 미실행.
-- README의 내보내기·백업 확인과 쓰기 중단 이후 postgres 역할로 파일 전체만 실행.
-- 오류/timeout: 해당 파일 전체 롤백. 다음 파일/COMMIT만 실행 금지. 99_rollback_only.sql 참조.
begin isolation level read committed;
set local lock_timeout = '5s';
set local statement_timeout = '60s';
set local idle_in_transaction_session_timeout = '60s';
set local check_function_bodies = on;
set local search_path = public, pg_catalog;
do $role$ begin
    if current_user <> 'postgres' then raise exception 'STOP: postgres role required'; end if;
end; $role$;
lock table public.diaries, public.diary_messages, public.diary_assets, public.member_reward_legacy_diaries, public.member_reward_wallets, public.member_daily_reward_grants, public.shop_items, public.member_owned_items, public.member_room_placements, public.member_room_settings, public.member_tree_states, public.member_tree_watering_events, public.comic_previews, public.diary_comics, public.comic_attach_requests, public.diary_edit_requests, public.diary_delete_requests, public.shop_purchase_receipts, public.shop_refund_receipts in share mode;
do $preflight$ begin
    if not coalesce((not exists(select 1 from pg_constraint where conrelid='public.diaries'::regclass
    and conname='diaries_user_date_uq')
and exists(select 1 from pg_index i where i.indexrelid=to_regclass('public.diaries_user_date_uq')
    and i.indrelid='public.diaries'::regclass and i.indisunique and i.indisvalid and i.indisready
    and i.indislive and i.indimmediate and i.indexprs is null
    and pg_get_expr(i.indpred,i.indrelid)='(deleted_at IS NULL)'
    and pg_get_indexdef(i.indexrelid)=
        'CREATE UNIQUE INDEX diaries_user_date_uq ON public.diaries USING btree (user_id, diary_date) WHERE (deleted_at IS NULL)')), false) then raise exception 'STOP: apply reviewed v04 first'; end if;
    if not coalesce((exists(select 1 from storage.buckets where id='diary-private' and public is false)), false) then raise exception 'STOP: expected private Storage bucket missing'; end if;
    if not coalesce((to_regclass('public.comic_generation_jobs') is null and to_regclass('public.comic_generation_jobs_poll') is null and to_regprocedure('public.comic_job_progress_valid(jsonb,jsonb)') is null and to_regprocedure('public.comic_job_command(text,jsonb)') is null), false) then raise exception 'STOP: comic jobs objects already exist; do not rerun'; end if;
end; $preflight$;
do $rpc$ begin
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.comic_panels_valid(jsonb)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='boolean'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_panels']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.comic_panels_valid(jsonb)'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.save_comic_preview_result(jsonb,text)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='jsonb'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_preview','p_fingerprint']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.save_comic_preview_result(jsonb,text)'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.complete_comic_preview_asset(uuid,uuid,uuid)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='jsonb'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_user_id','p_preview_id','p_asset_id']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.complete_comic_preview_asset(uuid,uuid,uuid)'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.attach_diary_comic(uuid,uuid,uuid,text)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='jsonb'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_user_id','p_diary_id','p_preview_id','p_request_id']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.attach_diary_comic(uuid,uuid,uuid,text)'; end if;
end; $rpc$;
do $before$ begin
    perform set_config('today_near.before_data', jsonb_build_object(
'diaries', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diaries t) rows),
'diary_messages', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_messages t) rows),
'diary_assets', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_assets t) rows),
'member_reward_legacy_diaries', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_reward_legacy_diaries t) rows),
'member_reward_wallets', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_reward_wallets t) rows),
'member_daily_reward_grants', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_daily_reward_grants t) rows),
'shop_items', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.shop_items t) rows),
'member_owned_items', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_owned_items t) rows),
'member_room_placements', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_room_placements t) rows),
'member_room_settings', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_room_settings t) rows),
'member_tree_states', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_tree_states t) rows),
'member_tree_watering_events', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_tree_watering_events t) rows),
'comic_previews', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.comic_previews t) rows),
'diary_comics', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_comics t) rows),
'comic_attach_requests', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.comic_attach_requests t) rows),
'diary_edit_requests', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_edit_requests t) rows),
'diary_delete_requests', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_delete_requests t) rows),
'shop_purchase_receipts', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.shop_purchase_receipts t) rows),
'shop_refund_receipts', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.shop_refund_receipts t) rows))::text, true);
end; $before$;
-- BEGIN ORIGINAL COMIC JOBS BODY (unchanged).
create function public.comic_job_progress_valid(p_progress jsonb, p_plan jsonb)
returns boolean language plpgsql immutable security invoker set search_path = ''
as $fn$
declare s jsonb; p jsonb; n integer := 0; a jsonb; test_panels jsonb;
begin
    if jsonb_typeof(p_progress) is distinct from 'object'
       or jsonb_typeof(p_plan->'comic4'->'scenes') is distinct from 'array'
       or jsonb_array_length(p_plan->'comic4'->'scenes') <> 4 then return false; end if;
    for s in select value from jsonb_array_elements(p_plan->'comic4'->'scenes') loop
        if s->'repeated' = 'true'::jsonb then continue; end if;
        n := n + 1;
        p := p_progress->(s->>'scene_id');
        if p is null or jsonb_typeof(p) <> 'object'
           or not (p ?& array['state','asset'])
           or p - array['state','asset'] <> '{}'::jsonb
           or coalesce(p->>'state','') not in
              ('waiting','dispatched','checkpoint','ready','failed','rejected','mock','unknown') then return false; end if;
        a := p->'asset';
        if p->>'state' in ('checkpoint','ready') or (p->>'state'='unknown' and a <> 'null'::jsonb) then
            select jsonb_agg(jsonb_build_object('position',i,'caption','checkpoint','source_ids',jsonb_build_array(0),
                'outcome','pending','asset',a) order by i) into test_panels from generate_series(1,4) i;
            if not public.comic_panels_valid(test_panels) then return false; end if;
        elsif a is distinct from 'null'::jsonb then return false;
        end if;
    end loop;
    return n between 1 and 4 and n = (select count(*) from jsonb_object_keys(p_progress));
exception when others then return false;
end;
$fn$;

create table public.comic_generation_jobs (
    preview_id uuid primary key,
    user_id uuid not null references auth.users(id) on delete restrict,
    session_id text not null check (session_id ~ '^ses_[0-9a-f]{32}$'),
    client_request_id text not null check (client_request_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    history_revision integer not null check (history_revision >= 1),
    draft_id text not null check (draft_id ~ '^draft_[0-9a-f]{32}$'),
    draft_text text not null check (char_length(draft_text) between 1 and 6000),
    confirmed_plan jsonb not null check (jsonb_typeof(confirmed_plan) = 'object'),
    fingerprint text not null check (fingerprint ~ '^[0-9a-f]{64}$'),
    progress jsonb not null,
    state text not null default 'queued' check (state in ('queued','running','completed','unknown')),
    claim_token uuid,
    lease_until timestamptz,
    result_saved boolean not null default false,
    created_at timestamptz not null default clock_timestamp(),
    unique (user_id, session_id, client_request_id),
    unique (user_id, session_id, draft_id, history_revision),
    check (public.comic_job_progress_valid(progress, confirmed_plan)),
    check ((state = 'queued' and claim_token is null and lease_until is null and not result_saved)
        or (state <> 'queued' and claim_token is not null and lease_until is not null)),
    check ((state in ('completed','unknown')) = result_saved)
);
create index comic_generation_jobs_poll on public.comic_generation_jobs(state, lease_until, created_at)
    where state in ('queued','running');
alter table public.comic_generation_jobs enable row level security;
revoke all on public.comic_generation_jobs from public, anon, authenticated, service_role;
-- 회원 JWT는 BE에서 검증한다. 원문/claim token을 포함한 원장은 서버 전용으로 유지한다.
grant select, insert on public.comic_generation_jobs to service_role;
grant update (state, claim_token, lease_until, progress, result_saved) on public.comic_generation_jobs to service_role;

create function public.comic_job_command(p_action text, p_args jsonb)
returns jsonb language plpgsql security invoker set search_path = ''
as $fn$
declare
    v public.comic_generation_jobs%rowtype;
    j jsonb; initial_progress jsonb; scene text; p jsonb; outcome text; token uuid;
    owner uuid; now_at timestamptz; expected jsonb;
begin
    if p_action = 'probe' then
        perform 1 from public.comic_generation_jobs limit 1;
        if pg_catalog.to_regprocedure('public.save_comic_preview_result(jsonb,text)') is null
           or pg_catalog.to_regprocedure('public.complete_comic_preview_asset(uuid,uuid,uuid)') is null then
            raise exception 'comic result functions required'; end if;
        return jsonb_build_object('version',1);
    end if;
    if p_action = 'admit' then
        j := p_args->'job'; owner := (j->>'user_id')::uuid;
        if not exists (select 1 from auth.users a where a.id = owner and a.is_anonymous is false
                       and a.role = 'authenticated' and a.deleted_at is null) then
            raise exception 'invalid owner';
        end if;
        perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(owner::text, 62));
        select * into v from public.comic_generation_jobs where user_id=owner
            and session_id=j->>'session_id' and client_request_id=j->>'client_request_id' for update;
        if found then
            if v.fingerprint is distinct from j->>'fingerprint' then
                return jsonb_build_object('error','IDEMPOTENCY_CONFLICT'); end if;
            return to_jsonb(v);
        end if;
        if exists (select 1 from public.comic_generation_jobs where user_id=owner and session_id=j->>'session_id'
                   and draft_id=j->>'draft_id' and history_revision=(j->>'history_revision')::integer) then
            return jsonb_build_object('error','COMIC_DRAFT_ALREADY_REQUESTED'); end if;
        select jsonb_object_agg(s->>'scene_id',jsonb_build_object('state','waiting','asset',null))
            into initial_progress from jsonb_array_elements(j->'confirmed_plan'->'comic4'->'scenes') s
            where s->'repeated' = 'false'::jsonb;
        insert into public.comic_generation_jobs(preview_id,user_id,session_id,client_request_id,
            history_revision,draft_id,draft_text,confirmed_plan,fingerprint,progress)
        values ((j->>'preview_id')::uuid,owner,j->>'session_id',j->>'client_request_id',
            (j->>'history_revision')::integer,j->>'draft_id',j->>'draft_text',j->'confirmed_plan',j->>'fingerprint',initial_progress)
        returning * into v;
        return to_jsonb(v);
    end if;
    if p_action = 'claim' then
        token := (p_args->>'claim_token')::uuid;
        if token is null then raise exception 'claim token required'; end if;
        select * into v from public.comic_generation_jobs
            where state='queued' or (state='running' and lease_until <= clock_timestamp())
            order by created_at,preview_id for update skip locked limit 1;
        if not found then return null; end if;
        -- 전송 상태를 waiting으로 되돌리지 않는다. 새 담당자는 바이트 확인 또는 unknown 처리만 한다.
        update public.comic_generation_jobs set state='running',claim_token=token,
            lease_until=clock_timestamp()+interval '300 seconds' where preview_id=v.preview_id returning * into v;
        return to_jsonb(v);
    end if;
    select * into v from public.comic_generation_jobs
        where preview_id=(p_args->>'preview_id')::uuid for update;
    now_at := clock_timestamp();
    if not found or v.state <> 'running' or v.claim_token is distinct from (p_args->>'claim_token')::uuid
       or v.lease_until <= now_at then return jsonb_build_object('error','COMIC_CLAIM_LOST'); end if;
    if p_action in ('dispatch','checkpoint','ready','outcome') then
        scene := p_args->>'scene_id'; p := v.progress->scene;
        if p is null then raise exception 'invalid scene'; end if;
        if p_action='dispatch' then
            if p->>'state' <> 'waiting' or exists (select 1 from jsonb_each(v.progress) q
                where q.value->>'state'='unknown') then raise exception 'cannot dispatch'; end if;
            p := jsonb_build_object('state','dispatched','asset',null);
        elsif p_action='checkpoint' then
            if p->>'state' <> 'dispatched' then raise exception 'cannot checkpoint'; end if;
            p := jsonb_build_object('state','checkpoint','asset',p_args->'asset');
        elsif p_action='ready' then
            if p->>'state' <> 'checkpoint' then raise exception 'cannot finish checkpoint'; end if;
            p := jsonb_set(p,'{state}','"ready"');
        else
            outcome := p_args->>'outcome';
            if outcome='unknown' then
                if p->>'state' not in ('waiting','dispatched','checkpoint','ready') then raise exception 'invalid unknown'; end if;
            elsif outcome in ('failed','rejected','mock') then
                if p->>'state' <> 'dispatched' then raise exception 'invalid outcome'; end if;
            else raise exception 'invalid outcome'; end if;
            -- 바이트가 유실돼도 이미 기록한 식별자/해시는 원인 조사용으로 보존한다.
            p := jsonb_build_object('state',outcome,'asset',case when outcome='unknown' then p->'asset' else 'null'::jsonb end);
        end if;
        update public.comic_generation_jobs set progress=jsonb_set(progress,array[scene],p),
            lease_until=now_at+interval '300 seconds' where preview_id=v.preview_id returning * into v;
    elsif p_action='renew' then
        update public.comic_generation_jobs set lease_until=now_at+interval '300 seconds'
            where preview_id=v.preview_id returning * into v;
    elsif p_action='complete' then
        if exists (select 1 from jsonb_each(v.progress) q
            where q.value->>'state' in ('waiting','dispatched','checkpoint')) then raise exception 'unfinished'; end if;
        select jsonb_agg(jsonb_build_object('position',(s->>'order')::integer,'caption',s->>'caption',
            'source_ids',s->'source_ids','outcome',v.progress->(case when s->'repeated'='true'::jsonb
            then s->>'reuse_scene_id' else s->>'scene_id' end)->>'state',
            'asset',case when v.progress->(case when s->'repeated'='true'::jsonb then s->>'reuse_scene_id' else s->>'scene_id' end)->>'state'='ready'
            then v.progress->(case when s->'repeated'='true'::jsonb then s->>'reuse_scene_id' else s->>'scene_id' end)->'asset' else 'null'::jsonb end)
            order by (s->>'order')::integer) into expected
            from jsonb_array_elements(v.confirmed_plan->'comic4'->'scenes') s;
        if not exists (select 1 from public.comic_previews r where r.preview_id=v.preview_id and r.user_id=v.user_id
            and r.session_id=v.session_id and r.client_request_id=v.client_request_id and r.draft_id=v.draft_id
            and r.history_revision=v.history_revision and r.draft_text=v.draft_text and r.panels=expected) then
            raise exception 'result not saved'; end if;
        update public.comic_generation_jobs set result_saved=true,
            state=case when exists(select 1 from jsonb_each(v.progress) q where q.value->>'state'='unknown')
                       then 'unknown' else 'completed' end
            where preview_id=v.preview_id returning * into v;
    else raise exception 'invalid action';
    end if;
    return to_jsonb(v);
end;
$fn$;

revoke all on function public.comic_job_progress_valid(jsonb,jsonb) from public,anon,authenticated;
grant execute on function public.comic_job_progress_valid(jsonb,jsonb) to service_role;
revoke all on function public.comic_job_command(text,jsonb) from public,anon,authenticated;
grant execute on function public.comic_job_command(text,jsonb) to service_role;
-- END ORIGINAL COMIC JOBS BODY.
do $after$ begin
    if current_setting('today_near.before_data')::jsonb is distinct from jsonb_build_object(
'diaries', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diaries t) rows),
'diary_messages', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_messages t) rows),
'diary_assets', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_assets t) rows),
'member_reward_legacy_diaries', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_reward_legacy_diaries t) rows),
'member_reward_wallets', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_reward_wallets t) rows),
'member_daily_reward_grants', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_daily_reward_grants t) rows),
'shop_items', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.shop_items t) rows),
'member_owned_items', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_owned_items t) rows),
'member_room_placements', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_room_placements t) rows),
'member_room_settings', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_room_settings t) rows),
'member_tree_states', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_tree_states t) rows),
'member_tree_watering_events', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_tree_watering_events t) rows),
'comic_previews', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.comic_previews t) rows),
'diary_comics', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_comics t) rows),
'comic_attach_requests', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.comic_attach_requests t) rows),
'diary_edit_requests', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_edit_requests t) rows),
'diary_delete_requests', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_delete_requests t) rows),
'shop_purchase_receipts', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.shop_purchase_receipts t) rows),
'shop_refund_receipts', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.shop_refund_receipts t) rows)) then
        raise exception 'STOP: existing data changed; transaction aborted';
    end if;
end; $after$;
do $acl$ begin
    if exists(select 1 from pg_class c cross join (values('anon'),('authenticated')) r(role_name)
        where c.relnamespace='public'::regnamespace and c.relname in ('comic_generation_jobs')
        and (not c.relrowsecurity or has_any_column_privilege(r.role_name,c.oid,'INSERT,UPDATE')
             or has_table_privilege(r.role_name,c.oid,'DELETE,TRUNCATE')
             or not has_table_privilege('service_role',c.oid,'SELECT'))) then
        raise exception 'STOP: unexpected RLS/client write/server SELECT permissions';
    end if;
end; $acl$;
do $rpc$ begin
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.comic_job_progress_valid(jsonb,jsonb)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='boolean'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_progress','p_plan']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.comic_job_progress_valid(jsonb,jsonb)'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.comic_job_command(text,jsonb)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='jsonb'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_action','p_args']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.comic_job_command(text,jsonb)'; end if;
end; $rpc$;
do $jobs_acl$ begin
    if has_table_privilege('anon','public.comic_generation_jobs','SELECT')
       or has_any_column_privilege('anon','public.comic_generation_jobs','SELECT')
       or has_any_column_privilege('authenticated','public.comic_generation_jobs','SELECT')
       or has_table_privilege('service_role','public.comic_generation_jobs','DELETE,TRUNCATE')
       or exists(select 1 from pg_policy where polrelid='public.comic_generation_jobs'::regclass)
       or exists(select 1 from pg_attribute where attrelid='public.comic_generation_jobs'::regclass
           and attnum>0 and not attisdropped
           and has_column_privilege('service_role',attrelid,attname,'UPDATE')
               <> (attname in ('state','claim_token','lease_until','progress','result_saved')))
       or not has_table_privilege('service_role','public.comic_generation_jobs','INSERT') then
        raise exception 'STOP: job ledger access/immutable columns mismatch';
    end if;
end; $jobs_acl$;
commit;
