-- 검토용 초안: 001 이후 적용합니다. 버킷 준비와 위험은 docs/DIARY_STORAGE_POLICY.md를 따릅니다.
-- diary-private 버킷은 Storage API/Dashboard에서 비공개로 준비하며 파일은 SQL로 조작하지 않습니다.
begin;

-- 기존의 넓은 허용 정책이 있어도 이 버킷의 소유권 제한을 우회하지 못하게 합니다.
create policy diary_storage_select_guard on storage.objects
    as restrictive for select to public
    using (
        bucket_id <> 'diary-private'
        or (
            (select auth.jwt() ->> 'is_anonymous') = 'false'
            and (storage.foldername(name))[1] = (select auth.uid())::text
            and name ~ '^[0-9a-f-]{36}/[0-9a-f-]{36}/(original_drawing|comic_scene)/[0-9a-f-]{36}\.(jpg|png)$'
        )
    );

-- DB 확정 전 원본을 올릴 수 있으나 본인 경로의 새 파일만 허용합니다.
create policy diary_storage_insert_guard on storage.objects
    as restrictive for insert to public
    with check (
        bucket_id <> 'diary-private'
        or (
            (select auth.jwt() ->> 'is_anonymous') = 'false'
            and (storage.foldername(name))[1] = (select auth.uid())::text
            and name ~ '^[0-9a-f-]{36}/[0-9a-f-]{36}/original_drawing/[0-9a-f-]{36}\.(jpg|png)$'
        )
    );

-- 수정/이동/덮어쓰기와 삭제는 보존 정책 합의 전 일반 요청에서 차단합니다.
create policy diary_storage_update_guard on storage.objects
    as restrictive for update to public
    using (bucket_id <> 'diary-private')
    with check (bucket_id <> 'diary-private');
create policy diary_storage_delete_guard on storage.objects
    as restrictive for delete to public
    using (bucket_id <> 'diary-private');

create policy diary_storage_select_own on storage.objects
    for select to authenticated
    using (
        bucket_id = 'diary-private'
        and (select auth.jwt() ->> 'is_anonymous') = 'false'
        and (storage.foldername(name))[1] = (select auth.uid())::text
        and name ~ '^[0-9a-f-]{36}/[0-9a-f-]{36}/(original_drawing|comic_scene)/[0-9a-f-]{36}\.(jpg|png)$'
    );
create policy diary_storage_insert_own_original on storage.objects
    for insert to authenticated
    with check (
        bucket_id = 'diary-private'
        and (select auth.jwt() ->> 'is_anonymous') = 'false'
        and (storage.foldername(name))[1] = (select auth.uid())::text
        and name ~ '^[0-9a-f-]{36}/[0-9a-f-]{36}/original_drawing/[0-9a-f-]{36}\.(jpg|png)$'
    );

commit;
