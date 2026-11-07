# WOOWANGTHON_TEAM_22_BE

오늘,그리고 프로젝트의 BE 저장소입니다. FE와 AI는 각각 별도 Git 저장소에서 관리합니다.

FE → BE → 기존 AI HTTP 서버로 이어지는 최소 텍스트 대화·일기 초안 서버입니다.
[FE·BE API 계약](docs/CHAT_API_CONTRACT.md)은 **팀 합의 전 잠정 계약**입니다.
실제 아동 데이터를 사용하지 마세요. 현재 구현에는 인증·DB·그림 저장·최종 일기 확인/저장·이미지 생성이 없습니다.

회원별 그림일기의 [Supabase DB 스키마·RLS 초안](docs/DIARY_DB_SCHEMA.md)과
[비공개 Storage 준비·접근 정책](docs/DIARY_STORAGE_POLICY.md)을 별도로 작성했습니다.
`supabase/migrations/`의 SQL은 **미적용 검토용**이며 현재 API에 연결되어 있지 않습니다.
적용 전 확인 순서와 사용자/날짜/원본 보존의 미정 사항은 두 문서를 확인하세요.

## 개발 환경

- Windows / VS Code / PowerShell
- 초기 설정에서 실행을 확인한 Python: **3.14.3** (프로젝트의 필수 버전을 고정한 것은 아닙니다.)
- Python 실행과 패키지 설치에는 BE 저장소 루트의 `.venv`를 사용합니다.

### 가상환경 생성과 활성화

BE 저장소 루트에서 실행합니다. 정상 동작하는 `.venv`가 이미 있으면 재사용합니다.

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -c "import sys; print(sys.executable); print(sys.version)"
```

활성화 후 VS Code의 `Python: Select Interpreter`에서 `.venv\Scripts\python.exe`를 선택합니다(Python 확장 필요).

PowerShell 실행 정책 때문에 활성화가 막혀도 가상환경의 Python을 직접 실행할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable); print(sys.version)"
.\.venv\Scripts\python.exe -m pip --version
```

패키지 설치도 `.\.venv\Scripts\python.exe -m pip install <패키지명>`처럼 가상환경의 Python을 통해 실행합니다.

### 의존성 설치와 서버 실행

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q
$env:AI_BASE_URL = 'http://127.0.0.1:8002'
$env:ALLOWED_ORIGINS = 'http://127.0.0.1:5500,http://localhost:5500'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

`GET http://127.0.0.1:8000/health`는 `{"status":"ok"}`를 반환합니다.
AI 서버 상태를 검사하는 경로는 아닙니다. API 문서는 `/docs`에서 확인합니다.
`.env.example`은 설정 참고용이며 `.env`를 자동 로딩하지 않습니다. PowerShell 환경변수를 사용하세요.
타임아웃 기본값은 연결 3초, 읽기 130초, 쓰기 10초, 풀 대기 3초, 전체 AI 대기 135초입니다.
각 이름은 `.env.example`에 있으며 0·음수·무한대는 허용하지 않습니다.
개발 CORS는 지정한 origin만 허용하고 자격증명 전송을 허용하지 않습니다.
`file://` 대신 정적 서버를 사용하고 FE 주소와 `ALLOWED_ORIGINS`를 맞추세요.

### API와 저장 방식

| API | 기능 |
| --- | --- |
| GET /health | BE 생존 확인 |
| POST /api/chat-sessions | `{}`와 Idempotency-Key로 첫 질문/세션 생성 |
| POST /api/chat-sessions/{session_id}/messages | client_message_id와 text를 보내 대화 진행 |
| POST /api/chat-sessions/{session_id}/diary-draft | client_request_id와 history_revision으로 기존 확정 이력의 일기 초안 생성 |

AI 주소는 HTTP(S) origin으로 지정합니다. BE는 `/api/chat`, `/api/diary`를 호출하며 자동 재시도하지 않습니다.
세션별 전체 확정 이력을 전달하고 성공한 발언/답변만 함께 보관합니다.
실패한 요청은 확정 이력을 바꾸지 않습니다. 요청 ID별 성공·실패 기록으로 중복 호출을 방지합니다.
연결 전 실패만 같은 ID·본문으로 명시적 재시도가 가능하고, timeout 후 결과 불명 오류는 캐시합니다.
세션 ID는 인증 수단이 아닙니다.

세션과 요청 기록은 단일 프로세스 메모리에만 있습니다. **BE 재시작 시 모두 사라집니다.**
TTL·영구 저장·자동 정리는 없으며 짧은 로컬 개발 검증용입니다.
`--workers 1`을 유지하고 최초 연동 검증에서는 `--reload`를 사용하지 않습니다.
세부 JSON·오류 코드·2,000자/60개/20,000자 한도는 계약 문서를 확인하세요.

대화 시작/발언 응답의 `history_revision`은 시작 시 0, 발언+AI 답변 확정마다 1씩 증가합니다.
초안 요청 예시(발언 1회 성공 후):

```http
POST /api/chat-sessions/{session_id}/diary-draft
Content-Type: application/json

{"client_request_id":"draft-demo-001","history_revision":1}
```

200 응답은 `{session_id, client_request_id, history_revision, draft}`이며 draft에
`draft_id, status:"draft", text, pages, sources, fallback, requires_confirmation:true, mock`이 있습니다.
FE는 messages를 제출하지 않습니다. BE가 첫 질문부터 마지막 확정 답변까지 전체 이력을 AI에 보냅니다.
`sources[].id`와 `pages[].source_ids`는 해당 이력의 0부터 시작하는 인덱스이며 BE 메시지 ID가 아닙니다.
아이 발언이 없으면 422, 대화 버전이 다르면 409입니다. 같은 요청 ID+버전 재전송은 캐시를 복구합니다.
초안 생성과 발언은 같은 세션에서 동시에 진행할 수 없습니다. timeout 이후 같은 ID로 AI를 재호출하지 않습니다.
초안은 메모리 재전송 캐시일 뿐이며 **최종 일기·영구 저장·사용자 확인 완료가 아닙니다.**

### 모의 테스트와 Python 호환성

2026-11-06 Windows CPython 3.14.3의 BE `.venv`에서 설치·import·OpenAPI·테스트를 검증했습니다.
직접 의존성은 FastAPI 0.162.0, Uvicorn 0.54.0, Pydantic 2.14.2, HTTPX 0.28.1,
개발 의존성은 pytest 9.2.0입니다. pydantic-core 2.50.2의 cp314 Windows x64 wheel을 설치했습니다.
`pip check`에 의존성 충돌이 없었습니다. 직접 의존성만 고정하며 전이 의존성 전체 잠금 파일은 아닙니다.

테스트는 HTTPX ASGITransport/MockTransport를 사용하며 외부 네트워크·실제 모델 호출이 없습니다.
ASGI lifespan은 테스트에서 명시적으로 실행합니다. AnyIO pytest 플러그인(HTTPX의 의존성)을 asyncio로 실행합니다.
검증 항목: 앱/health/CORS/설정, 시작/두 번 왕복/전체 이력, 세션 분리, 재전송/충돌/처리 중 중복,
문자열·Unicode·이력 한도, AI 오류 HTTP·잘못된 응답·통신 실패·timeout·취소·기존 이력 보존.
일기 초안의 전체 확정 이력/버전, 아이 발언 유무, 출처 인덱스/원문, 엄격한 응답 검사,
중복/대화와의 양방향 충돌, 실패 후 기존 초안·대화 보존도 검사합니다.
최종 실행 결과: **179 passed**, 기존 70개 회귀 포함(일기 관련 109개 추가), 경고 없음.

### 기존 AI mock 서버와 실제 HTTP 검증

2026-11-06 기존 `127.0.0.1:8002/api/status`에서 `mode=mock`을 확인하고,
BE ASGI → 별도 AI 프로세스의 실제 HTTP 대화 3회(시작+발언 2회)·초안 1회를 검증했습니다.
전체 확정 메시지 5개, 출처 `[1,3]`, 아이 발언 없는 세션 거절, 같은 초안 재전송 시 추가 AI 호출 없음도 확인했습니다.
아래 스크립트는 BE를 인프로세스로 실행합니다. 별도 BE Uvicorn 소켓·제품 FE 브라우저 연동 검증과 구분합니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_mock_diary
```

스크립트는 먼저 `/api/status`를 조회하고 서버 미실행·오류·mock 이외 모드면 생성 요청 없이 종료합니다.
서버를 시작하거나 AI 저장소를 수정/설치하지 않습니다. 실제 Groq·Cloudflare 등 모델을 호출하지 않습니다.
서버가 없을 때는 AI 담당자가 기존 코드와 **AI 전용** 가상환경을 준비하고 다음처럼 실행한 뒤 재검증합니다.
BE의 Python 호환성 결과를 AI SDK·AI 실행 환경의 호환성 결과로 간주하지 않습니다.

AI 루트의 별도 PowerShell에서, 준비 완료 후:

```powershell
$env:AI_MODE = 'mock'
$env:PORT = '8002'
.\.venv\Scripts\python.exe server.py
```

`GET http://127.0.0.1:8002/api/status`의 `mode=mock`을 **생성 요청 전에** 확인하고,
BE는 앞의 실행 명령으로 별도 터미널에서 실행합니다.
세 번째 PowerShell에서 가상 문장만 사용해 두 번 왕복합니다.

```powershell
$beUrl = 'http://127.0.0.1:8000'
$startKey = [guid]::NewGuid().ToString('N')
$session = Invoke-RestMethod -Method Post -Uri "$beUrl/api/chat-sessions" -ContentType 'application/json' -Headers @{'Idempotency-Key'=$startKey} -Body '{}'
if (-not $session.mock) { throw 'AI를 mock 모드로 준비한 뒤 다시 검증하세요.' }
$sessionId = $session.session_id
$firstBody = @{client_message_id=[guid]::NewGuid().ToString('N'); text='가상 인물과 공놀이한 이야기야.'} | ConvertTo-Json -Compress
$firstBytes = [Text.Encoding]::UTF8.GetBytes($firstBody)
$firstReply = Invoke-RestMethod -Method Post -Uri "$beUrl/api/chat-sessions/$sessionId/messages" -ContentType 'application/json; charset=utf-8' -Body $firstBytes
$secondBody = @{client_message_id=[guid]::NewGuid().ToString('N'); text='가상 인물은 즐거웠다고 했어.'} | ConvertTo-Json -Compress
$secondReply = Invoke-RestMethod -Method Post -Uri "$beUrl/api/chat-sessions/$sessionId/messages" -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($secondBody))
$replayed = Invoke-RestMethod -Method Post -Uri "$beUrl/api/chat-sessions/$sessionId/messages" -ContentType 'application/json; charset=utf-8' -Body $firstBytes
if ($replayed.reply.message_id -ne $firstReply.reply.message_id) { throw '중복 응답 ID 불일치' }
if (-not $firstReply.mock -or -not $secondReply.mock) { throw 'mock 모드 확인 필요' }
$session, $firstReply, $secondReply, $replayed | ConvertTo-Json -Depth 5
$draftBody = @{client_request_id=[guid]::NewGuid().ToString('N'); history_revision=$secondReply.history_revision} | ConvertTo-Json -Compress
$draftBytes = [Text.Encoding]::UTF8.GetBytes($draftBody)
$draft = Invoke-RestMethod -Method Post -Uri "$beUrl/api/chat-sessions/$sessionId/diary-draft" -ContentType 'application/json; charset=utf-8' -Body $draftBytes
$draftReplay = Invoke-RestMethod -Method Post -Uri "$beUrl/api/chat-sessions/$sessionId/diary-draft" -ContentType 'application/json; charset=utf-8' -Body $draftBytes
if ($draft.draft.draft_id -ne $draftReplay.draft.draft_id) { throw '초안 재전송 ID 불일치' }
if (-not $draft.draft.mock -or -not $draft.draft.requires_confirmation) { throw '확인 전 mock 초안이어야 합니다.' }
$draft | ConvertTo-Json -Depth 8
```

이 수동 절차는 별도 BE와 기존 AI mock 서버에 실제 소켓으로 연결하는 재현 안내이며 이번에는 실행하지 않았습니다.
FE 브라우저 연동·실제 공급자 호출 성공을 뜻하지 않습니다. FE 연결은 FE 담당자의 API 수정 후 별도로 확인합니다.

미검증/팀 합의 사항: 제품 FE 연결, 실제 모델의 품질·운영 timeout, 종료/재생성 UX,
과거 버전 초안 표시·최종 일기 확인/수정/저장 API, 인증·소유권·TTL·DB 정책.
현재는 finished 여부와 무관하게 아이 발언이 있으면 명시적 초안 요청을 허용하고,
새 ID로 같은 버전을 명시적으로 재생성할 수 있습니다. 이 구현 정책은 팀 승인 전 잠정안입니다.

### 코드 구조

`app/main.py`는 앱/lifespan/CORS, `config.py`는 설정, `schemas.py`는 경계 자료형,
`errors.py`는 공통 오류를 담당합니다. `api/chat.py`는 요청 처리,
`services/chat.py`는 후보 이력/AI 호출/상태 전이, `session_store.py`는 메모리 예약/저장,
`clients/ai.py`는 기존 AI HTTP와 응답 검증을 담당합니다.

### Git 작업 흐름

변경 전에 `git status`와 현재 브랜치를 확인하고 기존 작업을 보존합니다.
이번 최소 BE 구현은 기존 `feat/be-bootstrap`에서 계속 작업합니다. 새 브랜치·푸시·PR 생성·main 병합은 수행하지 않습니다.
작업과 검증을 완료한 뒤 변경 목적 단위로 커밋합니다. 커밋 전에 `git diff --cached`로 스테이징 내용을 확인합니다.
커밋 메시지는 `일기 초안 API와 세션 처리 구현`처럼 접두사 없이 짧고 명확한 한국어로 작성합니다.

실제 환경변수 파일(`.env`, `.env.*`), 비밀키와 `.venv`는 Git에서 제외합니다.
공유용 `.env.example`에는 비밀값 없이 변수 이름과 예시만 작성합니다.
`AGENTS.md`는 각 PC의 `.git/info/exclude`에 `/AGENTS.md`를 등록해 로컬 전용으로 관리합니다.
`PROJECT_STATUS.md`도 같은 방식으로 로컬 전용으로 유지하며 커밋하지 않습니다.
