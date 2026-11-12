2026-11-12 ?? ??: ../../BE_MAIN_REVIEW_20261112.txt ??.
??? ?? ?? ????? ????. room/comic/v0.4/jobs ?? ?? ? Render c75a9bc ?? ???
?? ??? ???? ??? ????? ???. ?? ??? SQL? ????? ???.
runtime factory? ??? worker ???? OpenAPI 202 ??? ????. ?? app? worker ?????.
FE main? ?? API ??? ????? API? proposal ??? ????. 005? ??? ???.

오늘,그리고 — v0.4 차단 최소 해결 / 전체 적용 준비
2026-11-11 Asia/Seoul. 운영 DB 접속·조회·실행0. 실제 PostgreSQL 미검증.

결론: DROP 금지로 인한 로컬 준비 차단을 해제했다.
허용한 것은 아래 한 건뿐이며 다른 DROP/CASCADE/행 삭제는 허용하지 않는다.
  ALTER TABLE public.diaries DROP CONSTRAINT diaries_user_date_uq RESTRICT;
같은 트랜잭션에서 같은 이름의 UNIQUE INDEX(user_id,diary_date)
WHERE deleted_at IS NULL을 만든다. 기존 일기 자체를 삭제하는 명령이 아니다.
03_v04_BLOCKED.sql·기존01/02/04_verify·원본 후보·migration001~005는 보존한다.
기존 README의 '전체 차단/03 실행 금지'는 과거 BLOCKED 파일에 대한 기록이다.
현재 실행 안내는 이 문서와 새03_apply_v04.sql/04_apply_comic_jobs.sql/06_verify_all.sql이다.

1. 사용자가 Dashboard에서 할 순서

대상은 기존 today-and-near 프로젝트, SQL Editor의 postgres 역할이다.
DB 적용과 BE/FE 활성화·배포는 별개다. 이번 작업은 로컬 준비만 완료했다.

1) 유지보수 시간에 앱·worker·협업자의 쓰기를 모두 멈춘다. 기존001~004 적용 상태와
   이미 설치된 room/comic/v04/jobs가 있는지 확인한다. 이 파일들은 최초 설치용이다.
   이전 성공 파일을 재실행하지 않는다. 일부 설치/결과 불명/예상외 객체가 있으면 중단한다.
2) 기존00_export_diary_data.sql 전체 실행 → backup_json 한 행을 로컬 비공개 파일로
   Export한다. CSV 파서→JSON으로 해석해 complete=true/export_version=1,
   diaries/diary_messages/diary_assets 배열 길이와 counts를 확인한다.
   화면의 축약 셀 복사만으로 백업 완료로 보지 않는다. 그림 원본도 diary-private에서
   object key를 유지해 다운로드하고 파일/크기/해시를 확인한다.
   기존 DB 스키마·함수·권한·정책과 Auth/room/comic 등 이미 있는 데이터까지 복구 가능한
   백업의 범위/시점/저장 완료를 확인한다. DB 백업만으로 Storage 바이트가 보존되지는 않는다.
   세 테이블 내보내기는 전체 DB/Auth/Storage 복원본이 아니다. 전체 복원이 보장된다고
   오인하지 않는다. 필요한 데이터 내보내기/복구 가능한 백업을 확인하지 못하면03을 실행하지 않는다.
3) 01_apply_room.sql 전체 실행 → 성공한 뒤02_apply_comic.sql 전체 실행.
   이미 이 두 실행본을 성공적으로 적용했다는 증거가 있다면 재실행하지 않고
   기존04_verify.sql의 room/comic 결과와 실제 스키마를 확인한다.
4) 02b_preflight_v04.sql 전체 실행. postgres_role/expected_original=true,
   dependency_conflict=false, duplicate_diary_groups/duplicate_reward_groups=0 확인.
   original_data_fingerprint 결과를 비교용으로 보관한다(개인정보 행은 출력하지 않는다).
   이 조회는 예비 검사다.03 내부에서 잠금을 잡고 동일한 핵심 조건을 다시 검사한다.
5) 03_apply_v04.sql 전체 실행 → 성공한 뒤04_apply_comic_jobs.sql 전체 실행.
6) 06_verify_all.sql 전체 실행. 아래 예상 결과와 데이터 fingerprint를 확인한다.

각 파일을 하나씩 BEGIN부터COMMIT까지 전체 실행한다. 파일 합치기/문장 선택 실행 금지.
오류/잠금timeout/중복/의존성 충돌이면 즉시 중단한다. 다음 파일 또는COMMIT만 실행하지 않는다.
열린 트랜잭션 정리가 필요하면99_rollback_only.sql 전체 실행. 이는 이미COMMIT된 파일의
변경을 취소하는 도구가 아니다.04_jobs 실패가 앞서 성공한03을 되돌리지는 않는다.
응답 유실이면 같은 설치본을 재실행하지 말고 실제 객체/오류/트랜잭션 상태부터 확인한다.
CASCADE/DROP IF EXISTS/중복 행 삭제/잔액 보정/가드 제거로 우회하지 않는다.

정확한 설치 순서:
기존001~004(재실행 안 함) → 01 room → 02 comic → 03_apply_v04 → 04_apply_comic_jobs.
migration005(20261109000500_persist_chat_sessions.sql)는 적용하지 않는다.
새04 파일 번호는 Dashboard 작업 순서이며 migration005와 관계없다.
supabase db push/migration up/reset은 사용하지 않는다. SESSION_STORE_MODE=memory 유지.

2. 교체 전후 규칙과 중단 조건

001의 정확한 대상: public.diaries.diaries_user_date_uq UNIQUE(user_id,diary_date).
이 제약의 내부 지원 인덱스도 같은 이름이다. DROP은 제약과 그 내부 인덱스를 제거한다.
두 번째 DROP INDEX를 실행하지 않는다. ID PK/diaries_id_user_uq/요청 멱등 UNIQUE는 보존한다.
로컬 FK는 날짜가 아니라diaries(id,user_id)를 참조한다.005의 FK도 이 키를 참조하지만
005는 이번 적용/검증 목록에서 제외한다. 운영 카탈로그가 같다고 가정하지 않는다.

03/04는 기존diary-private 버킷이 비공개인지도 검사한다. 버킷 설정은 바꾸지 않는다.
03은 기존 날짜 제약의 열 순서/UNIQUE/즉시검사/유효 상태, 동일 인덱스 정의,
NOT NULL 키·일반 테이블·추가 날짜 UNIQUE·예상외 trigger/rule을 먼저 확인한다.
pg_depend에서 내부 지원 인덱스 한 건 이외의 종속 객체, 날짜 인덱스를 참조하는 FK,
확장/파티션 의존성이 있으면 중단한다. RESTRICT도 의존 객체 삭제를 차단한다.
이미 v04 열/테이블이 있거나 교체 RPC 인자·반환형·ACL이 다르면 중단한다.
중복 일기 날짜 또는 중복 보상(member_id,diary_date)이 있어도 중단한다.

교체 전: 삭제 여부와 관계없이 회원/날짜당 일기1개.
교체 후: deleted_at IS NULL인 활성 일기만 회원/날짜당1개.
활성+활성은 거절, 논리삭제+활성 또는 논리삭제+논리삭제는 허용하는 규칙이다.
보상은 deleted_at과 무관하게 회원/날짜 UNIQUE를 유지해 재작성으로 이중 지급하지 않는다.
confirm_member_diary는 기존 날짜 충돌 이름 diaries_user_date_uq를 그대로 처리한다.
ON CONFLICT ON CONSTRAINT diaries_user_date_uq 호출은 로컬 코드/후보에 없다.
날짜 부분 인덱스를 FK 참조 대상이나 일반 UNIQUE CONSTRAINT로 취급하면 안 된다.

03은ACCESS EXCLUSIVE 잠금을 유지하면서 교체하므로 다른 세션이 UNIQUE 공백에 쓸 수 없다.
COMMIT 전 부분 UNIQUE의 정확한 열/조건/유효상태와 활성 날짜 중복0을 다시 검사한다.
기존 일기15개 관련 테이블의 행수/전체 기존 열 해시, 일기FK와 나머지 인덱스를 비교한다.
일기에 새로 생긴4열, 기존 보유의purchase_id, 연결 만화source_edit_version만 비교에서 제외한다.
새 일기 열은edit_version=1, updated_at=confirmed_at, deleted_at/active_drawing_edit_id=null도 확인한다.
기존 ID/본문/ai_draft/원본 대화/그림 참조·메타데이터/만화 패널은 바뀌면 롤백한다.
새 updated_at/purchase_id 이관은 원본 후보의 필요한 추가 메타데이터 작업이다.
원래 회원 데이터의 본문/그림을 수정하거나 삭제하는 RPC는 설치 중 호출하지 않는다.
함수 본문에 정의된 향후 환불/배치 해제 DELETE는 설치 시 실행되지 않는다.

잠금 대기5초, 문장60초, 유휴 트랜잭션60초. 전체 완료 시간 보장이 아니다.
큰 데이터의 해시/인덱스 생성이 시간 제한을 넘으면 전체 파일을 취소하고 재검토한다.
03/04 각각 단일 트랜잭션으로, 설치 오류를 예외 처리로 삼키거나 중간COMMIT하지 않는다.
이 동작을 실제 PostgreSQL/SQL Editor에서 실행 검증한 것은 아니다.

3. comic_jobs 의존성·권한·Storage

직접 의존: auth.users의 회원 검증 열, 기존comic_panels_valid(jsonb),
comic_previews의 결과 열, save_comic_preview_result/complete_comic_preview_asset.
room/v04/005 테이블을 직접 참조하지는 않는다. 하지만 현재 BE의 일기 상세/첨부가
v04 열을 요구하므로 전체 적용 순서는v04 다음jobs로 고정했다.
새 테이블1·인덱스1·함수2를 추가하며 기존comic 객체와 이름이 겹치지 않는다.
04 실행본은 후보의 CREATE/GRANT/RPC 본문을 그대로 사용하고 사전/사후 검사만 덧붙인다.
기존 객체가 있으면 실패하며 덮어쓰기/IF NOT EXISTS를 사용하지 않는다.
RLS 켜짐, 회원/anon의 원장 읽기·쓰기 금지,service_role SELECT/INSERT 및
state/claim_token/lease_until/progress/result_saved 열 UPDATE만 허용하는지 검사한다.
두 함수는SECURITY INVOKER, search_path='', service_role EXECUTE 전용이다.
기존 데이터를 해시 비교하고 원장 정책0개/회원열읽기불가/서버DELETE·TRUNCATE불가도 확인한다.

Storage 경로: diary-private/{회원UUID}/{previewUUID}/comic_scene/{assetUUID}.png.
원본 그림의original_drawing과 구분되며 기존002의UUID/UUID/comic_scene 규칙에 맞는다.
새 버킷/정책/업로드 SQL/경로 이동 없음. v04의 Storage restrictive SELECT는
original_drawing의 논리삭제를 대상으로 하므로comic_scene을 새로 차단하지 않는다.
이는 코드/SQL 대조 결과이며 실제 Storage 정책 상호작용·권한/서명 성공은 미검증이다.

4. 06_verify_all.sql 예상 결과

- postgres_role/storage_bucket_private=true.
- tables17개 모두 installed/rls/clients_cannot_write/server_select=true.
- functions14개 모두 installed/security_invoker/server_only=true, settings에search_path="".
- policy_count: room의legacy 보류표0/나머지8개1;
  comic_previews1, diary_comics2, comic_attach_requests0;
  diary_edit_requests1, diary_delete_requests0, shop_purchase_receipts0, shop_refund_receipts0;
  comic_generation_jobs0.
- diary_lock_column_grant=true, date_unique_constraint_present=false,
  partial_date_unique_ok=true, live_duplicate_groups/reward_duplicate_groups=0,
  job_clients_cannot_read/job_server_cannot_delete=true, v04_policies=6.
- original_data_fingerprint가02b 결과와 같아야 한다(쓰기 중단 유지).
  새 메타데이터 열을 제외해 기존 데이터만 비교한다. 새 열 포함 전체행 해시와 혼동하지 않는다.

이 결과는 설치 메타데이터/데이터 보존 확인이다. 실제 회원RLS 격리, service_role RPC,
다중 연결 잠금·claim/lease, 실패 롤백, Storage 업로드·서명·재시작 복구는 별도 실증이 필요하다.
운영 데이터로 중복쓰기/삭제/환불 실험을 하지 않는다. 백업/권한 확인 후 승인된 검증 데이터와
기존 프로젝트의 적절한 검증 절차로 남은 검증을 진행해야 한다. 새 프로젝트를 요구하지 않는다.
DB 준비만으로 운영 생성503을 해제하거나 FE/AI/Render 배포하지 않는다.

5. 로컬 검증 및 공유

BE 기존.venv + 기존pglast8.7 사용, 새 설치 없음.
SQL18파일(001~004, 후보4, Dashboard10) 및SQL/PLpgSQL 함수·DO 본문 구문 검사.
새03은61문, 새04_jobs는28문. 교체RPC6개의 인자명/타입/반환형 보존 대조.
실행본의DROP allowlist는03의위 한 건뿐이다. 후보v04 본문은RESTRICT 명시 외 그대로,
jobs 후보 본문은 그대로다. 기존01/02/차단03/원본001~005/후보는 해시 보존한다.
기존 관련회귀321개 통과(25.80초, 실패/오류/skip0). 전체1065개는 직전 결과이며 재실행하지 않았다.
테스트에서 실제 환경파일/네트워크 접근 시도0, Fake/MockTransport 기반으로 실DB 검증이 아니다.
검증 근거: ../../../../local-tools/be-v04-unblock-20261111/sql-review.json 및 focused.xml.
공통 API 추가안: ../../COMIC_API_ADDENDUM_PROPOSAL_20261111.txt.
POST comic-plans/GET comic-requests와 기존comic-previews의202 흐름은 FE 합의 전 제안이다.
기존 원문·장면 확인 완료에 자동 연결할 수 있으며 별도 생성 버튼을 요구하지 않는다.

카탈로그 검사 근거(공식 문서):
https://www.postgresql.org/docs/current/catalog-pg-constraint.html
https://www.postgresql.org/docs/current/catalog-pg-depend.html
https://www.postgresql.org/docs/current/catalog-pg-index.html
https://www.postgresql.org/docs/current/sql-altertable.html
과거백업안내: README.txt의 데이터 보존 범위와 복원 한계 참조.
