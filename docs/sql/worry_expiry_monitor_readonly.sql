-- pg_cron/고민 SQL 적용 후 관리자 읽기 전용 점검. 대화 원문/회원 ID 없음.
begin transaction read only;
-- 다른 이름의 같은 purge도 조회한다. 기존 일정은 재등록/수정하지 않는다.
select jobid, jobname, schedule, active, database, username, command
from cron.job where command ilike '%purge_expired_worry_sessions%';
select count(*) as purge_jobs, count(*) = 1 as no_duplicate_job
from cron.job where command ilike '%purge_expired_worry_sessions%';
select jobid, jobname, schedule, active, database, username
from cron.job where jobname in ('worry-expiry-minute', 'worry-expiry-hourly');

select j.jobid, j.active, r.status, r.start_time, r.end_time,
       case when not j.active then 'INACTIVE'
            when r.jobid is null then 'NO_RUN'
            when r.status = 'failed' then 'FAILED'
            when r.start_time < now() - interval '3 minutes' then 'STALE'
            when r.status not in ('succeeded','running','starting') then 'CHECK'
            else 'OK' end as health
from cron.job j
left join lateral (
    select jobid, status, start_time, end_time from cron.job_run_details
    where jobid=j.jobid order by runid desc limit 1
) r on true
where j.jobname='worry-expiry-minute';

select r.runid, r.status, r.return_message, r.start_time, r.end_time
from cron.job_run_details r join cron.job j on j.jobid=r.jobid
where j.jobname='worry-expiry-minute' order by r.runid desc limit 20;

select count(*) as expired_sessions, min(expires_at) as oldest_expiry,
       count(*) filter (where expires_at < now() - interval '3 minutes') as overdue_sessions
from public.worry_sessions where expires_at <= clock_timestamp();
-- 작업 0행/NO_RUN/FAILED/STALE/INACTIVE 또는 overdue_sessions>0이면 조사한다.
-- 성공 로그만으로 삭제 완료를 판단하지 않는다. SKIP LOCKED/한도 때문에 적체 가능.
commit;
