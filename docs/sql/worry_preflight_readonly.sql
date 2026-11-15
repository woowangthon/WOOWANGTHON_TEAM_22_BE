-- 읽기 전용 확인 후보. 운영에서는 이번에 실행하지 않았다.
select name,default_version,installed_version from pg_available_extensions where name='pg_cron';
select extname,extversion from pg_extension where extname='pg_cron';
select to_regnamespace('cron') as cron_schema, to_regclass('cron.job') as cron_jobs,
       to_regprocedure('cron.schedule(text,text,text)') as named_schedule;
select to_regclass('public.worry_sessions') as sessions,
       to_regprocedure('public.worry_command(uuid,text,uuid,jsonb)') as command;
-- 모두 NULL이면 신규 후보 적용 가능. 일부만 있으면 중단하고 기존 정의를 대조한다.
select to_regclass('public.worry_access_grants') as access_grants,
       to_regclass('public.worry_messages') as messages,
       to_regclass('public.worry_requests') as requests,
       to_regprocedure('public.worry_snapshot(uuid)') as snapshot,
       to_regprocedure('public.purge_expired_worry_sessions(integer)') as purge;
-- v2 증분 패치 이후 auth 직접 SELECT는 필요 없다. 아래는 관찰값이며 false여도 정상.
-- auth.users에 GRANT하지 않는다. 회원 인증은 BE -> /auth/v1/user에서 수행한다.
select has_schema_privilege('service_role','auth','USAGE') as auth_usage,
       has_table_privilege('service_role','auth.users','SELECT') as auth_users_select;
select column_name, data_type from information_schema.columns
where table_schema='auth' and table_name='users'
  and column_name in ('id','is_anonymous','role','deleted_at') order by column_name;
-- 적용 후에는 4개 테이블의 RLS=true, anon/authenticated의 모든 직접 권한=false 확인.
select c.relname, c.relrowsecurity, r.rolname,
       has_table_privilege(r.oid,c.oid,'SELECT') as can_select,
       has_table_privilege(r.oid,c.oid,'INSERT') as can_insert,
       has_table_privilege(r.oid,c.oid,'UPDATE') as can_update,
       has_table_privilege(r.oid,c.oid,'DELETE') as can_delete
from pg_class c join pg_namespace n on n.oid=c.relnamespace cross join pg_roles r
where n.nspname='public' and c.relname in
      ('worry_access_grants','worry_sessions','worry_messages','worry_requests')
  and r.rolname in ('anon','authenticated','service_role') order by c.relname,r.rolname;
-- 함수 3개: anon/authenticated execute=false, service_role=true, security definer=false.
select p.proname, p.prosecdef, r.rolname, has_function_privilege(r.oid,p.oid,'EXECUTE') as can_execute
from pg_proc p join pg_namespace n on n.oid=p.pronamespace cross join pg_roles r
where n.nspname='public' and p.proname in
      ('worry_snapshot','worry_command','purge_expired_worry_sessions')
  and r.rolname in ('anon','authenticated','service_role') order by p.proname,r.rolname;
-- cron.job가 있고 관리 권한이 확인된 경우에만 별도로 실행:
-- select jobid,jobname,schedule,active from cron.job where jobname='worry-expiry-hourly';
-- select jobid,jobname,schedule,active,database,username,command from cron.job
-- where jobname like 'worry-%' or command ilike '%purge_expired_worry_sessions%';
-- 신규 SQL 적용 후 데이터 원문 없이 정리 적체 확인:
-- select count(*) as expired_count,min(expires_at) as oldest_expiry
-- from public.worry_sessions where expires_at<=clock_timestamp();
