-- 로컬 후보 / 운영 미적용. 001~004 뒤 별도 검토. 005·보상 후보와 독립이다.
-- 작업 큐/worker/AI 재호출 RPC가 아니다. 이미 받은 결과의 저장/연결만 담당한다.
-- 기존 private diary-private 버킷·002 정책을 사용하며 Storage 파일 SQL 조작은 없다.

create function public.comic_panels_valid(p_panels jsonb) returns boolean
language plpgsql immutable security invoker set search_path = ''
as $function$
declare
    p jsonb;
    a jsonb;
    s jsonb;
    n integer := 0;
begin
    if p_panels is null or jsonb_typeof(p_panels) <> 'array'
        or jsonb_array_length(p_panels) <> 4 then return false; end if;
    for p in select value from jsonb_array_elements(p_panels) loop
        n := n + 1;
        if jsonb_typeof(p) <> 'object'
            or not (p ?& array['position','caption','source_ids','outcome','asset'])
            or p - array['position','caption','source_ids','outcome','asset'] <> '{}'::jsonb
            or p->'position' <> to_jsonb(n)
            or jsonb_typeof(p->'caption') <> 'string'
            or char_length(p->>'caption') not between 1 and 6000
            or jsonb_typeof(p->'source_ids') <> 'array'
            or jsonb_array_length(p->'source_ids') not between 1 and 80
            or p->>'outcome' not in ('pending','ready','failed','unknown','rejected','mock')
            then return false; end if;
        for s in select value from jsonb_array_elements(p->'source_ids') loop
            if jsonb_typeof(s) <> 'number' or s::text !~ '^(0|[1-9][0-9]?)$'
                or s::text::integer not between 0 and 79 then return false; end if;
        end loop;
        a := p->'asset';
        if p->>'outcome' in ('pending','ready') then
            if jsonb_typeof(a) <> 'object'
                or not (a ?& array['asset_id','mime_type','byte_size','width','height','sha256'])
                or a - array['asset_id','mime_type','byte_size','width','height','sha256'] <> '{}'::jsonb
                or a->>'asset_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                or a->>'mime_type' <> 'image/png'
                or a->>'sha256' !~ '^[0-9a-f]{64}$'
                or jsonb_typeof(a->'byte_size') <> 'number'
                or (a->>'byte_size') !~ '^[0-9]+$'
                or (a->>'byte_size')::bigint not between 1 and 10485760
                or jsonb_typeof(a->'width') <> 'number' or (a->>'width') !~ '^[0-9]+$'
                or jsonb_typeof(a->'height') <> 'number' or (a->>'height') !~ '^[0-9]+$'
                or (a->>'width')::bigint not between 1 and 8192
                or (a->>'height')::bigint not between 1 and 8192
                or (a->>'width')::bigint * (a->>'height')::bigint > 16000000
                then return false; end if;
            -- 같은 자산 ID를 공유하는 반복 컷은 메타데이터와 상태도 같아야 한다.
            if exists (select 1 from jsonb_array_elements(p_panels) q
                where q->'asset'->>'asset_id' = a->>'asset_id'
                and (q->'asset' <> a or q->>'outcome' <> p->>'outcome'))
                then return false; end if;
        elsif a <> 'null'::jsonb then return false;
        end if;
        -- JSON null과 누락으로 SQL NULL check를 우회하지 않는다.
        if p->>'outcome' is null or p->>'caption' is null
            or p->'position' is null or p->'source_ids' is null then return false; end if;
        if p->>'outcome' in ('pending','ready') and (
            a->>'asset_id' is null or a->>'mime_type' is null or a->>'sha256' is null
            or a->>'width' is null or a->>'height' is null or a->>'byte_size' is null)
            then return false; end if;
    end loop;
    return true;
exception when others then return false;
end;
$function$;

create table public.comic_previews (
    preview_id uuid primary key,
    user_id uuid not null references auth.users(id) on delete restrict,
    session_id text not null check (session_id ~ '^ses_[0-9a-f]{32}$'),
    client_request_id text not null check (client_request_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    history_revision integer not null check (history_revision >= 1),
    draft_id text not null check (draft_id ~ '^draft_[0-9a-f]{32}$'),
    draft_text text not null check (char_length(draft_text) between 1 and 6000
                                    and draft_text ~ '[^[:space:]]'),
    panels jsonb not null check (public.comic_panels_valid(panels)),
    result_fingerprint text not null check (result_fingerprint ~ '^[0-9a-f]{64}$'),
    created_at timestamptz not null default now(),
    unique (preview_id, user_id),
    unique (user_id, session_id, client_request_id)
);

create table public.diary_comics (
    diary_id uuid primary key,
    user_id uuid not null,
    preview_id uuid not null unique,
    created_at timestamptz not null default now(),
    foreign key (diary_id, user_id) references public.diaries(id, user_id) on delete restrict,
    foreign key (preview_id, user_id) references public.comic_previews(preview_id, user_id) on delete restrict,
    unique (diary_id, user_id, preview_id)
);

create table public.comic_attach_requests (
    user_id uuid not null,
    client_request_id text not null check (client_request_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    diary_id uuid not null,
    preview_id uuid not null,
    primary key (user_id, client_request_id),
    foreign key (diary_id, user_id, preview_id)
        references public.diary_comics(diary_id, user_id, preview_id) on delete restrict
);

alter table public.comic_previews enable row level security;
alter table public.diary_comics enable row level security;
alter table public.comic_attach_requests enable row level security;
revoke all on public.comic_previews, public.diary_comics, public.comic_attach_requests
    from public, anon, authenticated, service_role;
grant select on public.comic_previews, public.diary_comics to authenticated;
grant select, insert on public.comic_previews, public.diary_comics, public.comic_attach_requests to service_role;
grant update (panels) on public.comic_previews to service_role;
-- attach의 SELECT ... FOR SHARE는 한 열 이상의 UPDATE 권한도 요구한다.
-- 일기 내용 갱신 권한을 넓히지 않고 잠금에 필요한 ID 열만 명시한다.
grant update (id) on public.diaries to service_role;
create policy comic_previews_select_own on public.comic_previews for select to authenticated
    using (user_id = (select auth.uid()) and (select auth.jwt()->>'is_anonymous') = 'false');
create policy diary_comics_select_own on public.diary_comics for select to authenticated
    using (user_id = (select auth.uid()) and (select auth.jwt()->>'is_anonymous') = 'false');
-- 요청 영수증은 서버 전용이며 일반 회원 SELECT 정책도 두지 않는다.

create function public.save_comic_preview_result(p_preview jsonb, p_fingerprint text) returns jsonb
language plpgsql security invoker set search_path = ''
as $function$
declare
    v public.comic_previews%rowtype;
    u uuid := (p_preview->>'user_id')::uuid;
begin
    if p_fingerprint is null or p_fingerprint !~ '^[0-9a-f]{64}$' then
        raise exception 'invalid fingerprint'; end if;
    if not exists (select 1 from auth.users a where a.id = u
        and a.is_anonymous is false and a.role = 'authenticated' and a.deleted_at is null)
        then raise exception 'invalid owner'; end if;
    if not public.comic_panels_valid(p_preview->'panels')
        or exists (select 1 from jsonb_array_elements(p_preview->'panels') p
                   where p->>'outcome' = 'ready') then raise exception 'invalid result'; end if;
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(u::text, 61));
    select * into v from public.comic_previews p where p.user_id = u
        and p.session_id = p_preview->>'session_id'
        and p.client_request_id = p_preview->>'client_request_id' for update;
    if found then
        if v.result_fingerprint <> p_fingerprint then
            return jsonb_build_object('error','IDEMPOTENCY_CONFLICT'); end if;
        return to_jsonb(v) - array['result_fingerprint','created_at'];
    end if;
    insert into public.comic_previews(preview_id, user_id, session_id, client_request_id,
        history_revision, draft_id, draft_text, panels, result_fingerprint)
    values ((p_preview->>'preview_id')::uuid, u, p_preview->>'session_id',
        p_preview->>'client_request_id', (p_preview->>'history_revision')::integer,
        p_preview->>'draft_id', p_preview->>'draft_text', p_preview->'panels', p_fingerprint)
    returning * into v;
    return to_jsonb(v) - array['result_fingerprint','created_at'];
end;
$function$;

create function public.complete_comic_preview_asset(p_user_id uuid, p_preview_id uuid, p_asset_id uuid)
returns jsonb language plpgsql security invoker set search_path = ''
as $function$
declare
    v public.comic_previews%rowtype;
begin
    if not exists (select 1 from auth.users a where a.id = p_user_id
        and a.is_anonymous is false and a.role = 'authenticated' and a.deleted_at is null)
        then raise exception 'invalid owner'; end if;
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_user_id::text, 61));
    select * into v from public.comic_previews p
        where p.preview_id = p_preview_id and p.user_id = p_user_id for update;
    if not found then return jsonb_build_object('error','COMIC_PREVIEW_NOT_FOUND'); end if;
    if p_asset_id is null or not exists (select 1 from jsonb_array_elements(v.panels) p
        where p->'asset'->>'asset_id' = p_asset_id::text and p->>'outcome' in ('pending','ready'))
        then raise exception 'invalid asset'; end if;
    -- BE가 Storage 업로드 성공/기존 객체 해시 일치를 확인한 뒤에만 호출한다.
    update public.comic_previews set panels = (
        select jsonb_agg(case when p->'asset'->>'asset_id' = p_asset_id::text
            then jsonb_set(p, '{outcome}', '"ready"'::jsonb) else p end order by ord)
        from jsonb_array_elements(v.panels) with ordinality q(p, ord))
        where preview_id = p_preview_id returning * into v;
    return to_jsonb(v) - array['result_fingerprint','created_at'];
end;
$function$;

create function public.attach_diary_comic(p_user_id uuid, p_diary_id uuid, p_preview_id uuid, p_request_id text)
returns jsonb language plpgsql security invoker set search_path = ''
as $function$
declare
    v public.comic_previews%rowtype;
    d public.diaries%rowtype;
    r public.comic_attach_requests%rowtype;
    existing public.diary_comics%rowtype;
begin
    if p_request_id is null or p_request_id !~ '^[A-Za-z0-9_-]{1,128}$'
        or not exists (select 1 from auth.users a where a.id = p_user_id
        and a.is_anonymous is false and a.role = 'authenticated' and a.deleted_at is null)
        then raise exception 'invalid owner or request'; end if;
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_user_id::text, 61));
    select * into d from public.diaries where id = p_diary_id and user_id = p_user_id for share;
    if not found then return jsonb_build_object('error','COMIC_PREVIEW_MISMATCH'); end if;
    select * into v from public.comic_previews
        where preview_id = p_preview_id and user_id = p_user_id for update;
    if not found then return jsonb_build_object('error','COMIC_PREVIEW_NOT_FOUND'); end if;
    select * into r from public.comic_attach_requests
        where user_id = p_user_id and client_request_id = p_request_id;
    if found and (r.diary_id <> p_diary_id or r.preview_id <> p_preview_id) then
        return jsonb_build_object('error','IDEMPOTENCY_CONFLICT'); end if;
    if d.confirmed_at is null or d.confirmation_fingerprint is null
        or d.source_session_id <> v.session_id or d.source_history_revision <> v.history_revision
        or d.ai_draft->>'draft_id' is distinct from v.draft_id
        or d.ai_draft->>'text' is distinct from v.draft_text
        or d.confirmed_text is distinct from v.draft_text then
        return jsonb_build_object('error','COMIC_PREVIEW_MISMATCH'); end if;
    if exists (select 1 from jsonb_array_elements(v.panels) p where p->>'outcome' = 'pending')
        or not exists (select 1 from jsonb_array_elements(v.panels) p where p->>'outcome' = 'ready') then
        return jsonb_build_object('error','COMIC_NOT_READY'); end if;
    select * into existing from public.diary_comics
        where diary_id = p_diary_id or preview_id = p_preview_id;
    if found and (existing.diary_id <> p_diary_id or existing.preview_id <> p_preview_id) then
        return jsonb_build_object('error','COMIC_ALREADY_ATTACHED'); end if;
    insert into public.diary_comics(diary_id,user_id,preview_id)
        values (p_diary_id,p_user_id,p_preview_id) on conflict (diary_id) do nothing;
    insert into public.comic_attach_requests(user_id,client_request_id,diary_id,preview_id)
        values (p_user_id,p_request_id,p_diary_id,p_preview_id)
        on conflict (user_id,client_request_id) do nothing;
    return to_jsonb(v) - array['result_fingerprint','created_at'];
end;
$function$;

revoke all on function public.comic_panels_valid(jsonb) from public, anon, authenticated;
grant execute on function public.comic_panels_valid(jsonb) to service_role;
revoke all on function public.save_comic_preview_result(jsonb,text) from public, anon, authenticated;
grant execute on function public.save_comic_preview_result(jsonb,text) to service_role;
revoke all on function public.complete_comic_preview_asset(uuid,uuid,uuid) from public, anon, authenticated;
grant execute on function public.complete_comic_preview_asset(uuid,uuid,uuid) to service_role;
revoke all on function public.attach_diary_comic(uuid,uuid,uuid,text) from public, anon, authenticated;
grant execute on function public.attach_diary_comic(uuid,uuid,uuid,text) to service_role;
