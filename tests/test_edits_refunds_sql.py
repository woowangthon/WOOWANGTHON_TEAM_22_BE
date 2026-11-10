"""SQL 후보 경계의 정적 검사. PostgreSQL 실행으로 표시하지 않는다."""
from pathlib import Path
import re

SQL = (Path(__file__).resolve().parents[1] / 'supabase/candidates/diary_edits_refunds_v04.sql').read_text(encoding='utf-8')
CODE = re.sub(r'--[^\n]*', '', SQL).lower()






def test_atomic_finalization_receipt_and_active_revision_and_server_only_access():
    assert 'unique(user_id, client_request_id)' in CODE
    assert 'references public.diary_edit_requests(request_id, diary_id, user_id)' in CODE
    command = CODE.split('create function public.member_diary_edit')[1].split('$function$;')[0]
    assert command.index('for update') < command.index("set state='completed'")
    assert command.index("r.state='completed'") < command.index('d.edit_version <> r.expected_version')
    assert 'active_drawing_edit_id=case when r.drawing is not null then r.request_id' in command
    assert "from public,anon,authenticated" in CODE and 'to service_role' in CODE
    assert 'security definer' not in CODE and not re.search(r'grant[^;]*(?:insert|update|delete)[^;]*to authenticated', CODE)






def test_candidate_has_no_seed_or_session_migration_and_preserves_old_drawing_uniqueness():
    assert not re.search(r'insert into public\.shop_items|\btruncate\b|\bcommit\b', CODE)
    assert 'public.chat_sessions' not in CODE
    assert 'diary_assets_scene_uq' not in CODE
    assert "'/original_drawing/'" in CODE
