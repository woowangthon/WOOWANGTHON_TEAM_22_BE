-- 로컬 준비본 / 원격 미적용
alter table public.diary_assets
    add column upload_status text not null default 'ready',
    add column updated_at timestamptz not null default now();
alter table public.diary_assets add constraint diary_assets_upload_status_ck
    check (upload_status in ('pending', 'ready'));
alter table public.diary_assets add constraint diary_assets_pending_kind_ck
    check (upload_status = 'ready' or asset_kind = 'original_drawing');

-- 서버 전용 자산 기록 권한
grant select, insert on public.diary_assets to service_role;
grant update (upload_status, updated_at) on public.diary_assets to service_role;

create function public.reserve_diary_drawing(
    p_user_id uuid, p_diary_id uuid, p_mime_type text, p_byte_size bigint,
    p_width integer, p_height integer, p_sha256 text
) returns jsonb
language plpgsql security invoker set search_path = ''
as $function$
declare
    v_asset public.diary_assets%rowtype;
    v_id uuid;
begin
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

create function public.complete_diary_drawing(p_user_id uuid, p_diary_id uuid, p_asset_id uuid)
returns void language plpgsql security invoker set search_path = ''
as $function$
begin
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

revoke all on function public.reserve_diary_drawing(uuid, uuid, text, bigint, integer, integer, text)
    from public, anon, authenticated;
revoke all on function public.complete_diary_drawing(uuid, uuid, uuid)
    from public, anon, authenticated;
grant execute on function public.reserve_diary_drawing(uuid, uuid, text, bigint, integer, integer, text)
    to service_role;
grant execute on function public.complete_diary_drawing(uuid, uuid, uuid) to service_role;
