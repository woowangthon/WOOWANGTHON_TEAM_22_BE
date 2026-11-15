-- 신규 환경 재현 후보: worry_conversations_v1.sql 및 pg_cron 활성화 후 검토한다.
-- 기존 운영 DB에는 재실행하지 않는다. 이번 제출 작업에서는 운영 SQL을 실행하지 않았다.
-- 기존 일기/4컷/005 및 다른 cron 작업을 변경하지 않는다.
-- 매분 최대 1000세션. 정상 실행 시 만료 후 다음 분에 삭제; 장애/적체 시 지연될 수 있다.
begin;
set local lock_timeout = '5s';
set local statement_timeout = '30s';
do $schedule$
declare
    existing cron.job%rowtype;
    job_command constant text := 'set statement_timeout = ''30s''; select public.purge_expired_worry_sessions(1000);';
begin
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('worry-expiry-minute', 73));
    if to_regprocedure('public.purge_expired_worry_sessions(integer)') is null then
        raise exception 'Apply worry_conversations_v1.sql first';
    end if;
    -- 예전 문서의 hourly 예시나 다른 담당자의 작업을 조용히 덮어쓰지 않는다.
    if exists(select 1 from cron.job where jobname = 'worry-expiry-hourly') then
        raise exception 'Review existing worry-expiry-hourly before registering';
    end if;
    if (select count(*) from cron.job where jobname = 'worry-expiry-minute') > 1 then
        raise exception 'Review duplicate worry-expiry-minute jobs';
    end if;
    select * into existing from cron.job where jobname = 'worry-expiry-minute';
    if found then
        if existing.schedule is distinct from '* * * * *'
           or existing.command is distinct from job_command
           or existing.active is distinct from true
           or existing.database is distinct from current_database()
           or existing.username is distinct from current_user::text then
            raise exception 'Existing worry-expiry-minute differs; review instead of overwriting';
        end if;
    else
        perform cron.schedule('worry-expiry-minute', '* * * * *', job_command);
    end if;
end;
$schedule$;
commit;
