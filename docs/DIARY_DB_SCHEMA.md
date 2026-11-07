# 회원별 그림일기 DB 초안

2026-11-07 기준 **미적용·팀 검토용 SQL**이다. Supabase PostgreSQL/Auth를 기준으로 하며,
현재 API에 DB·로그인·저장 기능을 연결하지 않는다. 실제 DB 연결·SQL 실행·테이블 생성은 하지 않는다.

## 파일과 확인한 현재 계약

- `supabase/migrations/20261107000100_create_member_diaries.sql`: 테이블 3개, 제약·인덱스·권한·RLS.
- `supabase/migrations/20261107000200_private_diary_storage_policies.sql`: 별도 Storage 접근 정책.
- [Storage 준비·보존·파일 실패 처리](DIARY_STORAGE_POLICY.md)는 두 번째 SQL 적용 전에 함께 검토한다.
- 조사한 BE 기준 커밋: `171cc0a` (`feat/be-bootstrap`). `app/schemas.py`, `api/chat.py`,
  `services/chat.py`, `session_store.py`, `clients/ai.py`와 [공개 계약](CHAT_API_CONTRACT.md)을 확인했다.
  이번에는 원격 fetch/pull을 하지 않았으므로 원격의 추가 변경 유무는 확인하지 않았다.

현재 API는 `POST /api/chat-sessions`, `/{session_id}/messages`, `/{session_id}/diary-draft`다.
첫 assistant부터 성공한 child/assistant 쌍 전체를 메모리에 보존한다. `history_revision`은 성공한
대화 왕복마다 증가하며 일기 저장 버전과 다르다. 초안 요청은 `client_request_id`, `history_revision`만
받으며 사용자·날짜·전체 messages를 FE가 제출할 수 없다. 현재 세션에는 회원 소유권이 없다.

초안 응답은 `{session_id, client_request_id, history_revision, draft}`이고 draft는
`{draft_id,status:"draft",text,pages,sources,fallback,requires_confirmation:true,mock}`다.
`sources[].id`와 `pages[].source_ids`는 전체 messages의 0-based 인덱스다. 메시지 UUID가 아니다.
첫 assistant는 0, 첫 child는 1이다. 현재 `imageUrl`은 null이다.

## 저장 시점과 서비스 흐름

아이가 대화 → 종료 두 번 확인 → 그림 작성 → 초안 생성 → 최종 글 확인 → 저장 순서를 후보로 둔다.
종료 확인 UI와 그림 이후 초안 요청 시점은 FE/BE 후속 작업이다. 현재 초안 API는 그림 여부나
종료 확인을 검사하지 않는다. AI의 `finished=true`도 사용자 최종 확인을 대신하지 않는다.

이 초안의 `diaries`는 **확인된 결과의 스냅샷**이다. 진행 중 대화·종료 확인·그림 작업·실패한 생성은
이 테이블에 미리 저장하지 않는다. 최종 저장 시 검증된 원본 파일을 먼저 비공개 Storage에 올린 뒤,
일기·그 당시 전체 대화·원본 파일 참조를 하나의 DB 트랜잭션으로 추가하는 후속 BE 작업을 전제한다.
대화 중 실시간 영속화가 필요하다면 세션 수명·초안 상태 설계부터 다시 합의해야 한다.

결과창은 `diaries.diary_date/weather/confirmed_text`와 `original_drawing`을 왼쪽에,
추후 `comic_scene`을 `scene_index` 순서로 오른쪽에 조회한다. 원본 대화 다시보기는
`diary_messages order by position`이다. 달력은 회원의 `diary_date` 범위로 목록을 조회한 후
같은 `diary_id`의 상세를 읽는다. 조회 시 AI를 재호출하지 않는다. 조회 API는 아직 없다.

## 테이블·컬럼

모든 `id`는 UUID PK이며 `gen_random_uuid()`가 기본값이다. `created_at`은 `timestamptz`,
기본값 `now()`다. `user_id`는 UUID NOT NULL, 기본값 `auth.uid()`다.
`weather`만 nullable이고 나머지 아래 컬럼은 모두 NOT NULL이다.

### diaries — 회원의 확정 글·날짜와 AI 초안 스냅샷

| 컬럼 | 자료형 | 목적 |
| --- | --- | --- |
| id | uuid | 날짜와 독립적인 일기 식별자 |
| user_id | uuid | `auth.users.id` FK, 회원 소유권 |
| diary_date | date | Asia/Seoul의 일기 날짜, 기본값 서울의 현재 날짜 |
| weather | text, nullable | sunny/cloudy/snowy/rainy, 미입력은 null |
| confirmed_text | text | 사용자가 확인한 글, 공백뿐인 값 거절, 1~20,000자 |
| confirmed_at | timestamptz | BE가 확인 행위를 검증한 뒤 기록할 확인 시각 |
| source_session_id | text | 현재 `ses_`+32자리 hex 형식의 출처 세션 ID |
| source_history_revision | integer | 선택한 초안 생성 시 대화 버전, 1 이상 |
| source_client_request_id | text | 초안 요청 ID, 현재 API와 같은 ASCII 1~128자 |
| ai_draft | jsonb | 선택된 `draft` 객체 전체, 확정 글과 구분해 그대로 보존 |
| created_at | timestamptz | DB 저장 시각 |

`ai_draft.requires_confirmation=true`는 **AI가 반환한 당시의 상태**다. 행의 `confirmed_at`과
모순되지 않는다. 초안 text를 최종 확인 글로 덮어쓰거나, 수정된 최종 글의 근거로 초안 source_ids를
그대로 주장하지 않는다. 원본/초안 모두 개인정보일 수 있으므로 동일하게 회원별로 보호한다.

### diary_messages — 원본 대화 스냅샷

| 컬럼 | 자료형 | 목적 |
| --- | --- | --- |
| id | uuid | 저장 행 식별자, AI source index와 별개 |
| diary_id | uuid | 부모 일기 |
| user_id | uuid | 부모와 일치해야 하는 회원 ID |
| position | integer | 전체 대화 순서 0~59, AI 출처와 연결 |
| role | text | child/assistant 구분, 요약하지 않은 실제 역할 |
| text | text | 원문 보존, 공백뿐인 값 거절, 1~2,000자 |
| created_at | timestamptz | 저장 시각; 과거 실제 발언 시각을 의미하지 않음 |

현재 Session은 각 발언의 원래 ID·시각을 모두 보관하지 않으므로 이를 만들어내지 않는다.
첫 질문부터 **선택한 초안 버전까지**의 연속된 메시지만 그대로 옮겨야 한다. 나중 대화가 섞이면
출처가 틀어진다. 저장할 스냅샷의 회원과 버전을 서버에서 확정해야 한다.

### diary_assets — 원본 그림·향후 만화 파일 참조

| 컬럼 | 자료형 | 목적 |
| --- | --- | --- |
| id | uuid | 파일 식별자, 업로드 전 BE가 미리 생성 가능 |
| diary_id / user_id | uuid / uuid | 같은 회원의 부모 일기에 연결 |
| asset_kind | text | original_drawing 또는 comic_scene |
| scene_index | integer | 원본은 0, 만화는 1 이상 |
| bucket_id | text | diary-private 고정 |
| storage_key | text | 회원/일기/종류/파일 ID로 고정한 비공개 객체 키 |
| mime_type | text | image/jpeg 또는 image/png |
| byte_size | bigint | 1~10,485,760 바이트, 10 MiB 후보 한도 |
| width / height | integer / integer | 양수 픽셀 크기 |
| sha256 | text | 실제 업로드 바이트의 소문자 hex 64자리 해시 |
| created_at | timestamptz | 파일 참조 저장 시각 |

DB에는 파일 바이트·base64·데이터 URL·공개 URL·서명 URL을 저장하지 않는다.
`storage_key`는 `{user_id}/{diary_id}/{asset_kind}/{asset_id}.jpg` 또는 `.png`이며 MIME과 확장자도
일치해야 한다. 원본은 일기당 최대 한 장, 만화는 일기당 여러 장이다. 생성 작업/장면/프롬프트용
별도 테이블은 추가하지 않는다. 만화 재생성 이력이 필요하면 버전 정책부터 정한다.

## 제약·인덱스와 SQL의 보장 범위

| 제약/인덱스 | 목적 |
| --- | --- |
| 테이블별 UUID PK | 행 식별, 직접 ID 조회 |
| diaries_user_fk | 회원 없는 일기 차단, 회원 변경·탈퇴 연쇄 삭제 방지 |
| diaries_user_date_uq | 회원/날짜당 한 권 후보 정책 및 달력 범위 조회 |
| diaries_id_user_uq | 자식의 `(diary_id,user_id)` 복합 FK 참조 대상 |
| diaries_user_session_request_uq | 같은 초안 요청의 중복 저장 차단 |
| 자식의 diary_owner_fk | 내 user_id와 타인의 diary_id를 조합한 참조 차단 |
| diary_messages_diary_position_uq | 중복 순서 차단, 대화 순서 조회 |
| diary_assets_object_uq | 같은 파일 참조 중복 차단 |
| diary_assets_scene_uq | 원본 최대 한 장 및 만화 장면 순서 중복 차단 |
| 자식의 user_diary_idx 2개 | 회원별 RLS 필터와 자식 조회 보조 |
| 각 CHECK | 역할·날씨·종류·순서·텍스트 길이·파일 메타데이터·경로 형태 제한 |

고유 제약은 PostgreSQL의 고유 인덱스를 생성하므로 같은 인덱스를 다시 만들지 않는다.
복합 FK의 부모 일기 인덱스도 이미 있다. 삭제/변경 FK는 모두 RESTRICT다. 파일은 Storage 서비스가
관리하므로 `storage.objects`에 FK를 걸거나 행 삭제를 파일 삭제로 간주하지 않는다.

SQL은 JSON 필수 키/기본 자료형을 검사하지만 페이지/출처의 모든 내부 값까지 검증하지 않는다.
`app/clients/ai.py`의 출처 인덱스·원문·순서·중복·본문 연결 검사와 Pydantic 검증을 재사용해야 한다.
연속 메시지 순서, 아이 발언 존재, 전체 20,000자, 현재 assistant 150자 한도, 버전별 메시지 수,
원본 정확히 한 장 존재, 파일 실존·디코딩·MIME·크기·해시도 저장 서비스가 트랜잭션 전에 검사한다.
FK/CHECK만으로 일기·메시지·그림이 완전한 저장 묶음임을 보장하지 않는다.

초안 요청 고유 제약은 완전한 영속 idempotency 구현이 아니다. 같은 키·다른 최종 글/그림 충돌과
DB 커밋 후 응답 유실 복구를 위해 별도의 저장 요청 키·지문·상태 계약이 필요하다. 다른 초안으로
같은 날짜에 저장하면 날짜 고유 제약에 걸린다. 후속 API에서 구분하여 409로 변환할 것을 제안한다.

## 회원별 RLS와 권한

| 호출자 | SELECT | INSERT | UPDATE/DELETE/TRUNCATE |
| --- | --- | --- | --- |
| anon, 미인증 | 차단 | 차단 | 차단 |
| Supabase 익명 로그인 | 차단 | 차단 | 차단 |
| 일반 회원 A | A의 세 테이블만 | A의 일기/메시지/원본 참조만 | 차단 |
| 일반 회원 B의 ID를 지정한 A | 조회 결과 없음 | 차단 | 차단 |
| service_role/소유자 등 특권 역할 | RLS 우회 가능 | RLS 우회 가능 | 별도 서버/운영 통제 필요 |

기본 권한을 PUBLIC/anon/authenticated에서 회수하고 authenticated에 SELECT/INSERT만 다시 부여한다.
6개 정책은 모두 `user_id = (select auth.uid())`와 JWT `is_anonymous='false'`를 요구한다.
INSERT에도 WITH CHECK를 두므로 FE가 user_id를 타인 값으로 보내면 거절한다.
미인증 `auth.uid()`의 null, 익명 로그인, `is_anonymous` 누락도 허용하지 않는다.
일반 회원의 comic_scene INSERT는 차단하고 향후 검증된 BE 생성 경로로 남긴다.

이 정책은 **소유권 격리와 추가 후 일반 회원 수정 금지**를 보장하는 후보안이다.
본인 영역에 직접 INSERT할 수 있으므로 assistant/AI 초안/확인 시각의 진위나 실제 확인 UI를
RLS로 증명하지 않는다. 진위가 필요한 제품 정책이면 직접 INSERT 권한을 회수하고,
검증된 BE 전용 쓰기/최소 권한 RPC로 전환하는 후속 마이그레이션이 필요하다.
현재 초안에는 SECURITY DEFINER 함수나 인증 우회 RPC를 추가하지 않는다.

BE는 검증한 Supabase JWT에서 회원 ID를 얻고 세션·초안 소유권을 먼저 연결해야 한다.
현재 비인증 세션 ID를 회원에게 사후 연결하는 것만으로는 안전하지 않다.
가능하면 회원 JWT로 DB/Storage를 호출하여 RLS를 유지한다. 서버가 service_role을 쓰면 RLS가
대신 검증해 주지 않으므로 서버의 회원/일기/파일 검사가 필수다. 키는 서버 비밀 설정에만 두며
FE 번들·SQL·문서·로그에 실제 키나 비밀번호를 기록하지 않는다.

## 적용 전 확인 순서와 미정 사항

1. 사용자 모델을 합의한다. 이번 초안은 auth.users의 **회원당** 한 권이며 child_id/가족/보호자 공유는 없다.
   한 회원이 여러 아이를 관리하면 사용자/아이 모델과 고유 제약부터 변경해야 한다.
2. Asia/Seoul 사건 날짜를 서버에서 확정한다. date에는 시간대 정보가 없으므로 기본값만 서울 기준이다.
   명시 입력은 날짜 정책 검증이 필요하다. 자정 경계, 과거·미래·날짜 변경 허용은 미정이다.
3. 하루 한 권, 최종 확인, 재생성/수정/삭제·탈퇴·보존 기간·백업까지 합의한다.
   현재 UPDATE/DELETE 미허용은 임시 보수적 선택이며 영구 보존 합의가 아니다.
   회원 탈퇴는 RESTRICT 때문에 선행 데이터/파일 정리 정책 없이 실행하면 실패한다.
4. Supabase 대상 프로젝트/DB 버전, auth.users·auth.uid/auth.jwt·gen_random_uuid의 존재와 권한,
   예약 이름 충돌, 기본 grants·기존 RLS·노출 스키마·익명 로그인 설정을 확인한다.
   기존 테이블/정책에 덧씌우지 않는다. SQL은 IF NOT EXISTS로 충돌을 숨기지 않으며 각 파일은 트랜잭션이다.
5. 인증·세션 소유권·검증된 원본 업로드·저장 트랜잭션·실패 복구를 구현한다.
   현재 API가 이 스키마를 자동 사용한다고 배포하지 않는다. 원본/최종 글 수정 이력과 과거 초안 전체 보관은 미정이다.
6. 별도 개발 프로젝트에서 가상 회원 A/B와 가상 데이터로 001 → 비공개 버킷 준비 → 002 순서로 검증한다.
   두 SQL 전체가 하나의 원자적 배포는 아니며 002 실패 시 Storage 기능을 열지 않는다.
7. A/B 격리, user_id 위조, 다른 부모 연결, anon/익명 로그인, 중복 날짜/순서/키,
   수정/삭제/덮어쓰기 거절, 파일 실패와 자정 경계, 원본/초안 출처 복원을 검증한 뒤 적용을 승인한다.
   service_role이나 SQL Editor만으로 테스트하면 실제 회원의 RLS를 검증한 것이 아니다.
8. 실제 적용 전 백업·마이그레이션 이력·중단 시 복구 순서를 검토한다. 자동 down/DROP은 준비하지 않는다.
   데이터가 생긴 뒤 롤백은 새 보정 마이그레이션과 파일 보존 계획으로 처리한다.

이번 검증은 PostgreSQL 파서의 문법 검사와 참조·제약·권한의 정적 검토다.
실제 Supabase 카탈로그/역할/정책 조합·RLS 실행 결과·성능·Storage 연동은 미검증이다.

검증 기록(2026-11-07): pglast 8.7로 SQL 2개/26개 문장 구문 분석 통과, AST 기반 정적 검사로
PK 3개·FK 3개의 로컬 참조/자료형/RESTRICT·고유 참조 대상·RLS 대상 3개·정책 12개·문서 링크를
확인했다. 기존 네트워크 없는 모의 회귀 테스트도 BE `.venv`에서 **179 passed in 3.39s**였다.
파서는 BE `.venv/sql-review/`에만 격리해 애플리케이션 의존성/잠금 파일을 변경하지 않았다.
DB 접속·SQL 실행·테이블 생성·버킷 생성·실제 모델 호출은 하지 않았다.

공식 근거: [Supabase RLS와 권한](https://supabase.com/docs/guides/database/postgres/row-level-security),
[Storage 접근 정책](https://supabase.com/docs/guides/storage/security/access-control).
