-- 읽기 전용 확인 후보. 운영에서는 이번에 실행하지 않았다.
select name,default_version,installed_version from pg_available_extensions where name='pg_cron';
select extname,extversion from pg_extension where extname='pg_cron';
select to_regnamespace('cron') as cron_schema, to_regclass('cron.job') as cron_jobs,
       to_regprocedure('cron.schedule(text,text,text)') as named_schedule;
select to_regclass('public.worry_sessions') as sessions,
       to_regprocedure('public.worry_command(uuid,text,uuid,jsonb)') as command;
-- cron.job가 있고 관리 권한이 확인된 경우에만 별도로 실행:
-- select jobid,jobname,schedule,active from cron.job where jobname='worry-expiry-hourly';
-- 신규 SQL 적용 후 데이터 원문 없이 정리 적체 확인:
-- select count(*) as expired_count,min(expires_at) as oldest_expiry
-- from public.worry_sessions where expires_at<=clock_timestamp();
