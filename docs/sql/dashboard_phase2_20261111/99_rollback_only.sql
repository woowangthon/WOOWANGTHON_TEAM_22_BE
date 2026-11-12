-- 오류 뒤 같은 연결에 열린 트랜잭션이 남았다면 취소한다. 데이터 복원 SQL이 아니다.
-- Dashboard가 연결을 바꾸면 이전 연결에 닿는다는 보장은 없다. idle timeout도 설정했다.
-- 이미 COMMIT된 앞 파일은 취소되지 않는다. COMMIT 단독 실행/오류 이후 강행 금지.
rollback;
