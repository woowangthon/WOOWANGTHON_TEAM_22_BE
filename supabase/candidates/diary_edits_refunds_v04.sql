-- REVIEW ONLY. 운영 미적용. 001~004 + room_rewards_v031 + comic_previews_v1 후 별도 검토.
-- 005는 불필요. 단일 READ COMMITTED 트랜잭션/쓰기 중단/사전 백업을 전제로 한다.
-- 이 후보는 기존 함수 교체와 날짜 제약 변경을 포함한다. 자동 migration 경로에 복사 금지.
lock table public.diaries in access exclusive mode;
lock table public.member_daily_reward_grants, public.member_owned_items in access exclusive mode;

alter table public.diaries
    add column edit_version integer not null default 1 check (edit_version >= 1),
    add column updated_at timestamptz,
    add column deleted_at timestamptz,
    add column active_drawing_edit_id uuid;
update public.diaries set updated_at = confirmed_at;
alter table public.diaries alter column updated_at set default now();
alter table public.diaries alter column updated_at set not null;
create table public.diary_edit_requests (
    request_id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    diary_id uuid not null,
    client_request_id text not null check (client_request_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    fingerprint text not null check (fingerprint ~ '^[0-9a-f]{64}$'),
    expected_version integer not null check (expected_version between 1 and 2147483646),
    confirmed_text text not null check (char_length(confirmed_text) between 1 and 20000 and confirmed_text ~ '[^[:space:]]'),
    drawing jsonb,
    state text not null default 'pending' check (state in ('pending','completed')),
    result jsonb,
    created_at timestamptz not null default now(),
    unique(user_id, client_request_id),
    unique(request_id, diary_id, user_id),
    foreign key(diary_id, user_id) references public.diaries(id, user_id) on delete restrict,
    check ((state = 'completed') = (result is not null)),
    check (drawing is null or coalesce(
        jsonb_typeof(drawing) = 'object'
        and drawing->>'user_id' = user_id::text and drawing->>'diary_id' = diary_id::text
        and drawing->>'id' = request_id::text and drawing->>'asset_kind' = 'original_drawing'
        and drawing->>'scene_index' = '0' and drawing->>'bucket_id' = 'diary-private'
        and drawing->>'mime_type' = 'image/png' and drawing->>'sha256' ~ '^[0-9a-f]{64}$'
        and drawing->>'storage_key' = user_id::text || '/' || diary_id::text || '/original_drawing/' || request_id::text || '.png'
        and (drawing->>'byte_size')::bigint between 1 and 10485760
        and (drawing->>'width')::integer between 1 and 8192
        and (drawing->>'height')::integer between 1 and 8192
        and (drawing->>'width')::bigint * (drawing->>'height')::bigint <= 16000000
        and drawing->>'upload_status' = case state when 'pending' then 'pending' else 'ready' end,
        false))
);
alter table public.diaries add constraint diaries_active_edit_v04_fk
    foreign key(active_drawing_edit_id, id, user_id)
    references public.diary_edit_requests(request_id, diary_id, user_id) on delete restrict;

alter table public.diary_edit_requests enable row level security;
revoke all on public.diary_edit_requests from public, anon, authenticated, service_role;
grant select on public.diary_edit_requests to authenticated;
grant select, insert on public.diary_edit_requests to service_role;
grant update(drawing,state,result) on public.diary_edit_requests to service_role;
grant update(confirmed_text,edit_version,updated_at,deleted_at,active_drawing_edit_id) on public.diaries to service_role;

create policy edit_drawing_read_v04 on public.diary_edit_requests for select to authenticated
    using (user_id = (select auth.uid()) and (select auth.jwt()->>'is_anonymous') = 'false'
        and state = 'completed' and exists(select 1 from public.diaries d where d.id = diary_id and d.deleted_at is null));
alter table public.diary_comics add column source_edit_version integer not null default 1 check (source_edit_version >= 1);

create function public.member_diary_edit(p_user_id uuid, p_diary_id uuid, p_action text, p_payload jsonb)
returns jsonb language plpgsql security invoker set search_path = ''
as $function$
declare
    d public.diaries%rowtype;
    r public.diary_edit_requests%rowtype;
    a jsonb;
    v_result jsonb;
    ts timestamptz;
    aid uuid;
begin
    if p_user_id is null or not exists(select 1 from auth.users u where u.id = p_user_id
        and u.is_anonymous is false and u.role = 'authenticated' and u.deleted_at is null) then
        raise exception using errcode='22023',message='invalid member'; end if;
    if p_diary_id is null or p_action is null or p_action not in ('reserve','finish')
        or p_payload is null or jsonb_typeof(p_payload) <> 'object'
        or not coalesce(jsonb_typeof(p_payload->'client_request_id') = 'string'
            and p_payload->>'client_request_id' ~ '^[A-Za-z0-9_-]{1,128}$',false) then
        raise exception using errcode='PT422',message='invalid edit',detail='DIARY_EDIT_INVALID'; end if;
    -- 확정 저장과 삭제는 같은 회원 잠금(0), 이후 일기 행 잠금 순서를 따른다.
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_user_id::text,0));
    select * into d from public.diaries where id = p_diary_id and user_id = p_user_id for update;
    if not found then raise exception using errcode='PT404',message='not found',detail='DIARY_NOT_FOUND'; end if;
    if d.deleted_at is not null or d.confirmed_at is null then
        raise exception using errcode='PT404',message='not found',detail='DIARY_NOT_FOUND'; end if;
    if not coalesce(jsonb_typeof(p_payload->'fingerprint')='string'
        and p_payload->>'fingerprint' ~ '^[0-9a-f]{64}$',false) then
        raise exception using errcode='PT422',message='invalid edit',detail='DIARY_EDIT_INVALID'; end if;
    select * into r from public.diary_edit_requests where user_id=p_user_id
        and client_request_id=p_payload->>'client_request_id' for update;
    if found then
        if r.diary_id <> p_diary_id or r.fingerprint <> p_payload->>'fingerprint' then
            raise exception using errcode='PT409',message='request conflict',detail='IDEMPOTENCY_CONFLICT'; end if;
        if r.state='completed' then return to_jsonb(r); end if;
    end if;
    if p_action='reserve' then
        if not coalesce(jsonb_typeof(p_payload->'expected_version') = 'number'
            and p_payload->>'expected_version' ~ '^[1-9][0-9]{0,9}$'
            and (p_payload->>'expected_version')::bigint <= 2147483646
            and jsonb_typeof(p_payload->'confirmed_text')='string'
            and char_length(p_payload->>'confirmed_text') between 1 and 20000
            and p_payload->>'confirmed_text' ~ '[^[:space:]]',false)
            or not (p_payload ? 'drawing')
            or (p_payload - array['client_request_id','expected_version','confirmed_text','fingerprint','drawing']) <> '{}'::jsonb then
            raise exception using errcode='PT422',message='invalid edit',detail='DIARY_EDIT_INVALID'; end if;
        if d.edit_version <> (p_payload->>'expected_version')::integer then
            raise exception using errcode='PT409',message='version conflict',detail='DIARY_EDIT_VERSION_CONFLICT'; end if;
        if r.request_id is not null then return to_jsonb(r); end if;
        aid := gen_random_uuid();
        a := nullif(p_payload->'drawing','null'::jsonb);
        if a is not null then
            if not coalesce(jsonb_typeof(a)='object' and a ?& array['mime_type','byte_size','width','height','sha256']
                and (a-array['mime_type','byte_size','width','height','sha256'])='{}'::jsonb,false) then
                raise exception using errcode='PT422',message='invalid image',detail='DIARY_EDIT_INVALID'; end if;
            a := a || jsonb_build_object('id',aid,'user_id',p_user_id,'diary_id',p_diary_id,
                'asset_kind','original_drawing','scene_index',0,'bucket_id','diary-private','upload_status','pending',
                'storage_key',p_user_id::text||'/'||p_diary_id::text||'/original_drawing/'||aid::text||'.png');
        end if;
        insert into public.diary_edit_requests(request_id,user_id,diary_id,client_request_id,fingerprint,expected_version,confirmed_text,drawing)
            values(aid,p_user_id,p_diary_id,p_payload->>'client_request_id',p_payload->>'fingerprint',
                (p_payload->>'expected_version')::integer,p_payload->>'confirmed_text',a) returning * into r;
        return to_jsonb(r);
    end if;
    if (p_payload-array['client_request_id','fingerprint']) <> '{}'::jsonb or r.request_id is null then
        raise exception using errcode='PT422',message='invalid receipt',detail='DIARY_EDIT_INVALID'; end if;
    if d.edit_version <> r.expected_version then
        raise exception using errcode='PT409',message='version conflict',detail='DIARY_EDIT_VERSION_CONFLICT'; end if;
    -- 바이트 업로드/해시 확인은 신뢰된 BE가 수행한다. DB 완료 호출은 그 뒤에만 허용한다.
    a := r.drawing;
    if a is not null then
        a := a || jsonb_build_object('upload_status','ready');
    elsif d.active_drawing_edit_id is not null then
        select drawing into a from public.diary_edit_requests where request_id=d.active_drawing_edit_id and state='completed';
    else
        select to_jsonb(x) into a from public.diary_assets x where x.diary_id=p_diary_id
            and x.user_id=p_user_id and x.asset_kind='original_drawing' and x.scene_index=0 and x.upload_status='ready';
    end if;
    ts := clock_timestamp();
    v_result := jsonb_build_object('diary_id',p_diary_id,'edit_version',d.edit_version+1,
        'confirmed_text',r.confirmed_text,'updated_at',ts,'original_drawing',a);
    update public.diary_edit_requests set state='completed',result=v_result,
        drawing=case when r.drawing is null then null else a end where request_id=r.request_id returning * into r;
    update public.diaries set confirmed_text=r.confirmed_text,edit_version=edit_version+1,updated_at=ts,
        active_drawing_edit_id=case when r.drawing is not null then r.request_id else active_drawing_edit_id end
        where id=p_diary_id;
    return to_jsonb(r);
end;
$function$;
revoke all on function public.member_diary_edit(uuid,uuid,text,jsonb) from public,anon,authenticated;
grant execute on function public.member_diary_edit(uuid,uuid,text,jsonb) to service_role;

-- 이하 기존 RPC의 v0.4 교체본은 생성 스크립트가 원본을 보존하며 추가한다.
-- BEGIN GENERATED REPLACEMENTS

create or replace function public.reserve_diary_drawing(
    p_user_id uuid, p_diary_id uuid, p_mime_type text, p_byte_size bigint,
    p_width integer, p_height integer, p_sha256 text
) returns jsonb
language plpgsql security invoker set search_path = ''
as $function$
declare
    v_asset public.diary_assets%rowtype;
    v_id uuid;
begin
    perform 1 from public.diaries d where d.id=p_diary_id and d.user_id=p_user_id and d.deleted_at is null for share;
    if not found then raise exception using errcode='PT404',message='not found',detail='DIARY_NOT_FOUND'; end if;
    if exists(select 1 from public.diaries where id=p_diary_id and edit_version <> 1) then
        raise exception using errcode='PT409',message='use edit API',detail='DIARY_EDIT_VERSION_CONFLICT'; end if;
    -- 회원 소유권 확인
    if p_user_id is null or p_diary_id is null or not exists (
        select 1 from auth.users u where u.id = p_user_id
            and u.is_anonymous is false and u.role = 'authenticated' and u.deleted_at is null
    ) or not exists (
        select 1 from public.diaries d where d.id = p_diary_id and d.user_id = p_user_id
    ) then
        raise exception using errcode = '22023', message = 'invalid owner';
    end if;
    if p_mime_type is null or p_mime_type not in ('image/png', 'image/jpeg')
        or p_byte_size is null or p_byte_size not between 1 and 10485760
        or p_width is null or p_width not between 1 and 8192
        or p_height is null or p_height not between 1 and 8192
        or p_width::bigint * p_height::bigint > 16000000
        or p_sha256 is null or p_sha256 !~ '^[0-9a-f]{64}$' then
        raise exception using errcode = '22023', message = 'invalid drawing';
    end if;
    -- 일기별 원본 예약 직렬화
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_diary_id::text, 4));
    select a.* into v_asset from public.diary_assets a
    where a.diary_id = p_diary_id and a.user_id = p_user_id
        and a.asset_kind = 'original_drawing' and a.scene_index = 0;
    if found then
        if v_asset.sha256 <> p_sha256 or v_asset.mime_type <> p_mime_type
            or v_asset.byte_size <> p_byte_size or v_asset.width <> p_width
            or v_asset.height <> p_height then
            raise exception using errcode = 'PT409', message = '이미 저장된 그림',
                detail = 'DRAWING_CONFLICT';
        end if;
        return pg_catalog.to_jsonb(v_asset);
    end if;
    v_id := gen_random_uuid();
    insert into public.diary_assets (
        id, diary_id, user_id, asset_kind, scene_index, bucket_id, storage_key,
        mime_type, byte_size, width, height, sha256, upload_status
    ) values (
        v_id, p_diary_id, p_user_id, 'original_drawing', 0, 'diary-private',
        p_user_id::text || '/' || p_diary_id::text || '/original_drawing/' || v_id::text
            || case p_mime_type when 'image/png' then '.png' else '.jpg' end,
        p_mime_type, p_byte_size, p_width, p_height, p_sha256, 'pending'
    ) returning * into v_asset;
    return pg_catalog.to_jsonb(v_asset);
end;
$function$;

create or replace function public.complete_diary_drawing(p_user_id uuid, p_diary_id uuid, p_asset_id uuid)
returns void language plpgsql security invoker set search_path = ''
as $function$
begin
    perform 1 from public.diaries d where d.id=p_diary_id and d.user_id=p_user_id and d.deleted_at is null for share;
    if not found then raise exception using errcode='PT404',message='not found',detail='DIARY_NOT_FOUND'; end if;
    if exists(select 1 from public.diaries where id=p_diary_id and edit_version <> 1) then
        raise exception using errcode='PT409',message='use edit API',detail='DIARY_EDIT_VERSION_CONFLICT'; end if;
    -- 회원 소유권 재확인
    if not exists (
        select 1 from auth.users u where u.id = p_user_id
            and u.is_anonymous is false and u.role = 'authenticated' and u.deleted_at is null
    ) then
        raise exception using errcode = '22023', message = 'invalid owner';
    end if;
    update public.diary_assets a set upload_status = 'ready', updated_at = now()
    where a.id = p_asset_id and a.user_id = p_user_id and a.diary_id = p_diary_id
        and a.asset_kind = 'original_drawing' and a.scene_index = 0
        and exists (select 1 from public.diaries d
                    where d.id = p_diary_id and d.user_id = p_user_id);
    if not found then
        raise exception using errcode = '22023', message = 'invalid asset';
    end if;
end;
$function$;

create or replace function public.attach_diary_comic(p_user_id uuid, p_diary_id uuid, p_preview_id uuid, p_request_id text)
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
    select * into d from public.diaries where id = p_diary_id and user_id = p_user_id and deleted_at is null for share;
    if not found then return jsonb_build_object('error','COMIC_PREVIEW_MISMATCH'); end if;
    select * into v from public.comic_previews
        where preview_id = p_preview_id and user_id = p_user_id for update;
    if not found then return jsonb_build_object('error','COMIC_PREVIEW_NOT_FOUND'); end if;
    select * into r from public.comic_attach_requests
        where user_id = p_user_id and client_request_id = p_request_id;
    if found and (r.diary_id <> p_diary_id or r.preview_id <> p_preview_id) then
        return jsonb_build_object('error','IDEMPOTENCY_CONFLICT'); end if;
    if d.edit_version <> 1 or d.confirmed_at is null or d.confirmation_fingerprint is null
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
