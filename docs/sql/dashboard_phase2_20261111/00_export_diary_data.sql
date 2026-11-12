-- READ ONLY: 한 문장/한 스냅샷/한 결과 행. SQL Editor의 전체 결과를 CSV로 저장한다.
-- 결과에는 실제 개인정보가 있으므로 채팅/보고서/Git에 붙이지 않는다.
-- auth.users 로그인 데이터, Storage 파일 바이트, 스키마/RPC/RLS는 포함하지 않는다.
-- export_version=1, complete=true 및 배열 길이=counts 확인. 응답 크기 제한/잘림 시 백업 실패.
-- SQL Editor Role=postgres 필수. 다른 역할의 RLS 부분 내보내기를 백업으로 오인하지 않는다.
with d as (select coalesce(jsonb_agg(to_jsonb(t) order by t.id), '[]'::jsonb) rows from public.diaries t),
     m as (select coalesce(jsonb_agg(to_jsonb(t) order by t.id), '[]'::jsonb) rows from public.diary_messages t),
     a as (select coalesce(jsonb_agg(to_jsonb(t) order by t.id), '[]'::jsonb) rows from public.diary_assets t)
select jsonb_build_object(
    'export_version', 1, 'captured_at', statement_timestamp(),
    'counts', jsonb_build_object('diaries', jsonb_array_length(d.rows),
        'diary_messages', jsonb_array_length(m.rows), 'diary_assets', jsonb_array_length(a.rows)),
    'data', jsonb_build_object('diaries', d.rows, 'diary_messages', m.rows, 'diary_assets', a.rows),
    'complete', true
) as backup_json
from d cross join m cross join a
where current_user = 'postgres';
