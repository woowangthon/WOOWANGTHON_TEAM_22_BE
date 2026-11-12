-- 원격 실행 전 사용자 검토용, 데이터 행 조회 없음
-- SQL Editor 전체 파일 단일 실행
begin read only;
select current_database() as database_name, current_user as operator_role,
    current_setting('server_version') as server_version;
select p.oid::regprocedure as signature, p.prorettype::regtype as return_type,
    p.proargnames as argument_names, p.prosecdef as security_definer,
    p.proconfig as function_settings,
    md5(btrim(replace(p.prosrc, chr(13), ''))) as source_md5,
    pg_get_functiondef(p.oid) as function_definition
from pg_catalog.pg_proc p
where p.oid = to_regprocedure(
    'public.confirm_member_diary(uuid,text,text,integer,text,text,timestamptz,jsonb,jsonb)');
select r.role_name, has_function_privilege(r.role_name,
    'public.confirm_member_diary(uuid,text,text,integer,text,text,timestamptz,jsonb,jsonb)',
    'execute') as rpc_execute
from (values ('anon'), ('authenticated'), ('service_role')) r(role_name);
select a.grantee = 0 as public_grant, a.privilege_type
from pg_catalog.pg_proc p,
    lateral pg_catalog.aclexplode(coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))) a
where p.oid = to_regprocedure(
    'public.confirm_member_diary(uuid,text,text,integer,text,text,timestamptz,jsonb,jsonb)')
    and a.grantee = 0;
select r.role_name, t.table_name,
    has_table_privilege(r.role_name, t.table_name, 'SELECT') as table_select,
    has_table_privilege(r.role_name, t.table_name, 'INSERT') as table_insert
from (values ('anon'), ('authenticated'), ('service_role')) r(role_name)
cross join (values ('public.diaries'), ('public.diary_messages')) t(table_name);
select c.oid::regclass as table_name, c.relrowsecurity as rls_enabled,
    c.relforcerowsecurity as rls_forced, c.relkind as relation_kind,
    pg_get_userbyid(c.relowner) as table_owner
from pg_catalog.pg_class c where c.oid in (
    'public.diaries'::regclass, 'public.diary_messages'::regclass, 'public.diary_assets'::regclass);
select conrelid::regclass as table_name, conname as constraint_name,
    pg_get_constraintdef(oid) as constraint_definition
from pg_catalog.pg_constraint where conrelid in (
    'public.diaries'::regclass, 'public.diary_messages'::regclass, 'public.diary_assets'::regclass)
order by conrelid, conname;
select schemaname, tablename, indexname, indexdef from pg_catalog.pg_indexes
where schemaname = 'public' and tablename in ('diaries','diary_messages','diary_assets')
order by tablename, indexname;
select a.attrelid::regclass as table_name, a.attname as column_name,
    pg_get_expr(d.adbin, d.adrelid) as column_default
from pg_catalog.pg_attribute a join pg_catalog.pg_attrdef d
    on d.adrelid = a.attrelid and d.adnum = a.attnum
where a.attrelid in ('public.diaries'::regclass, 'public.diary_messages'::regclass)
order by a.attrelid, a.attnum;
select tgrelid::regclass as table_name, tgname, tgenabled, pg_get_triggerdef(oid)
from pg_catalog.pg_trigger where not tgisinternal
    and tgrelid in ('public.diaries'::regclass, 'public.diary_messages'::regclass);
select ev_class::regclass as table_name, rulename, pg_get_ruledef(oid)
from pg_catalog.pg_rewrite where rulename <> '_RETURN'
    and ev_class in ('public.diaries'::regclass, 'public.diary_messages'::regclass);
rollback;
