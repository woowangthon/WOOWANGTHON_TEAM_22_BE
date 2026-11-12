-- LOCAL PREPARATION ONLY. 운영/실제 PostgreSQL 미실행.
-- README의 내보내기·백업 확인과 쓰기 중단 이후 postgres 역할로 파일 전체만 실행.
-- 오류/timeout: 해당 파일 전체 롤백. 다음 파일/COMMIT만 실행 금지. 99_rollback_only.sql 참조.
begin isolation level read committed;
set local lock_timeout = '5s';
set local statement_timeout = '60s';
set local idle_in_transaction_session_timeout = '60s';
set local check_function_bodies = on;
set local search_path = public, pg_catalog;
do $role$ begin
    if current_user <> 'postgres' then raise exception 'STOP: postgres role required'; end if;
end; $role$;
lock table public.diaries, public.member_daily_reward_grants, public.member_owned_items in access exclusive mode;
lock table public.diary_messages, public.diary_assets, public.member_reward_legacy_diaries, public.member_reward_wallets, public.shop_items, public.member_room_placements, public.member_room_settings, public.member_tree_states, public.member_tree_watering_events, public.comic_previews, public.diary_comics, public.comic_attach_requests in share mode;
do $preflight$ begin
    if not coalesce((exists(select 1 from storage.buckets where id='diary-private' and public is false)), false) then raise exception 'STOP: expected private Storage bucket missing'; end if;
    if not coalesce((exists (
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
)), false) then raise exception 'STOP: unexpected original date UNIQUE constraint/index'; end if;
    if not coalesce((not (exists (
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
))), false) then raise exception 'STOP: date constraint/index has unexpected dependencies'; end if;
    if not coalesce((not exists(select 1 from public.diaries group by user_id,diary_date having count(*)>1)), false) then raise exception 'STOP: duplicate diary dates'; end if;
    if not coalesce((not exists(select 1 from public.member_daily_reward_grants group by member_id,diary_date having count(*)>1)), false) then raise exception 'STOP: duplicate reward dates; do not delete grants'; end if;
    if not coalesce(((select relkind='r' and not relispartition from pg_class where oid='public.diaries'::regclass) and not exists(select 1 from pg_inherits where inhrelid='public.diaries'::regclass or inhparent='public.diaries'::regclass)), false) then raise exception 'STOP: partition/inheritance drift'; end if;
    if not coalesce(((select count(*)=2 from pg_attribute where attrelid='public.diaries'::regclass and attname in ('user_id','diary_date') and attnotnull and not attisdropped)), false) then raise exception 'STOP: date keys must remain NOT NULL'; end if;
    if not coalesce((not exists(select 1 from pg_trigger where tgrelid in ('public.diaries'::regclass,'public.member_owned_items'::regclass) and not tgisinternal) and not exists(select 1 from pg_rewrite where ev_class in ('public.diaries'::regclass,'public.member_owned_items'::regclass))), false) then raise exception 'STOP: unexpected write trigger/rule on backfilled tables'; end if;
    if not coalesce((not exists(select 1 from pg_index i where i.indrelid='public.diaries'::regclass and i.indisunique and i.indexrelid<>to_regclass('public.diaries_user_date_uq') and i.indkey::text in (select a.attnum::text||' '||b.attnum::text from pg_attribute a cross join pg_attribute b where a.attrelid=i.indrelid and b.attrelid=i.indrelid and (a.attname,b.attname) in (('user_id','diary_date'),('diary_date','user_id'))))), false) then raise exception 'STOP: additional date unique index must be reviewed'; end if;
    if not coalesce((not exists(select 1 from pg_attribute where attrelid='public.diaries'::regclass and attname=any(array['edit_version','updated_at','deleted_at','active_drawing_edit_id']) and not attisdropped)), false) then raise exception 'STOP: v04 columns already exist: diaries'; end if;
    if not coalesce((not exists(select 1 from pg_attribute where attrelid='public.member_owned_items'::regclass and attname=any(array['purchase_id']) and not attisdropped)), false) then raise exception 'STOP: v04 columns already exist: member_owned_items'; end if;
    if not coalesce((not exists(select 1 from pg_attribute where attrelid='public.diary_comics'::regclass and attname=any(array['source_edit_version']) and not attisdropped)), false) then raise exception 'STOP: v04 columns already exist: diary_comics'; end if;
    if not coalesce((to_regclass('public.diary_edit_requests') is null), false) then raise exception 'STOP: v04 object already exists: diary_edit_requests'; end if;
    if not coalesce((to_regclass('public.diary_delete_requests') is null), false) then raise exception 'STOP: v04 object already exists: diary_delete_requests'; end if;
    if not coalesce((to_regclass('public.shop_purchase_receipts') is null), false) then raise exception 'STOP: v04 object already exists: shop_purchase_receipts'; end if;
    if not coalesce((to_regclass('public.shop_refund_receipts') is null), false) then raise exception 'STOP: v04 object already exists: shop_refund_receipts'; end if;
end; $preflight$;
do $rpc$ begin
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.confirm_member_diary(uuid,text,text,integer,text,text,timestamptz,jsonb,jsonb)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='uuid'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_user_id','p_session_id','p_client_request_id','p_history_revision','p_fingerprint','p_weather','p_confirmed_at','p_ai_draft','p_messages']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.confirm_member_diary(uuid,text,text,integer,text,text,timestamptz,jsonb,jsonb)'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.reserve_diary_drawing(uuid,uuid,text,bigint,integer,integer,text)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='jsonb'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_user_id','p_diary_id','p_mime_type','p_byte_size','p_width','p_height','p_sha256']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.reserve_diary_drawing(uuid,uuid,text,bigint,integer,integer,text)'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.complete_diary_drawing(uuid,uuid,uuid)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='void'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_user_id','p_diary_id','p_asset_id']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.complete_diary_drawing(uuid,uuid,uuid)'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.room_guard_owned_catalog()') and p.prokind='f' and not p.prosecdef
        and p.prorettype='trigger'::regtype and coalesce(p.proargnames,array[]::text[])=array[]::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.room_guard_owned_catalog()'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.room_guard_item_usage()') and p.prokind='f' and not p.prosecdef
        and p.prorettype='trigger'::regtype and coalesce(p.proargnames,array[]::text[])=array[]::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.room_guard_item_usage()'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.room_snapshot(uuid,date)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='jsonb'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_member_id','p_on']::text[]
        and l.lanname='sql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.room_snapshot(uuid,date)'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.member_room_command(uuid,text,jsonb)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='jsonb'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_member_id','p_action','p_payload']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.member_room_command(uuid,text,jsonb)'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.comic_panels_valid(jsonb)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='boolean'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_panels']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.comic_panels_valid(jsonb)'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.save_comic_preview_result(jsonb,text)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='jsonb'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_preview','p_fingerprint']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.save_comic_preview_result(jsonb,text)'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.complete_comic_preview_asset(uuid,uuid,uuid)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='jsonb'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_user_id','p_preview_id','p_asset_id']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.complete_comic_preview_asset(uuid,uuid,uuid)'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.attach_diary_comic(uuid,uuid,uuid,text)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='jsonb'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_user_id','p_diary_id','p_preview_id','p_request_id']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.attach_diary_comic(uuid,uuid,uuid,text)'; end if;
end; $rpc$;
do $before$ begin
    perform set_config('today_near.before_data', jsonb_build_object(
'diaries', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diaries t) rows),
'diary_messages', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_messages t) rows),
'diary_assets', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_assets t) rows),
'member_reward_legacy_diaries', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_reward_legacy_diaries t) rows),
'member_reward_wallets', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_reward_wallets t) rows),
'member_daily_reward_grants', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_daily_reward_grants t) rows),
'shop_items', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.shop_items t) rows),
'member_owned_items', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_owned_items t) rows),
'member_room_placements', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_room_placements t) rows),
'member_room_settings', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_room_settings t) rows),
'member_tree_states', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_tree_states t) rows),
'member_tree_watering_events', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_tree_watering_events t) rows),
'comic_previews', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.comic_previews t) rows),
'diary_comics', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_comics t) rows),
'comic_attach_requests', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.comic_attach_requests t) rows))::text, true);
end; $before$;
do $catalog$ begin
 perform set_config('today_near.before_catalog', jsonb_build_object(
 'indexes',(select coalesce(jsonb_agg(jsonb_build_array(i.indexrelid,pg_get_indexdef(i.indexrelid)) order by i.indexrelid),'[]'::jsonb)
    from pg_index i where i.indrelid='public.diaries'::regclass and i.indexrelid<>to_regclass('public.diaries_user_date_uq')),
 'foreign_keys',(select coalesce(jsonb_agg(jsonb_build_array(c.oid,pg_get_constraintdef(c.oid)) order by c.oid),'[]'::jsonb)
    from pg_constraint c where c.contype='f' and (c.conrelid='public.diaries'::regclass or c.confrelid='public.diaries'::regclass)
      ))::text,true);
end; $catalog$;
-- BEGIN ORIGINAL V04 BODY (only DROP now explicitly RESTRICT).
alter table public.diaries
    add column edit_version integer not null default 1 check (edit_version >= 1),
    add column updated_at timestamptz,
    add column deleted_at timestamptz,
    add column active_drawing_edit_id uuid;
update public.diaries set updated_at = confirmed_at;
alter table public.diaries alter column updated_at set default now();
alter table public.diaries alter column updated_at set not null;
alter table public.diaries drop constraint diaries_user_date_uq restrict;
create unique index diaries_user_date_uq on public.diaries(user_id, diary_date) where deleted_at is null;
-- 이미 중복 지급된 날짜가 있으면 설치가 실패한다. 지급 행 삭제/지갑 보정으로 강행하지 않는다.
alter table public.member_daily_reward_grants add constraint reward_member_date_v04_uq unique(member_id, diary_date);

create table public.diary_edit_requests (
    request_id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    diary_id uuid not null,
    client_request_id text not null check (client_request_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    fingerprint text not null check (fingerprint ~ '^[0-9a-f]{64}$'),
    expected_version integer not null check (expected_version between 1 and 2147483646),
    confirmed_text text not null check (char_length(confirmed_text) between 1 and 20000 and confirmed_text ~ '[^[:space:]]'),
    drawing jsonb,
    state text not null default 'pending' check (state in ('pending','completed')),
    result jsonb,
    created_at timestamptz not null default now(),
    unique(user_id, client_request_id),
    unique(request_id, diary_id, user_id),
    foreign key(diary_id, user_id) references public.diaries(id, user_id) on delete restrict,
    check ((state = 'completed') = (result is not null)),
    check (drawing is null or coalesce(
        jsonb_typeof(drawing) = 'object'
        and drawing->>'user_id' = user_id::text and drawing->>'diary_id' = diary_id::text
        and drawing->>'id' = request_id::text and drawing->>'asset_kind' = 'original_drawing'
        and drawing->>'scene_index' = '0' and drawing->>'bucket_id' = 'diary-private'
        and drawing->>'mime_type' = 'image/png' and drawing->>'sha256' ~ '^[0-9a-f]{64}$'
        and drawing->>'storage_key' = user_id::text || '/' || diary_id::text || '/original_drawing/' || request_id::text || '.png'
        and (drawing->>'byte_size')::bigint between 1 and 10485760
        and (drawing->>'width')::integer between 1 and 8192
        and (drawing->>'height')::integer between 1 and 8192
        and (drawing->>'width')::bigint * (drawing->>'height')::bigint <= 16000000
        and drawing->>'upload_status' = case state when 'pending' then 'pending' else 'ready' end,
        false))
);
alter table public.diaries add constraint diaries_active_edit_v04_fk
    foreign key(active_drawing_edit_id, id, user_id)
    references public.diary_edit_requests(request_id, diary_id, user_id) on delete restrict;

create table public.diary_delete_requests (
    user_id uuid not null,
    client_request_id text not null check (client_request_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    diary_id uuid not null,
    expected_version integer not null check (expected_version >= 1),
    deleted_at timestamptz not null,
    primary key(user_id, client_request_id),
    foreign key(diary_id, user_id) references public.diaries(id, user_id) on delete restrict
);

create table public.shop_purchase_receipts (
    purchase_id uuid primary key default gen_random_uuid(),
    member_id uuid not null references auth.users(id) on delete restrict,
    item_id text not null references public.shop_items(item_id) on delete restrict,
    client_request_id text not null check (client_request_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    price_paid bigint not null check (price_paid >= 0),
    purchased_at timestamptz not null,
    unique(member_id, client_request_id),
    unique(purchase_id, member_id, item_id)
);
insert into public.shop_purchase_receipts(member_id,item_id,client_request_id,price_paid,purchased_at)
    select member_id,item_id,purchase_request_id,price_coins,purchased_at from public.member_owned_items;
alter table public.member_owned_items add column purchase_id uuid;
update public.member_owned_items o set purchase_id = r.purchase_id from public.shop_purchase_receipts r
    where r.member_id = o.member_id and r.client_request_id = o.purchase_request_id;
alter table public.member_owned_items alter column purchase_id set not null;
alter table public.member_owned_items add constraint owned_purchase_v04_fk foreign key(purchase_id,member_id,item_id)
    references public.shop_purchase_receipts(purchase_id,member_id,item_id) on delete restrict;

create table public.shop_refund_receipts (
    member_id uuid not null,
    client_request_id text not null check (client_request_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    purchase_id uuid not null unique,
    item_id text not null,
    refunded_coins bigint not null check (refunded_coins >= 0),
    refunded_at timestamptz not null,
    response jsonb not null,
    primary key(member_id, client_request_id),
    foreign key(purchase_id,member_id,item_id) references public.shop_purchase_receipts(purchase_id,member_id,item_id) on delete restrict
);

alter table public.diary_edit_requests enable row level security;
alter table public.diary_delete_requests enable row level security;
alter table public.shop_purchase_receipts enable row level security;
alter table public.shop_refund_receipts enable row level security;
revoke all on public.diary_edit_requests, public.diary_delete_requests,
    public.shop_purchase_receipts, public.shop_refund_receipts from public, anon, authenticated, service_role;
grant select on public.diary_edit_requests to authenticated;
grant select, insert on public.diary_edit_requests, public.diary_delete_requests,
    public.shop_purchase_receipts, public.shop_refund_receipts to service_role;
grant update(drawing,state,result) on public.diary_edit_requests to service_role;
-- 영수증은 UPDATE/DELETE 권한을 부여하지 않는다. 활성 보유만 제거한다.
grant delete on public.member_owned_items to service_role;
grant update(confirmed_text,edit_version,updated_at,deleted_at,active_drawing_edit_id) on public.diaries to service_role;

create policy diary_live_v04 on public.diaries as restrictive for select to authenticated
    using (deleted_at is null);
create policy messages_live_v04 on public.diary_messages as restrictive for select to authenticated
    using (exists(select 1 from public.diaries d where d.id = diary_id and d.user_id = diary_messages.user_id and d.deleted_at is null));
create policy assets_live_v04 on public.diary_assets as restrictive for select to authenticated
    using (exists(select 1 from public.diaries d where d.id = diary_id and d.user_id = diary_assets.user_id and d.deleted_at is null));
create policy edit_drawing_read_v04 on public.diary_edit_requests for select to authenticated
    using (user_id = (select auth.uid()) and (select auth.jwt()->>'is_anonymous') = 'false'
        and state = 'completed' and exists(select 1 from public.diaries d where d.id = diary_id and d.deleted_at is null));
create policy comic_live_v04 on public.diary_comics as restrictive for select to authenticated
    using (exists(select 1 from public.diaries d where d.id = diary_id and d.deleted_at is null));
-- 미연결 만화 preview의 기존 읽기 정책은 보존한다. 이미 발급한 URL은 최대 300초 유효하다.
create policy drawing_storage_live_v04 on storage.objects as restrictive for select to authenticated
    using (bucket_id <> 'diary-private' or (storage.foldername(name))[3] <> 'original_drawing'
        or exists(select 1 from public.diaries d where d.id::text = (storage.foldername(name))[2]
            and d.user_id = (select auth.uid()) and d.deleted_at is null));

alter table public.diary_comics add column source_edit_version integer not null default 1 check (source_edit_version >= 1);

create function public.member_diary_edit(p_user_id uuid, p_diary_id uuid, p_action text, p_payload jsonb)
returns jsonb language plpgsql security invoker set search_path = ''
as $function$
declare
    d public.diaries%rowtype;
    r public.diary_edit_requests%rowtype;
    z public.diary_delete_requests%rowtype;
    a jsonb;
    v_result jsonb;
    ts timestamptz;
    aid uuid;
begin
    if p_user_id is null or not exists(select 1 from auth.users u where u.id = p_user_id
        and u.is_anonymous is false and u.role = 'authenticated' and u.deleted_at is null) then
        raise exception using errcode='22023',message='invalid member'; end if;
    if p_diary_id is null or p_action is null or p_action not in ('reserve','finish','delete')
        or p_payload is null or jsonb_typeof(p_payload) <> 'object'
        or not coalesce(jsonb_typeof(p_payload->'client_request_id') = 'string'
            and p_payload->>'client_request_id' ~ '^[A-Za-z0-9_-]{1,128}$',false) then
        raise exception using errcode='PT422',message='invalid edit',detail='DIARY_EDIT_INVALID'; end if;
    -- 확정 저장과 삭제는 같은 회원 잠금(0), 이후 일기 행 잠금 순서를 따른다.
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_user_id::text,0));
    select * into d from public.diaries where id = p_diary_id and user_id = p_user_id for update;
    if not found then raise exception using errcode='PT404',message='not found',detail='DIARY_NOT_FOUND'; end if;
    if p_action = 'delete' then
        if not coalesce(jsonb_typeof(p_payload->'expected_version') = 'number'
            and p_payload->>'expected_version' ~ '^[1-9][0-9]{0,9}$'
            and (p_payload->>'expected_version')::bigint <= 2147483647,false)
            or (p_payload - array['client_request_id','expected_version']) <> '{}'::jsonb then
            raise exception using errcode='PT422',message='invalid edit',detail='DIARY_EDIT_INVALID'; end if;
        select * into z from public.diary_delete_requests where user_id=p_user_id
            and client_request_id=p_payload->>'client_request_id';
        if found then
            if z.diary_id <> p_diary_id or z.expected_version <> (p_payload->>'expected_version')::integer then
                raise exception using errcode='PT409',message='request conflict',detail='IDEMPOTENCY_CONFLICT'; end if;
            return jsonb_build_object('deleted',true);
        end if;
        if d.deleted_at is not null then
            raise exception using errcode='PT404',message='not found',detail='DIARY_NOT_FOUND'; end if;
        if d.edit_version <> (p_payload->>'expected_version')::integer then
            raise exception using errcode='PT409',message='version conflict',detail='DIARY_EDIT_VERSION_CONFLICT'; end if;
        ts := clock_timestamp();
        update public.diaries set deleted_at=ts,updated_at=ts where id=p_diary_id;
        insert into public.diary_delete_requests(user_id,client_request_id,diary_id,expected_version,deleted_at)
            values(p_user_id,p_payload->>'client_request_id',p_diary_id,d.edit_version,ts);
        return jsonb_build_object('deleted',true);
    end if;
    if d.deleted_at is not null or d.confirmed_at is null then
        raise exception using errcode='PT404',message='not found',detail='DIARY_NOT_FOUND'; end if;
    if not coalesce(jsonb_typeof(p_payload->'fingerprint')='string'
        and p_payload->>'fingerprint' ~ '^[0-9a-f]{64}$',false) then
        raise exception using errcode='PT422',message='invalid edit',detail='DIARY_EDIT_INVALID'; end if;
    select * into r from public.diary_edit_requests where user_id=p_user_id
        and client_request_id=p_payload->>'client_request_id' for update;
    if found then
        if r.diary_id <> p_diary_id or r.fingerprint <> p_payload->>'fingerprint' then
            raise exception using errcode='PT409',message='request conflict',detail='IDEMPOTENCY_CONFLICT'; end if;
        if r.state='completed' then return to_jsonb(r); end if;
    end if;
    if p_action='reserve' then
        if not coalesce(jsonb_typeof(p_payload->'expected_version') = 'number'
            and p_payload->>'expected_version' ~ '^[1-9][0-9]{0,9}$'
            and (p_payload->>'expected_version')::bigint <= 2147483646
            and jsonb_typeof(p_payload->'confirmed_text')='string'
            and char_length(p_payload->>'confirmed_text') between 1 and 20000
            and p_payload->>'confirmed_text' ~ '[^[:space:]]',false)
            or not (p_payload ? 'drawing')
            or (p_payload - array['client_request_id','expected_version','confirmed_text','fingerprint','drawing']) <> '{}'::jsonb then
            raise exception using errcode='PT422',message='invalid edit',detail='DIARY_EDIT_INVALID'; end if;
        if d.edit_version <> (p_payload->>'expected_version')::integer then
            raise exception using errcode='PT409',message='version conflict',detail='DIARY_EDIT_VERSION_CONFLICT'; end if;
        if r.request_id is not null then return to_jsonb(r); end if;
        aid := gen_random_uuid();
        a := nullif(p_payload->'drawing','null'::jsonb);
        if a is not null then
            if not coalesce(jsonb_typeof(a)='object' and a ?& array['mime_type','byte_size','width','height','sha256']
                and (a-array['mime_type','byte_size','width','height','sha256'])='{}'::jsonb,false) then
                raise exception using errcode='PT422',message='invalid image',detail='DIARY_EDIT_INVALID'; end if;
            a := a || jsonb_build_object('id',aid,'user_id',p_user_id,'diary_id',p_diary_id,
                'asset_kind','original_drawing','scene_index',0,'bucket_id','diary-private','upload_status','pending',
                'storage_key',p_user_id::text||'/'||p_diary_id::text||'/original_drawing/'||aid::text||'.png');
        end if;
        insert into public.diary_edit_requests(request_id,user_id,diary_id,client_request_id,fingerprint,expected_version,confirmed_text,drawing)
            values(aid,p_user_id,p_diary_id,p_payload->>'client_request_id',p_payload->>'fingerprint',
                (p_payload->>'expected_version')::integer,p_payload->>'confirmed_text',a) returning * into r;
        return to_jsonb(r);
    end if;
    if (p_payload-array['client_request_id','fingerprint']) <> '{}'::jsonb or r.request_id is null then
        raise exception using errcode='PT422',message='invalid receipt',detail='DIARY_EDIT_INVALID'; end if;
    if d.edit_version <> r.expected_version then
        raise exception using errcode='PT409',message='version conflict',detail='DIARY_EDIT_VERSION_CONFLICT'; end if;
    -- 바이트 업로드/해시 확인은 신뢰된 BE가 수행한다. DB 완료 호출은 그 뒤에만 허용한다.
    a := r.drawing;
    if a is not null then
        a := a || jsonb_build_object('upload_status','ready');
    elsif d.active_drawing_edit_id is not null then
        select drawing into a from public.diary_edit_requests where request_id=d.active_drawing_edit_id and state='completed';
    else
        select to_jsonb(x) into a from public.diary_assets x where x.diary_id=p_diary_id
            and x.user_id=p_user_id and x.asset_kind='original_drawing' and x.scene_index=0 and x.upload_status='ready';
    end if;
    ts := clock_timestamp();
    v_result := jsonb_build_object('diary_id',p_diary_id,'edit_version',d.edit_version+1,
        'confirmed_text',r.confirmed_text,'updated_at',ts,'original_drawing',a);
    update public.diary_edit_requests set state='completed',result=v_result,
        drawing=case when r.drawing is null then null else a end where request_id=r.request_id returning * into r;
    update public.diaries set confirmed_text=r.confirmed_text,edit_version=edit_version+1,updated_at=ts,
        active_drawing_edit_id=case when r.drawing is not null then r.request_id else active_drawing_edit_id end
        where id=p_diary_id;
    return to_jsonb(r);
end;
$function$;
revoke all on function public.member_diary_edit(uuid,uuid,text,jsonb) from public,anon,authenticated;
grant execute on function public.member_diary_edit(uuid,uuid,text,jsonb) to service_role;

-- 이하 기존 RPC의 v0.4 교체본은 생성 스크립트가 원본을 보존하며 추가한다.
-- BEGIN GENERATED REPLACEMENTS

create or replace function public.confirm_member_diary(
    p_user_id uuid,
    p_session_id text,
    p_client_request_id text,
    p_history_revision integer,
    p_fingerprint text,
    p_weather text,
    p_confirmed_at timestamptz,
    p_ai_draft jsonb,
    p_messages jsonb
) returns uuid
language plpgsql
security invoker
set search_path = ''
as $function$
declare
    v_existing public.diaries%rowtype;
    v_diary_id uuid;
    v_date date;
    v_constraint text;
begin
    -- 회원 유효성 확인
    if p_user_id is null or not exists (
        select 1 from auth.users as u
        where u.id = p_user_id and u.is_anonymous is false
            and u.role = 'authenticated' and u.deleted_at is null
    ) then
        raise exception using errcode = '22023', message = 'invalid member';
    end if;
    if p_fingerprint is null or p_fingerprint !~ '^[0-9a-f]{64}$'
        or p_confirmed_at is null or not pg_catalog.isfinite(p_confirmed_at)
        or p_session_id is null or p_session_id !~ '^ses_[0-9a-f]{32}$'
        or p_client_request_id is null
        or p_client_request_id !~ '^[A-Za-z0-9_-]{1,128}$'
        or p_history_revision is null or p_history_revision < 1
        or (p_weather is not null and p_weather not in ('sunny','cloudy','snowy','rainy'))
    then
        raise exception using errcode = '22023', message = 'invalid confirmation';
    end if;

    -- 동일 회원 저장 직렬화와 재시작 이후 중복 요청 확인
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_user_id::text, 0));
    select d.* into v_existing from public.diaries as d
    where d.user_id = p_user_id and d.source_client_request_id = p_client_request_id
        and d.confirmation_fingerprint is not null;
    if found then
        if v_existing.confirmation_fingerprint <> p_fingerprint then
            -- 요청 충돌 처리
            raise exception using errcode = 'PT409', message = '요청 내용 불일치',
                detail = 'IDEMPOTENCY_CONFLICT';
        end if;
        return v_existing.id;
    end if;

    -- 날짜 중복 확인
    v_date := (p_confirmed_at at time zone 'Asia/Seoul')::date;
    if exists (select 1 from public.diaries as d
               where d.user_id = p_user_id and d.diary_date = v_date and d.deleted_at is null) then
        raise exception using errcode = 'PT409', message = '날짜 중복',
            detail = 'DIARY_DATE_CONFLICT';
    end if;

    -- 원본 대화 구조·순서 확인
    if p_messages is null or pg_catalog.jsonb_typeof(p_messages) <> 'array' then
        raise exception using errcode = '22023', message = 'invalid messages';
    end if;
    if pg_catalog.jsonb_array_length(p_messages) not between 3 and 60 then
        raise exception using errcode = '22023', message = 'invalid messages';
    end if;
    if exists (
        select 1 from pg_catalog.jsonb_array_elements(p_messages)
            with ordinality as m(value, ordinal)
        where not coalesce(
            pg_catalog.jsonb_typeof(m.value) = 'object'
            and pg_catalog.jsonb_typeof(m.value -> 'position') = 'number'
            and m.value ->> 'position' = (m.ordinal - 1)::text
            and pg_catalog.jsonb_typeof(m.value -> 'role') = 'string'
            and m.value ->> 'role' in ('child', 'assistant')
            and pg_catalog.jsonb_typeof(m.value -> 'text') = 'string', false
        )
    ) or not exists (
        select 1 from pg_catalog.jsonb_array_elements(p_messages) as m(value)
        where m.value ->> 'role' = 'child'
    ) then
        raise exception using errcode = '22023', message = 'invalid messages';
    end if;

    insert into public.diaries (
        user_id, diary_date, weather, confirmed_text, confirmed_at,
        source_session_id, source_history_revision, source_client_request_id,
        ai_draft, confirmation_fingerprint, updated_at
    ) values (
        p_user_id, v_date, p_weather, p_ai_draft ->> 'text', p_confirmed_at,
        p_session_id, p_history_revision, p_client_request_id, p_ai_draft, p_fingerprint, p_confirmed_at
    ) returning id into v_diary_id;

    insert into public.diary_messages (diary_id, user_id, position, role, text)
    select v_diary_id, p_user_id, (m.value ->> 'position')::integer,
        m.value ->> 'role', m.value ->> 'text'
    from pg_catalog.jsonb_array_elements(p_messages) with ordinality as m(value, ordinal)
    order by m.ordinal;
    return v_diary_id;
exception when unique_violation then
    -- 예외 블록 전체 쓰기 롤백
    get stacked diagnostics v_constraint = constraint_name;
    if v_constraint = 'diaries_user_date_uq' then
        raise exception using errcode = 'PT409', message = '날짜 중복',
            detail = 'DIARY_DATE_CONFLICT';
    end if;
    raise;
end;
$function$;

create or replace function public.reserve_diary_drawing(
    p_user_id uuid, p_diary_id uuid, p_mime_type text, p_byte_size bigint,
    p_width integer, p_height integer, p_sha256 text
) returns jsonb
language plpgsql security invoker set search_path = ''
as $function$
declare
    v_asset public.diary_assets%rowtype;
    v_id uuid;
begin
    perform 1 from public.diaries d where d.id=p_diary_id and d.user_id=p_user_id and d.deleted_at is null for share;
    if not found then raise exception using errcode='PT404',message='not found',detail='DIARY_NOT_FOUND'; end if;
    if exists(select 1 from public.diaries where id=p_diary_id and edit_version <> 1) then
        raise exception using errcode='PT409',message='use edit API',detail='DIARY_EDIT_VERSION_CONFLICT'; end if;
    -- 회원 소유권 확인
    if p_user_id is null or p_diary_id is null or not exists (
        select 1 from auth.users u where u.id = p_user_id
            and u.is_anonymous is false and u.role = 'authenticated' and u.deleted_at is null
    ) or not exists (
        select 1 from public.diaries d where d.id = p_diary_id and d.user_id = p_user_id
    ) then
        raise exception using errcode = '22023', message = 'invalid owner';
    end if;
    if p_mime_type is null or p_mime_type not in ('image/png', 'image/jpeg')
        or p_byte_size is null or p_byte_size not between 1 and 10485760
        or p_width is null or p_width not between 1 and 8192
        or p_height is null or p_height not between 1 and 8192
        or p_width::bigint * p_height::bigint > 16000000
        or p_sha256 is null or p_sha256 !~ '^[0-9a-f]{64}$' then
        raise exception using errcode = '22023', message = 'invalid drawing';
    end if;
    -- 일기별 원본 예약 직렬화
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_diary_id::text, 4));
    select a.* into v_asset from public.diary_assets a
    where a.diary_id = p_diary_id and a.user_id = p_user_id
        and a.asset_kind = 'original_drawing' and a.scene_index = 0;
    if found then
        if v_asset.sha256 <> p_sha256 or v_asset.mime_type <> p_mime_type
            or v_asset.byte_size <> p_byte_size or v_asset.width <> p_width
            or v_asset.height <> p_height then
            raise exception using errcode = 'PT409', message = '이미 저장된 그림',
                detail = 'DRAWING_CONFLICT';
        end if;
        return pg_catalog.to_jsonb(v_asset);
    end if;
    v_id := gen_random_uuid();
    insert into public.diary_assets (
        id, diary_id, user_id, asset_kind, scene_index, bucket_id, storage_key,
        mime_type, byte_size, width, height, sha256, upload_status
    ) values (
        v_id, p_diary_id, p_user_id, 'original_drawing', 0, 'diary-private',
        p_user_id::text || '/' || p_diary_id::text || '/original_drawing/' || v_id::text
            || case p_mime_type when 'image/png' then '.png' else '.jpg' end,
        p_mime_type, p_byte_size, p_width, p_height, p_sha256, 'pending'
    ) returning * into v_asset;
    return pg_catalog.to_jsonb(v_asset);
end;
$function$;

create or replace function public.complete_diary_drawing(p_user_id uuid, p_diary_id uuid, p_asset_id uuid)
returns void language plpgsql security invoker set search_path = ''
as $function$
begin
    perform 1 from public.diaries d where d.id=p_diary_id and d.user_id=p_user_id and d.deleted_at is null for share;
    if not found then raise exception using errcode='PT404',message='not found',detail='DIARY_NOT_FOUND'; end if;
    if exists(select 1 from public.diaries where id=p_diary_id and edit_version <> 1) then
        raise exception using errcode='PT409',message='use edit API',detail='DIARY_EDIT_VERSION_CONFLICT'; end if;
    -- 회원 소유권 재확인
    if not exists (
        select 1 from auth.users u where u.id = p_user_id
            and u.is_anonymous is false and u.role = 'authenticated' and u.deleted_at is null
    ) then
        raise exception using errcode = '22023', message = 'invalid owner';
    end if;
    update public.diary_assets a set upload_status = 'ready', updated_at = now()
    where a.id = p_asset_id and a.user_id = p_user_id and a.diary_id = p_diary_id
        and a.asset_kind = 'original_drawing' and a.scene_index = 0
        and exists (select 1 from public.diaries d
                    where d.id = p_diary_id and d.user_id = p_user_id);
    if not found then
        raise exception using errcode = '22023', message = 'invalid asset';
    end if;
end;
$function$;

create or replace function public.room_snapshot(p_member_id uuid, p_on date) returns jsonb
language sql stable security invoker set search_path = ''
as $function$
    select pg_catalog.jsonb_build_object(
        'as_of_date', p_on,
        'wallet', pg_catalog.jsonb_build_object(
            'coins', coalesce(w.coins, 0), 'water_drops', coalesce(w.water_drops, 0)),
        'room', pg_catalog.jsonb_build_object('width', 1440, 'height', 900,
            'theme', pg_catalog.jsonb_build_object('item_id', s.active_theme_item_id,
                'background_asset_key', coalesce(t.background_asset_key, 'room_default'),
                'diary_cover_asset_key', coalesce(t.diary_cover_asset_key, 'cover_default'))),
        'tree', pg_catalog.jsonb_build_object('stage', coalesce(g.growth_points, 0), 'max_stage', 7,
            'days_watered', coalesce(g.growth_points, 0),
            'watered_today', exists (select 1 from public.member_tree_watering_events e
                where e.member_id = p_member_id and e.watered_on = p_on),
            'asset_key', case when coalesce(g.growth_points, 0) < 7
                then 'tree_stage_' || coalesce(g.growth_points, 0)::text
                else coalesce(t.adult_tree_asset_key, 'tree_adult_default') end),
        'inventory', coalesce((select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                'item_id', i.item_id, 'name', i.name, 'category', i.category,
                'asset_key', i.asset_key, 'width', i.width, 'height', i.height,
                'refundable', i.category in ('furniture','decoration'),
                'refund_coins', case when i.category in ('furniture','decoration') then o.price_coins else null end,
                'position', case when p.item_id is null then null
                    else pg_catalog.jsonb_build_object('x', p.x, 'y', p.y) end) order by i.item_id)
            from public.member_owned_items o join public.shop_items i on i.item_id = o.item_id
            left join public.member_room_placements p on p.member_id = o.member_id and p.item_id = o.item_id
            where o.member_id = p_member_id), '[]'::jsonb)
    )
    from (values (p_member_id)) as m(member_id)
    left join public.member_reward_wallets w on w.member_id = m.member_id
    left join public.member_room_settings s on s.member_id = m.member_id
    left join public.shop_items t on t.item_id = s.active_theme_item_id
    left join public.member_tree_states g on g.member_id = m.member_id;
$function$;

create or replace function public.member_room_command(
    p_member_id uuid, p_action text, p_payload jsonb
) returns jsonb
language plpgsql security invoker set search_path = ''
as $function$
declare
    v_now timestamptz;
    v_today date;
    v_keys text[];
    v_item public.shop_items%rowtype;
    v_owned public.member_owned_items%rowtype;
    v_purchase public.shop_purchase_receipts%rowtype;
    v_refund public.shop_refund_receipts%rowtype;
    v_event public.member_tree_watering_events%rowtype;
    v_wallet public.member_reward_wallets%rowtype;
    v_state jsonb;
    v_rewards jsonb;
    v_count integer;
    v_stage integer;
    v_x integer;
    v_y integer;
begin
    if p_member_id is null or not exists (select 1 from auth.users u where u.id = p_member_id
        and u.is_anonymous is false and u.role = 'authenticated' and u.deleted_at is null) then
        raise exception using errcode = '22023', message = 'invalid member';
    end if;
    if p_action is null or p_action not in ('get_room', 'list_items', 'sync_rewards',
        'purchase', 'refund', 'place', 'remove_placement', 'theme', 'water') then
        raise exception using errcode = '22023', message = 'invalid room action';
    end if;
    v_keys := case p_action
        when 'purchase' then array['client_request_id', 'item_id']
        when 'refund' then array['client_request_id', 'item_id']
        when 'place' then array['item_id', 'x', 'y']
        when 'remove_placement' then array['item_id']
        when 'theme' then array['item_id']
        when 'water' then array['client_request_id'] else array[]::text[] end;
    if p_payload is null or pg_catalog.jsonb_typeof(p_payload) <> 'object'
        or not (p_payload ?& v_keys) or (p_payload - v_keys) <> '{}'::jsonb then
        raise exception using errcode = '22023', message = 'invalid room payload';
    end if;
    if p_payload ? 'client_request_id' and (pg_catalog.jsonb_typeof(p_payload->'client_request_id') <> 'string'
        or (p_payload->>'client_request_id') !~ '^[A-Za-z0-9_-]{1,128}$') then
        raise exception using errcode = '22023', message = 'invalid request id';
    end if;
    if p_payload ? 'item_id' and not (p_action = 'theme' and p_payload->'item_id' = 'null'::jsonb)
        and (pg_catalog.jsonb_typeof(p_payload->'item_id') <> 'string'
            or (p_payload->>'item_id') !~ '^[A-Za-z0-9_-]{1,128}$') then
        raise exception using errcode = '22023', message = 'invalid item id';
    end if;

    -- 모든 명령이 같은 회원 잠금을 사용하여 구매/지급/물 사용 간 잔액 경합도 직렬화한다.
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_member_id::text, 22));
    -- 잠금을 기다리는 동안 서울 자정이 지났다면 실제 처리일을 사용한다.
    v_now := pg_catalog.clock_timestamp();
    v_today := (v_now at time zone 'Asia/Seoul')::date;
    if p_action = 'get_room' then
        return public.room_snapshot(p_member_id, v_today);
    elsif p_action = 'list_items' then
        return pg_catalog.jsonb_build_object('items', coalesce((
            select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                'item_id', i.item_id, 'name', i.name, 'category', i.category,
                'price_coins', i.price_coins, 'asset_key', i.asset_key, 'width', i.width, 'height', i.height,
                'background_asset_key', i.background_asset_key, 'diary_cover_asset_key', i.diary_cover_asset_key,
                'adult_tree_asset_key', i.adult_tree_asset_key,
                'owned', exists (select 1 from public.member_owned_items o
                    where o.member_id = p_member_id and o.item_id = i.item_id)) order by i.item_id)
            from public.shop_items i where i.is_active), '[]'::jsonb));
    end if;

    insert into public.member_reward_wallets(member_id) values (p_member_id) on conflict do nothing;
    select w.* into v_wallet from public.member_reward_wallets w where w.member_id = p_member_id for update;

    if p_action = 'sync_rewards' then
        with granted as (
            insert into public.member_daily_reward_grants(member_id, diary_date, diary_id, granted_at)
            select p_member_id, d.diary_date, d.id, v_now from (
                -- 삭제된 첫 확정도 자격 기록으로 유지한다. 소급 보류된 첫 일기는 새 ID로 우회할 수 없다.
                select distinct on (diary_date) id, diary_date from public.diaries
                where user_id = p_member_id and confirmation_fingerprint is not null
                order by diary_date, confirmed_at, id
            ) d where true
                and not exists (select 1 from public.member_reward_legacy_diaries old
                    where old.diary_id = d.id)
                -- fingerprint가 없는 과거 일기도 그 회원의 날짜 전체를 소급 보류한다.
                and not exists (select 1 from public.member_reward_legacy_diaries held
                    join public.diaries original on original.id = held.diary_id
                        and original.user_id = held.member_id
                    where held.member_id = p_member_id and original.diary_date = d.diary_date)
            on conflict (member_id, diary_date) do nothing
            returning diary_date, coins, water_drops
        ) select count(*)::integer, coalesce(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
            'diary_date', diary_date, 'coins', coins, 'water_drops', water_drops) order by diary_date), '[]'::jsonb)
            into v_count, v_rewards from granted;
        update public.member_reward_wallets set coins = coins + v_count::bigint * 30,
            water_drops = water_drops + v_count, updated_at = v_now where member_id = p_member_id;
        v_state := public.room_snapshot(p_member_id, v_today);
        return pg_catalog.jsonb_build_object('new_rewards', v_rewards, 'wallet', v_state->'wallet');
    elsif p_action = 'purchase' then
        select * into v_purchase from public.shop_purchase_receipts where member_id=p_member_id
            and client_request_id=p_payload->>'client_request_id';
        if found then
            if v_purchase.item_id <> p_payload->>'item_id' then
                raise exception using errcode='PT409',message='request conflict',detail='IDEMPOTENCY_CONFLICT'; end if;
            if exists(select 1 from public.shop_refund_receipts where purchase_id=v_purchase.purchase_id) then
                raise exception using errcode='PT409',message='purchase refunded',detail='PURCHASE_ALREADY_REFUNDED'; end if;
        else
            select * into v_item from public.shop_items where item_id=p_payload->>'item_id' and is_active for share;
            if not found then raise exception using errcode='PT404',message='item not found',detail='ITEM_NOT_FOUND'; end if;
            if exists(select 1 from public.member_owned_items where member_id=p_member_id and item_id=v_item.item_id) then
                raise exception using errcode='PT409',message='owned',detail='ITEM_ALREADY_OWNED'; end if;
            if v_wallet.coins < v_item.price_coins then
                raise exception using errcode='PT409',message='insufficient',detail='INSUFFICIENT_COINS'; end if;
            insert into public.shop_purchase_receipts(member_id,item_id,client_request_id,price_paid,purchased_at)
                values(p_member_id,v_item.item_id,p_payload->>'client_request_id',v_item.price_coins,v_now) returning * into v_purchase;
            update public.member_reward_wallets set coins=coins-v_purchase.price_paid,updated_at=v_now where member_id=p_member_id;
            insert into public.member_owned_items(member_id,item_id,price_coins,purchase_request_id,purchased_at,purchase_id)
                values(p_member_id,v_item.item_id,v_purchase.price_paid,v_purchase.client_request_id,v_now,v_purchase.purchase_id);
        end if;
        v_state := public.room_snapshot(p_member_id,v_today);
        return jsonb_build_object('item_id',p_payload->>'item_id','owned',true,'wallet',v_state->'wallet');
    elsif p_action = 'refund' then
        select * into v_refund from public.shop_refund_receipts where member_id=p_member_id
            and client_request_id=p_payload->>'client_request_id';
        if found then
            if v_refund.item_id <> p_payload->>'item_id' then
                raise exception using errcode='PT409',message='request conflict',detail='IDEMPOTENCY_CONFLICT'; end if;
            return v_refund.response;
        end if;
        select * into v_owned from public.member_owned_items where member_id=p_member_id and item_id=p_payload->>'item_id' for update;
        if not found then raise exception using errcode='PT409',message='not owned',detail='ITEM_NOT_OWNED'; end if;
        select * into v_item from public.shop_items where item_id=v_owned.item_id for share;
        if v_item.category not in ('furniture','decoration') then
            raise exception using errcode='PT422',message='not refundable',detail='ITEM_NOT_REFUNDABLE'; end if;
        select * into strict v_purchase from public.shop_purchase_receipts where purchase_id=v_owned.purchase_id
            and member_id=p_member_id and item_id=v_owned.item_id;
        -- 삭제되는 것은 활성 배치/보유뿐이다. 불변 구매와 환불 영수증은 남는다.
        delete from public.member_room_placements where member_id=p_member_id and item_id=v_owned.item_id;
        delete from public.member_owned_items where member_id=p_member_id and item_id=v_owned.item_id;
        update public.member_reward_wallets set coins=coins+v_purchase.price_paid,updated_at=v_now where member_id=p_member_id;
        v_state := public.room_snapshot(p_member_id,v_today);
        v_state := jsonb_build_object('item_id',v_owned.item_id,'refunded_coins',v_purchase.price_paid,'owned',false,'wallet',v_state->'wallet');
        insert into public.shop_refund_receipts(member_id,client_request_id,purchase_id,item_id,refunded_coins,refunded_at,response)
            values(p_member_id,p_payload->>'client_request_id',v_purchase.purchase_id,v_owned.item_id,v_purchase.price_paid,v_now,v_state);
        return v_state;
    elsif p_action in ('place', 'remove_placement', 'theme') then
        if p_payload->>'item_id' is not null then
            select i.* into v_item from public.shop_items i where i.item_id = p_payload->>'item_id' for share;
            if not found then
                raise exception using errcode = 'PT404', message = 'item not found', detail = 'ITEM_NOT_FOUND';
            end if;
            if not exists (select 1 from public.member_owned_items o
                where o.member_id = p_member_id and o.item_id = v_item.item_id) then
                raise exception using errcode = 'PT409', message = 'item not owned', detail = 'ITEM_NOT_OWNED';
            end if;
        end if;
        if p_action = 'theme' then
            if v_item.item_id is not null and v_item.category <> 'theme' then
                raise exception using errcode = 'PT404', message = 'theme not found', detail = 'ITEM_NOT_FOUND';
            end if;
            insert into public.member_room_settings(member_id, active_theme_item_id, updated_at)
                values (p_member_id, p_payload->>'item_id', v_now)
                on conflict (member_id) do update set active_theme_item_id = excluded.active_theme_item_id,
                    updated_at = excluded.updated_at;
            v_state := public.room_snapshot(p_member_id, v_today);
            return pg_catalog.jsonb_build_object('theme', v_state->'room'->'theme', 'tree', v_state->'tree');
        end if;
        if v_item.category not in ('furniture', 'decoration') then
            raise exception using errcode = 'PT422', message = 'not placeable', detail = 'ITEM_NOT_PLACEABLE';
        end if;
        if p_action = 'remove_placement' then
            delete from public.member_room_placements where member_id = p_member_id and item_id = v_item.item_id;
            return '{}'::jsonb;
        end if;
        if pg_catalog.jsonb_typeof(p_payload->'x') <> 'number'
            or pg_catalog.jsonb_typeof(p_payload->'y') <> 'number'
            or (p_payload->>'x') !~ '^[0-9]{1,4}$' or (p_payload->>'y') !~ '^[0-9]{1,3}$' then
            raise exception using errcode = 'PT422', message = 'invalid position', detail = 'ROOM_POSITION_INVALID';
        end if;
        v_x := (p_payload->>'x')::integer;
        v_y := (p_payload->>'y')::integer;
        if v_x < 0 or v_y < 0 or v_x + v_item.width > 1440 or v_y + v_item.height > 900 then
            raise exception using errcode = 'PT422', message = 'invalid position', detail = 'ROOM_POSITION_INVALID';
        end if;
        insert into public.member_room_placements(member_id, item_id, x, y, updated_at)
            values (p_member_id, v_item.item_id, v_x, v_y, v_now)
            on conflict (member_id, item_id) do update set x = excluded.x, y = excluded.y, updated_at = excluded.updated_at;
        return pg_catalog.jsonb_build_object('item_id', v_item.item_id, 'position', pg_catalog.jsonb_build_object('x', v_x, 'y', v_y));
    elsif p_action = 'water' then
        select e.* into v_event from public.member_tree_watering_events e
            where e.member_id = p_member_id and e.client_request_id = p_payload->>'client_request_id';
        if not found then
            insert into public.member_tree_states(member_id) values (p_member_id) on conflict do nothing;
            select g.growth_points into v_stage from public.member_tree_states g where g.member_id = p_member_id for update;
            if v_stage >= 7 then
                raise exception using errcode = 'PT409', message = 'full grown', detail = 'TREE_FULL_GROWN';
            end if;
            if exists (select 1 from public.member_tree_watering_events e
                where e.member_id = p_member_id and e.watered_on = v_today) then
                raise exception using errcode = 'PT409', message = 'already watered', detail = 'TREE_ALREADY_WATERED_TODAY';
            end if;
            if v_wallet.water_drops < 1 then
                raise exception using errcode = 'PT409', message = 'insufficient water', detail = 'INSUFFICIENT_WATER';
            end if;
            update public.member_reward_wallets set water_drops = water_drops - 1, updated_at = v_now where member_id = p_member_id;
            update public.member_tree_states set growth_points = growth_points + 1, updated_at = v_now where member_id = p_member_id;
            insert into public.member_tree_watering_events(member_id, client_request_id, watered_on, created_at)
                values (p_member_id, p_payload->>'client_request_id', v_today, v_now) returning * into v_event;
        end if;
        v_state := public.room_snapshot(p_member_id, v_today);
        return pg_catalog.jsonb_build_object('watered_on', v_event.watered_on, 'wallet', v_state->'wallet', 'tree', v_state->'tree');
    end if;
    raise exception using errcode = '22023', message = 'unhandled room action';
end;
$function$;

create or replace function public.attach_diary_comic(p_user_id uuid, p_diary_id uuid, p_preview_id uuid, p_request_id text)
returns jsonb language plpgsql security invoker set search_path = ''
as $function$
declare
    v public.comic_previews%rowtype;
    d public.diaries%rowtype;
    r public.comic_attach_requests%rowtype;
    existing public.diary_comics%rowtype;
begin
    if p_request_id is null or p_request_id !~ '^[A-Za-z0-9_-]{1,128}$'
        or not exists (select 1 from auth.users a where a.id = p_user_id
        and a.is_anonymous is false and a.role = 'authenticated' and a.deleted_at is null)
        then raise exception 'invalid owner or request'; end if;
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_user_id::text, 61));
    select * into d from public.diaries where id = p_diary_id and user_id = p_user_id and deleted_at is null for share;
    if not found then return jsonb_build_object('error','COMIC_PREVIEW_MISMATCH'); end if;
    select * into v from public.comic_previews
        where preview_id = p_preview_id and user_id = p_user_id for update;
    if not found then return jsonb_build_object('error','COMIC_PREVIEW_NOT_FOUND'); end if;
    select * into r from public.comic_attach_requests
        where user_id = p_user_id and client_request_id = p_request_id;
    if found and (r.diary_id <> p_diary_id or r.preview_id <> p_preview_id) then
        return jsonb_build_object('error','IDEMPOTENCY_CONFLICT'); end if;
    if d.edit_version <> 1 or d.confirmed_at is null or d.confirmation_fingerprint is null
        or d.source_session_id <> v.session_id or d.source_history_revision <> v.history_revision
        or d.ai_draft->>'draft_id' is distinct from v.draft_id
        or d.ai_draft->>'text' is distinct from v.draft_text
        or d.confirmed_text is distinct from v.draft_text then
        return jsonb_build_object('error','COMIC_PREVIEW_MISMATCH'); end if;
    if exists (select 1 from jsonb_array_elements(v.panels) p where p->>'outcome' = 'pending')
        or not exists (select 1 from jsonb_array_elements(v.panels) p where p->>'outcome' = 'ready') then
        return jsonb_build_object('error','COMIC_NOT_READY'); end if;
    select * into existing from public.diary_comics
        where diary_id = p_diary_id or preview_id = p_preview_id;
    if found and (existing.diary_id <> p_diary_id or existing.preview_id <> p_preview_id) then
        return jsonb_build_object('error','COMIC_ALREADY_ATTACHED'); end if;
    insert into public.diary_comics(diary_id,user_id,preview_id)
        values (p_diary_id,p_user_id,p_preview_id) on conflict (diary_id) do nothing;
    insert into public.comic_attach_requests(user_id,client_request_id,diary_id,preview_id)
        values (p_user_id,p_request_id,p_diary_id,p_preview_id)
        on conflict (user_id,client_request_id) do nothing;
    return to_jsonb(v) - array['result_fingerprint','created_at'];
end;
$function$;

-- END ORIGINAL V04 BODY. No service RPC is invoked during installation.
do $after$ begin
    if current_setting('today_near.before_data')::jsonb is distinct from jsonb_build_object(
'diaries', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t) - array['edit_version','updated_at','deleted_at','active_drawing_edit_id'])::text) h from public.diaries t) rows),
'diary_messages', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_messages t) rows),
'diary_assets', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_assets t) rows),
'member_reward_legacy_diaries', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_reward_legacy_diaries t) rows),
'member_reward_wallets', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_reward_wallets t) rows),
'member_daily_reward_grants', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_daily_reward_grants t) rows),
'shop_items', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.shop_items t) rows),
'member_owned_items', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t) - array['purchase_id'])::text) h from public.member_owned_items t) rows),
'member_room_placements', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_room_placements t) rows),
'member_room_settings', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_room_settings t) rows),
'member_tree_states', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_tree_states t) rows),
'member_tree_watering_events', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.member_tree_watering_events t) rows),
'comic_previews', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.comic_previews t) rows),
'diary_comics', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t) - array['source_edit_version'])::text) h from public.diary_comics t) rows),
'comic_attach_requests', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.comic_attach_requests t) rows)) then
        raise exception 'STOP: existing data changed; transaction aborted';
    end if;
end; $after$;
do $postflight$ begin
    if not coalesce((not exists(select 1 from pg_constraint where conrelid='public.diaries'::regclass
    and conname='diaries_user_date_uq')
and exists(select 1 from pg_index i where i.indexrelid=to_regclass('public.diaries_user_date_uq')
    and i.indrelid='public.diaries'::regclass and i.indisunique and i.indisvalid and i.indisready
    and i.indislive and i.indimmediate and i.indexprs is null
    and pg_get_expr(i.indpred,i.indrelid)='(deleted_at IS NULL)'
    and pg_get_indexdef(i.indexrelid)=
        'CREATE UNIQUE INDEX diaries_user_date_uq ON public.diaries USING btree (user_id, diary_date) WHERE (deleted_at IS NULL)')), false) then raise exception 'STOP: replacement partial UNIQUE index mismatch'; end if;
    if not coalesce((not exists(select 1 from public.diaries where deleted_at is null group by user_id,diary_date having count(*)>1)), false) then raise exception 'STOP: duplicate live diary dates'; end if;
    if not coalesce((not exists(select 1 from public.diaries where edit_version<>1 or deleted_at is not null or active_drawing_edit_id is not null or updated_at is distinct from confirmed_at)), false) then raise exception 'STOP: unexpected backfill values'; end if;
    if not coalesce((current_setting('today_near.before_catalog')::jsonb=jsonb_build_object(
 'indexes',(select coalesce(jsonb_agg(jsonb_build_array(i.indexrelid,pg_get_indexdef(i.indexrelid)) order by i.indexrelid),'[]'::jsonb)
    from pg_index i where i.indrelid='public.diaries'::regclass and i.indexrelid<>to_regclass('public.diaries_user_date_uq')),
 'foreign_keys',(select coalesce(jsonb_agg(jsonb_build_array(c.oid,pg_get_constraintdef(c.oid)) order by c.oid),'[]'::jsonb)
    from pg_constraint c where c.contype='f' and (c.conrelid='public.diaries'::regclass or c.confrelid='public.diaries'::regclass)
      and c.oid in (select (v->>0)::oid from jsonb_array_elements(current_setting('today_near.before_catalog')::jsonb->'foreign_keys') v)))), false) then raise exception 'STOP: existing FK/index changed'; end if;
end; $postflight$;
do $acl$ begin
    if exists(select 1 from pg_class c cross join (values('anon'),('authenticated')) r(role_name)
        where c.relnamespace='public'::regnamespace and c.relname in ('diary_edit_requests','diary_delete_requests','shop_purchase_receipts','shop_refund_receipts')
        and (not c.relrowsecurity or has_any_column_privilege(r.role_name,c.oid,'INSERT,UPDATE')
             or has_table_privilege(r.role_name,c.oid,'DELETE,TRUNCATE')
             or not has_table_privilege('service_role',c.oid,'SELECT'))) then
        raise exception 'STOP: unexpected RLS/client write/server SELECT permissions';
    end if;
end; $acl$;
do $rpc$ begin
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.member_diary_edit(uuid,uuid,text,jsonb)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='jsonb'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_user_id','p_diary_id','p_action','p_payload']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.member_diary_edit(uuid,uuid,text,jsonb)'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.confirm_member_diary(uuid,text,text,integer,text,text,timestamptz,jsonb,jsonb)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='uuid'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_user_id','p_session_id','p_client_request_id','p_history_revision','p_fingerprint','p_weather','p_confirmed_at','p_ai_draft','p_messages']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.confirm_member_diary(uuid,text,text,integer,text,text,timestamptz,jsonb,jsonb)'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.reserve_diary_drawing(uuid,uuid,text,bigint,integer,integer,text)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='jsonb'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_user_id','p_diary_id','p_mime_type','p_byte_size','p_width','p_height','p_sha256']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.reserve_diary_drawing(uuid,uuid,text,bigint,integer,integer,text)'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.complete_diary_drawing(uuid,uuid,uuid)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='void'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_user_id','p_diary_id','p_asset_id']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.complete_diary_drawing(uuid,uuid,uuid)'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.room_snapshot(uuid,date)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='jsonb'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_member_id','p_on']::text[]
        and l.lanname='sql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.room_snapshot(uuid,date)'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.member_room_command(uuid,text,jsonb)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='jsonb'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_member_id','p_action','p_payload']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.member_room_command(uuid,text,jsonb)'; end if;
    if not coalesce((exists(select 1 from pg_proc p join pg_language l on l.oid=p.prolang
        where p.oid=to_regprocedure('public.attach_diary_comic(uuid,uuid,uuid,text)') and p.prokind='f' and not p.prosecdef
        and p.prorettype='jsonb'::regtype and coalesce(p.proargnames,array[]::text[])=array['p_user_id','p_diary_id','p_preview_id','p_request_id']::text[]
        and l.lanname='plpgsql'
        and 'search_path=""'=any(p.proconfig)
        and not has_function_privilege('anon',p.oid,'EXECUTE')
        and not has_function_privilege('authenticated',p.oid,'EXECUTE')
        and has_function_privilege('service_role',p.oid,'EXECUTE'))), false) then raise exception 'STOP: RPC signature/ACL mismatch: public.attach_diary_comic(uuid,uuid,uuid,text)'; end if;
end; $rpc$;
commit;
