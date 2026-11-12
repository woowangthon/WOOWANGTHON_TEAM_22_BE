2026-11-12 ?? ??: ../../BE_MAIN_REVIEW_20261112.txt ??.
??? ?? ?? ????? ????. room/comic/v0.4/jobs ?? ?? ? Render c75a9bc ?? ???
?? ??? ???? ??? ????? ???. ?? ??? SQL? ????? ???.
runtime factory? ??? worker ???? OpenAPI 202 ??? ????. ?? app? worker ?????.
FE main? ?? API ??? ????? API? proposal ??? ????. 005? ??? ???.

최신 안내 — v0.4 날짜 제약 교체 한 건을 명시 허용한 실행용 복사본 준비 완료.
실행 순서/백업 전제/검증 결과는 같은 폴더 README_APPLY_V04.txt를 따른다.
01_apply_room → 02_apply_comic → 02b_preflight_v04(조회) → 03_apply_v04
→ 04_apply_comic_jobs → 06_verify_all(조회). migration005 제외. 실제 DB 미실행.
03_v04_BLOCKED.sql과 아래 이전 안내는 당시 차단 근거로 보존한다.
아래 '전체 차단/03 실행 금지'는 과거 BLOCKED 파일에 관한 설명이다.

----- 이전 준비 안내 원문 -----
기존 today-and-near에 적용할 최소 준비 — 2026-11-11 Asia/Seoul

판정: 운영 접속/조회/실행 0. 로컬 준비본이며 PostgreSQL 실행 성공이 아니다.
전체 2차 적용은 현재 차단: v0.4의 날짜 제약 교체가 이번 DROP 금지 조건과 충돌한다.
01/02는 트랜잭션·보존 검사·권한 보완을 포함한 조건부 적용 준비본이다.
원본 SQL의 무결함/운영 실행 가능 판정을 하지 않는다. 03은 의도적으로 즉시 실패한다.
Q01~Q15는 사용자 완료 보고를 사용하며 반복하지 않는다. 001~004 재실행도 하지 않는다.

사용자가 직접 할 최소 순서

1. 기존 프로젝트 today-and-near의 Dashboard → SQL Editor를 연다.
   Role은 postgres를 선택한다. 다른 역할로 00을 실행하면 결과가 0행이며 백업 성공이 아니다.
   백업 시작부터 적용/검증 종료까지 일기 저장·수정·삭제·업로드·보상·구매 등 쓰기를 멈춘다.
   협업자도 같은 시간에 실행/쓰기하지 않도록 한다. 새 프로젝트/배포/설치는 필요 없다.
2. 00_export_diary_data.sql 전체를 붙여 한 번 실행한다.
   결과의 backup_json 한 행을 Export/Download CSV로 로컬 비공개 폴더에 저장한다.
   JSON 내보내기가 제공되는 UI라면 JSON도 가능하다. 화면 셀의 축약 문자열 복사는 피한다.
   CSV는 CSV 파서로 backup_json 필드를 읽고 JSON으로 해석해야 한다(따옴표를 손으로 바꾸지 않는다).
   export_version=1, complete=true, data의 세 배열 길이와 counts 일치를 확인한다.
   파일 저장 실패·잘림·JSON 해석 실패·크기 제한이면 백업 실패로 보고 다음 단계로 가지 않는다.
   Storage → diary-private에서 기존 그림을 Download하여 같은 비공개 보관 장소에 둔다.
   diary_assets의 bucket_id/storage_key와 대응되도록 회원/일기/종류/파일명 경로를 보존한다.
   최소한 ready 그림 전부를 받으며, 가능하면 버킷 전체를 보관한다. 파일을 열고 크기/해시를 확인한다.
   파일 수가 많아 수동 보존이 어렵다면 이 절차로 백업 완료라 판단하지 않는다.
3. 부분 준비(room/comic)까지만 진행한다면 01_apply_room.sql 전체를 한 번 실행한다.
   성공 후 02_apply_comic.sql 전체를 한 번 실행한다. 문장 선택 실행/파일 합치기는 금지한다.
   어느 파일이든 오류/timeout이면 즉시 중단한다. 뒤 파일이나 COMMIT만 실행하지 않는다.
   열린 트랜잭션 정리가 필요하면 99_rollback_only.sql 전체만 실행한다.
4. 04_verify.sql 전체를 한 번 실행한다. room/comic 12개 테이블의 installed/rls/
   clients_cannot_write/server_select가 true이고 base/room/comic 11개 함수의 installed/
   security_invoker/server_only가 true인지 확인한다. diary_lock_column_grant=true도 확인한다.
   legacy 보류표와 comic_attach_requests의 policy_count=0은 서버 전용이므로 정상이다.
   나머지 신규 테이블의 policy_count는 1이 예상된다.
   현재 v04 테이블 4개·member_diary_edit는 installed=false, v04_policies=0,
   date_unique_constraint_present=true가 예상값이다. 이를 2차 전체 성공으로 해석하지 않는다.
5. 03_v04_BLOCKED.sql은 실행하지 않는다. 전체 2차 기능이 필요하면 01/02도 미루고
   아래 차단 원인이 해결된 새 완성본을 기다리는 것이 가장 짧다. 가드를 손으로 지우지 않는다.
   현재 v0.4 열을 요구하는 BE를 먼저 연결하면 조회/저장이 실패할 수 있다.
   부분 적용 후에는 기존 1차 서비스 범위만 재개한다. 2차 기능 활성화/배포는 이번 범위가 아니다.

데이터 보존 범위와 복원 한계

- 00은 단일 SELECT라 세 테이블을 같은 SQL 문장 스냅샷으로 읽는다. 쓰기·RPC 호출이 없다.
  postgres 역할만 결과를 반환하여 회원 RLS로 일부만 나온 데이터를 전체 백업으로 오인하지 않는다.
  모든 열을 JSON으로 보존하므로 id/user_id/diary_id, 메시지 순서, 초안/본문,
  confirmation_fingerprint 및 그림 bucket_id/storage_key/sha256/상태가 함께 남는다.
  배열이 빈 경우도 []와 count=0으로 명시한다. 한 행 포장은 UI 행 제한을 줄이지만
  응답 바이트/셀 크기 제한까지 없애지는 않는다. SQL Editor가 전체를 내보내는지 확인해야 한다.
- 이 파일은 세 테이블의 데이터 사본이다. Auth 계정·암호·identities·세션, 전체 DB 스키마,
  기존 함수/RLS/권한, 다른 테이블, 새 room/comic/v04 데이터의 복원 백업은 아니다.
  user_id를 보존해도 삭제된 auth.users 계정의 로그인 복구는 불가능하다.
  기존 계정/프로젝트/버킷을 삭제하거나 초기화하지 않는다.
- 같은 스키마와 같은 auth.users UUID가 남아 있을 때, 충돌 여부 확인 후 원래 UUID로
  diaries → diary_messages/diary_assets 순서로 복원하는 재료가 된다.
  CSV 자체를 Table Editor에 바로 넣는 형식은 아니다. JSON→각 테이블 레코드 변환과
  FK/고유키 검증이 필요한 별도 복원 SQL이 필요하며 이번에는 복원을 실행/검증하지 않았다.
  v0.4 적용 후라면 추가 열/참조/영수증도 고려해야 한다. 이 사본만으로 전체 되돌리기는 불가하다.
- diary-private 파일 바이트는 DB 내보내기에 포함되지 않는다. storage.objects 메타데이터나
  만료되는 signed URL만 저장해도 그림 복구는 되지 않는다. 파일과 원래 object key를 보관한다.
  기존 파일을 삭제/덮어쓰기/이동/버킷 공개 전환하지 않는다. 기존 참조가 있으면 그대로 유지한다.
- Dashboard → Database → Backups에 이미 사용 가능한 백업이 있다면 복원 시점/범위도 확인한다.
  이 옵션의 실제 제공 여부는 여기서 조회하지 않았다. 새 유료 기능/CLI/인프라를 요구하지 않는다.
  복원 가능한 백업이 없으면 파괴적 변경을 강행하지 않는다. 00은 완전 복원 보증이 아니다.
- 실제 내보낸 개인정보/행/파일/키를 이 저장소, 채팅, 보고서에 첨부하지 않는다.

검토한 의존관계와 RPC 영향

- 기반: 기존 001~004의 diaries(id,user_id) 복합 UNIQUE/FK, confirmation_fingerprint,
  diary_assets.upload_status/updated_at, Auth 4열(id/is_anonymous/role/deleted_at),
  auth.uid/auth.jwt, service_role 및 private Storage/002 정책.
- room: 새 테이블 9개, 함수 4개, 트리거 3개, SELECT 정책 8개.
  기존 일기/RPC를 교체하지 않고 기존 일기 ID/소유자를 legacy 보류표에 복사한다.
  과거 일기에 보상을 지급하지 않으며 상품 seed도 없다.
- comic: 새 테이블 3개, 함수 4개, SELECT 정책 2개. room에는 의존하지 않는다.
  service_role UPDATE(id) on diaries는 attach의 FOR SHARE 행 잠금에 필요하다.
  comic_panels_valid는 CHECK에서 호출되므로 service_role EXECUTE도 필요하다.
- v0.4: room+comic 모두 필요. 새 테이블 4개, 새 함수 1개와 기존 함수 교체 6개:
  confirm_member_diary(날짜 활성 조건/updated_at), reserve_diary_drawing 및
  complete_diary_drawing(삭제/수정 버전 검사), room_snapshot(환불 표시),
  member_room_command(날짜별 보상/영수증/환불), attach_diary_comic(삭제/버전 검사).
  로컬 원본의 인자 이름/타입/반환형은 선행 정의와 비교한다. 실 DB의 정의는 미조회다.
  CREATE OR REPLACE는 기존 ACL/소유자를 보존하므로 잘못된 운영 ACL을 자동 수리하지 않는다.
  새 member_diary_edit만 별도 REVOKE/GRANT가 있다. 실행 시 기존 ACL 검사도 필요하다.
- 새 함수는 SECURITY INVOKER, search_path=''이며 명시적 public/auth 참조와 pg_catalog 함수를 쓴다.
  회원 직접 쓰기/서버 RPC는 차단하고 조회는 소유자+비익명 정책을 쓴다.
  v0.4의 추가 restrictive 정책은 논리삭제 일기/대화/그림/연결 만화를 숨긴다.
  이미 발급된 URL/미연결 preview 접근은 완전 철회되지 않는다.
- 확정 순서: 기존 001~004(이미 확인) → room → comic → v0.4(현재 차단).
  005는 어느 파일에도 의존하지 않는다. SESSION_STORE_MODE=memory 유지, 설정 파일 변경 없음.

정확한 차단 원인과 수정안

R1 — v0.4 원본 15~16행:
  기존 diaries_user_date_uq UNIQUE 제약을 제거하고 deleted_at IS NULL 부분 고유 인덱스로 바꾼다.
  행 삭제는 아니지만 DROP CONSTRAINT이므로 이번 명시적 DROP 금지 조건과 충돌한다.
  DROP만 생략하면 같은 이름 인덱스 생성도 충돌한다. 두 문장을 생략해도 원래 UNIQUE가 남아
  논리삭제 후 같은 날짜 새 일기가 409로 막힌다. 이를 기능 완성으로 표시할 수 없다.
  수정안: 복원 가능한 백업을 확보한 뒤 제약 교체만 허용하는 별도 범위를 정해 재검토하거나,
  같은 날짜 재작성 불가로 계약을 바꾸고 관련 RPC/BE/FE 검증을 다시 한다. 이번에는 둘 다 안 한다.
  03 복사본은 두 문장을 실행문에서 제외하고 첫 DDL 전 무조건 예외를 발생시킨다.
  따라서 문법 검사를 통과해도 적용 가능한 v0.4가 아니다. 가드를 제거하는 수동 지시는 없다.

R2 — room 원본 126~130행 부근의 테이블 REVOKE 대상:
  service_role이 빠져 있어 생성자 default privileges에서 받은 과도한 권한이 남을 수 있다.
  SELECT/INSERT/UPDATE GRANT를 써도 기존 DELETE/TRUNCATE 등은 회수되지 않는다.
  원본의 최소 권한 충족은 운영 기본 ACL에 의존하므로 무조건 실행 가능이라 판정하지 않는다.
  수정안은 01 복사본의 해당 REVOKE에 service_role을 포함하고 기존 GRANT를 그대로 적용하는 것.
  신규 테이블만 대상으로 하며 기존 일기/Auth/Storage ACL은 이 보완으로 변경하지 않는다.
  역할 상속 등 간접 권한은 별도이며 실제 운영 권한 성공은 미검증이다.

기타 실패 조건:
  세 후보 원본 모두 BEGIN/COMMIT이 없어 Dashboard 조각 실행 시 부분 확정 위험이 있다.
  복사본은 각각 단일 명시적 트랜잭션이다. 01/02에 원본 데이터 전체열/ID/행수 해시 비교와
  신규 RLS/회원 쓰기/RPC 권한 검사를 COMMIT 전에 넣었고 실패를 삼키지 않는다.
  v0.4 날짜별 보상 UNIQUE는 기존 중복 지급일이 있으면 실패한다. 데이터 삭제/잔액 보정으로
  억지 적용하지 않는다. 기존 구매 영수증 이관도 FK/고유키 불일치 시 전체 취소해야 한다.
  후보는 재실행용이 아니다. 객체 존재 충돌 때 DROP/IF NOT EXISTS로 우회하지 않는다.

잠금/오류/검증의 의미

- 01/02는 백업 비교가 흔들리지 않도록 세 기존 테이블 SHARE 잠금을 유지한다.
  일반 SELECT는 가능하지만 쓰기는 대기한다. 원본 room에도 diaries SHARE 잠금이 있다.
- 원본 v0.4는 diaries/member_daily_reward_grants/member_owned_items에 ACCESS EXCLUSIVE.
  읽기와 쓰기를 모두 막으며 다른 ALTER/인덱스/FK 작업도 잠금을 획득한다.
  향후 차단 해제 후 적용하더라도 일기·방 등 서비스의 일시 중단이 필요하다.
- lock_timeout=5s는 각 잠금 대기, statement_timeout=60s는 문장 실행 제한이며 전체 작업 완료
  시간 보장은 아니다. idle_in_transaction_session_timeout=60s는 열린 유휴 트랜잭션을 끊는다.
  timeout도 오류이므로 해당 파일의 트랜잭션은 확정되지 않는다.
- SQL Editor에서 psql 전용 ON_ERROR_STOP 명령을 사용하지 않는다. DB 오류가 트랜잭션을
  aborted 상태로 만들도록 하고 중간 COMMIT/SAVEPOINT 복구/예외 무시가 없는 전체 파일을 쓴다.
  저장하는 RPC 함수 내부의 예외 처리는 함수 정의문 설치 실패를 삼키는 것이 아니다.
- 02 실패가 이미 성공한 01까지 취소하지는 않는다. 99는 미확정 트랜잭션 취소용이며
  성공한 COMMIT을 되돌리는 백업 복원 도구가 아니다. 통신 결과 불명이면 재실행하지 말고
  04의 객체 상태를 확인한다. 일부 객체/예상외 권한이면 중단하고 실제 오류만 확인한다.
- 로컬 검사 결과: 기존 BE .venv + 기존 pglast 8.7로 기반 SQL 4개, 후보 원본 3개,
  새 SQL 6개(총 13개) 및 SQL/PLpgSQL 함수·DO 본문 구문 통과.
  v0.4 교체 함수 6개의 인자 이름/타입/반환형 보존 확인. 새 SQL의 실행문에는
  DROP/DELETE/TRUNCATE가 없고 적용 복사본별 BEGIN/COMMIT 각 1개를 확인했다.
  함수 본문에 정의된 환불/배치 해제 DELETE는 해당 RPC 호출 시의 동작이며 설치 시 실행하지 않는다.
- 로컬 parser는 SQL/PLpgSQL 문법만 검사한다. 실제 카탈로그 바인딩, 역할 상속, RLS 행 격리,
  트리거 동작, 동시 호출, 잠금/실패 롤백, Dashboard 응답·내보내기는 실행 검증하지 않았다.
  기존 1035개 테스트/Q01~Q15를 이번 검증으로 다시 세지 않는다.

근거(공식 문서; 운영 프로젝트에는 접속하지 않음)
- DB 백업은 Storage 파일을 포함하지 않음:
  https://supabase.com/docs/guides/platform/backups
- CREATE OR REPLACE FUNCTION의 소유자/권한 유지:
  https://www.postgresql.org/docs/current/sql-createfunction.html
- 잠금/SQL/유휴 트랜잭션 timeout의 범위:
  https://www.postgresql.org/docs/current/runtime-config-client.html
- 트랜잭션 취소:
  https://www.postgresql.org/docs/16/sql-rollback.html
