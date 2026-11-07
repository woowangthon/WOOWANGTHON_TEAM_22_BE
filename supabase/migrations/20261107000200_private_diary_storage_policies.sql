-- 검토용 초안: 001 이후 적용합니다. 버킷 준비와 위험은 docs/DIARY_STORAGE_POLICY.md를 따릅니다.
-- diary-private 버킷은 Storage API/Dashboard에서 비공개로 준비하며 파일은 SQL로 조작하지 않습니다.
-- 파일과 적용 이력의 트랜잭션은 CLI가 관리하며 이 블록은 버킷 상태만 검사합니다.
do $bucket_check$
begin
    if not exists (
        select 1 from storage.buckets as b
        where b.id = 'diary-private' and b.public is false
    ) then
        raise exception 'diary-private 버킷을 먼저 비공개(public=false)로 준비해야 합니다.';
    end if;
end;
$bucket_check$;

-- 기존의 넓은 허용 정책이 있어도 이 버킷의 소유권 제한을 우회하지 못하게 합니다.
create policy diary_storage_select_guard on storage.objects
    as restrictive for select to public
    using (
        bucket_id <> 'diary-private'
        or (
            (select auth.jwt() ->> 'is_anonymous') = 'false'
            and (storage.foldername(name))[1] = (select auth.uid())::text
            and name ~ '^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/){2}(original_drawing|comic_scene)/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(jpg|png)$'
        )
    );

-- 기존의 넓은 INSERT 정책이 있어도 이 버킷의 회원 직접 업로드를 허용하지 않습니다.
-- BE 업로드는 미구현이며 인증·파일/경로 검증과 최소 권한 쓰기를 별도로 설계해야 합니다.
create policy diary_storage_insert_guard on storage.objects
    as restrictive for insert to public
    with check (bucket_id <> 'diary-private');

-- 수정/이동/덮어쓰기와 삭제는 보존 정책 합의 전 일반 요청에서 차단합니다.
create policy diary_storage_update_guard on storage.objects
    as restrictive for update to public
    using (bucket_id <> 'diary-private')
    with check (bucket_id <> 'diary-private');
create policy diary_storage_delete_guard on storage.objects
    as restrictive for delete to public
    using (bucket_id <> 'diary-private');

-- 회원의 원본/만화만 읽으며 service_role 등 RLS 우회 경로의 검증 책임은 서버에 있습니다.
create policy diary_storage_select_own on storage.objects
    for select to authenticated
    using (
        bucket_id = 'diary-private'
        and (select auth.jwt() ->> 'is_anonymous') = 'false'
        and (storage.foldername(name))[1] = (select auth.uid())::text
        and name ~ '^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/){2}(original_drawing|comic_scene)/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(jpg|png)$'
    );
