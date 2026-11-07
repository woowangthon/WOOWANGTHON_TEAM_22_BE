-- 검토용 초안: 실제 DB에 적용하지 않았습니다. 선행 검토는 docs/DIARY_DB_SCHEMA.md를 따릅니다.
-- 파일과 적용 이력의 원자성은 Supabase CLI에 맡기며 직접 COMMIT하지 않습니다.

-- 확인된 일기 한 권을 저장하며 대화 원문과 AI 초안은 별도로 보존합니다.
create table public.diaries (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null default auth.uid(),
    diary_date date not null default ((now() at time zone 'Asia/Seoul')::date),
    weather text,
    confirmed_text text not null,
    confirmed_at timestamptz not null,
    source_session_id text not null,
    source_history_revision integer not null,
    source_client_request_id text not null,
    ai_draft jsonb not null,
    created_at timestamptz not null default now(),
    constraint diaries_user_fk foreign key (user_id)
        references auth.users (id) on update restrict on delete restrict,
    -- 하루 한 권은 초기 후보 정책이며 복수 아이/복수 권 정책 합의 시 변경해야 합니다.
    constraint diaries_user_date_uq unique (user_id, diary_date),
    constraint diaries_id_user_uq unique (id, user_id),
    constraint diaries_user_session_request_uq
        unique (user_id, source_session_id, source_client_request_id),
    constraint diaries_weather_ck check (
        weather in ('sunny', 'cloudy', 'snowy', 'rainy')
    ),
    constraint diaries_confirmed_text_ck check (
        char_length(confirmed_text) between 1 and 20000
        and confirmed_text ~ '[^[:space:]]'
    ),
    constraint diaries_session_id_ck check (
        source_session_id ~ '^ses_[0-9a-f]{32}$'
    ),
    constraint diaries_history_revision_ck check (source_history_revision >= 1),
    constraint diaries_request_id_ck check (
        char_length(source_client_request_id) between 1 and 128
        and source_client_request_id ~ '^[A-Za-z0-9_-]+$'
    ),
    -- 세부 출처 검증은 BE 계약 검사에서 수행하고 DB는 필수 구조의 누락을 거절합니다.
    constraint diaries_ai_draft_ck check (coalesce(
        jsonb_typeof(ai_draft) = 'object'
        and ai_draft ?& array[
            'draft_id', 'status', 'text', 'pages', 'sources',
            'fallback', 'requires_confirmation', 'mock'
        ]
        and jsonb_typeof(ai_draft -> 'draft_id') = 'string'
        and (ai_draft ->> 'draft_id') ~ '^draft_[0-9a-f]{32}$'
        and ai_draft ->> 'status' = 'draft'
        and jsonb_typeof(ai_draft -> 'text') = 'string'
        and char_length(ai_draft ->> 'text') between 1 and 20000
        and (ai_draft ->> 'text') ~ '[^[:space:]]'
        and jsonb_typeof(ai_draft -> 'pages') = 'array'
        and jsonb_typeof(ai_draft -> 'sources') = 'array'
        and jsonb_typeof(ai_draft -> 'fallback') = 'boolean'
        and ai_draft -> 'requires_confirmation' = 'true'::jsonb
        and jsonb_typeof(ai_draft -> 'mock') = 'boolean',
        false
    ))
);

-- position은 AI sources[].id와 같은 전체 대화의 0-based 순서입니다.
create table public.diary_messages (
    id uuid primary key default gen_random_uuid(),
    diary_id uuid not null,
    user_id uuid not null default auth.uid(),
    position integer not null,
    role text not null,
    text text not null,
    created_at timestamptz not null default now(),
    -- 다른 회원의 일기를 내 회원 ID로 연결하지 못하도록 부모 소유자도 검사합니다.
    constraint diary_messages_diary_owner_fk foreign key (diary_id, user_id)
        references public.diaries (id, user_id) on update restrict on delete restrict,
    constraint diary_messages_diary_position_uq unique (diary_id, position),
    constraint diary_messages_position_ck check (position between 0 and 59),
    constraint diary_messages_role_ck check (role in ('child', 'assistant')),
    constraint diary_messages_text_ck check (
        char_length(text) between 1 and 2000 and text ~ '[^[:space:]]'
    )
);

-- 파일 바이트/URL 대신 비공개 Storage의 키와 검증한 메타데이터만 기록합니다.
create table public.diary_assets (
    id uuid primary key default gen_random_uuid(),
    diary_id uuid not null,
    user_id uuid not null default auth.uid(),
    asset_kind text not null,
    scene_index integer not null,
    bucket_id text not null default 'diary-private',
    storage_key text not null,
    mime_type text not null,
    byte_size bigint not null,
    width integer not null,
    height integer not null,
    sha256 text not null,
    created_at timestamptz not null default now(),
    constraint diary_assets_diary_owner_fk foreign key (diary_id, user_id)
        references public.diaries (id, user_id) on update restrict on delete restrict,
    constraint diary_assets_object_uq unique (bucket_id, storage_key),
    constraint diary_assets_scene_uq unique (diary_id, asset_kind, scene_index),
    -- 원본 한 장과 향후 만화 여러 장을 같은 테이블에서 구분합니다.
    constraint diary_assets_kind_scene_ck check (
        (asset_kind = 'original_drawing' and scene_index = 0)
        or (asset_kind = 'comic_scene' and scene_index >= 1)
    ),
    constraint diary_assets_bucket_ck check (bucket_id = 'diary-private'),
    constraint diary_assets_mime_ck check (mime_type in ('image/jpeg', 'image/png')),
    constraint diary_assets_size_ck check (byte_size between 1 and 10485760),
    constraint diary_assets_dimensions_ck check (width > 0 and height > 0),
    constraint diary_assets_sha256_ck check (sha256 ~ '^[0-9a-f]{64}$'),
    -- 키를 회원/일기/종류/파일 ID에 고정하여 타인 파일 참조와 경로 이탈을 막습니다.
    constraint diary_assets_storage_key_ck check (
        storage_key = user_id::text || '/' || diary_id::text || '/'
            || asset_kind || '/' || id::text
            || case mime_type when 'image/png' then '.png' else '.jpg' end
    )
);

-- 날짜/대화순서/장면 조회는 위 고유 인덱스를 재사용하고 회원별 RLS 검색을 보조합니다.
create index diary_messages_user_diary_idx on public.diary_messages (user_id, diary_id);
create index diary_assets_user_diary_idx on public.diary_assets (user_id, diary_id);

alter table public.diaries enable row level security;
alter table public.diary_messages enable row level security;
alter table public.diary_assets enable row level security;

-- 회원 직접 쓰기를 막고 일기·원문·AI 결과는 향후 BE의 검증된 저장 경로로만 추가합니다.
-- BE 저장은 미구현이며 JWT 인증·소유권·내용 검증과 최소 권한 쓰기를 별도로 설계해야 합니다.
-- service_role/소유자는 RLS를 우회하므로 서버 검증을 대신하지 못하며 관리자 키는 서버에만 둡니다.
revoke all privileges on table public.diaries, public.diary_messages, public.diary_assets
    from public, anon, authenticated;
grant select on table public.diaries, public.diary_messages, public.diary_assets
    to authenticated;

-- 회원 ID는 JWT의 auth.uid()와 일치해야 하며 Supabase 익명 로그인도 제외합니다.
create policy diaries_select_own on public.diaries
    for select to authenticated
    using (
        user_id = (select auth.uid())
        and (select auth.jwt() ->> 'is_anonymous') = 'false'
    );

create policy diary_messages_select_own on public.diary_messages
    for select to authenticated
    using (
        user_id = (select auth.uid())
        and (select auth.jwt() ->> 'is_anonymous') = 'false'
    );

create policy diary_assets_select_own on public.diary_assets
    for select to authenticated
    using (
        user_id = (select auth.uid())
        and (select auth.jwt() ->> 'is_anonymous') = 'false'
    );
