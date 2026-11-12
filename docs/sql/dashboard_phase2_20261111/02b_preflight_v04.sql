-- READ ONLY. 01/02 완료 후 03 직전. 행/본문/회원 ID를 출력하지 않는다.
-- expected_original=true, dependency_conflict=false, duplicate_*_groups=0 필요.
select jsonb_build_object(
 'postgres_role',current_user='postgres',
 'expected_original',exists (
    select 1 from pg_constraint c
    join pg_index i on i.indexrelid=c.conindid
    join pg_class ix on ix.oid=i.indexrelid
    where c.conrelid='public.diaries'::regclass and c.conname='diaries_user_date_uq'
      and c.contype='u' and c.convalidated and not c.condeferrable and not c.condeferred
      and c.conislocal and c.coninhcount=0 and c.conparentid=0
      and c.conkey=array[(select attnum from pg_attribute where attrelid=c.conrelid and attname='user_id'),
                         (select attnum from pg_attribute where attrelid=c.conrelid and attname='diary_date')]::smallint[]
      and i.indrelid=c.conrelid and i.indisunique and i.indisvalid and i.indisready and i.indislive
      and i.indimmediate and not i.indisprimary and not i.indisexclusion
      and not i.indisreplident and not i.indisclustered and i.indexprs is null and i.indpred is null
      and ix.relkind='i' and ix.relnamespace='public'::regnamespace
      and pg_get_indexdef(i.indexrelid) =
          'CREATE UNIQUE INDEX diaries_user_date_uq ON public.diaries USING btree (user_id, diary_date)'
),
 'dependency_conflict',(exists (
    select 1 from pg_constraint c join pg_depend d on
        (d.refclassid='pg_constraint'::regclass and d.refobjid=c.oid)
        or (d.refclassid='pg_class'::regclass and d.refobjid=c.conindid)
    where c.conrelid='public.diaries'::regclass and c.conname='diaries_user_date_uq'
      and not (d.refclassid='pg_constraint'::regclass and d.refobjid=c.oid
          and d.classid='pg_class'::regclass and d.objid=c.conindid
          and d.objsubid=0 and d.refobjsubid=0 and d.deptype='i')
) or exists (
    select 1 from pg_constraint c join pg_constraint fk on fk.contype='f' and fk.conindid=c.conindid
    where c.conrelid='public.diaries'::regclass and c.conname='diaries_user_date_uq'
) or exists (
    select 1 from pg_constraint c join pg_depend d on
        (d.classid='pg_constraint'::regclass and d.objid=c.oid)
        or (d.classid='pg_class'::regclass and d.objid=c.conindid)
    where c.conrelid='public.diaries'::regclass and c.conname='diaries_user_date_uq'
      and d.deptype in ('e','x','P','S')
)),
 'duplicate_diary_groups',(select count(*) from (select 1 from public.diaries group by user_id,diary_date having count(*)>1) q),
 'duplicate_reward_groups',(select count(*) from (select 1 from public.member_daily_reward_grants group by member_id,diary_date having count(*)>1) q),
 'original_data_fingerprint',jsonb_build_object(
'diaries', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diaries t) rows),
'diary_messages', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_messages t) rows),
'diary_assets', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_assets t) rows))
) as v04_preflight;
