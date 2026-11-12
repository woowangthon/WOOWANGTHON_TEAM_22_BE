-- 로컬 후보. 운영 미적용. 기존 comic_previews_v1 뒤 별도 검토/적용한다. 005 불필요.
-- 기존 테이블/함수/Storage 정책은 변경하지 않는다.
begin;
set local lock_timeout = '5s';
set local statement_timeout = '60s';

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
commit;
