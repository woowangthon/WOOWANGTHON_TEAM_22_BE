# WOOWANGTHON_TEAM_22_BE

오늘,그리고 프로젝트의 BE 저장소입니다. FE와 AI는 각각 별도 Git 저장소에서 관리합니다.

FE → BE → 기존 AI HTTP 서버로 이어지는 최소 텍스트 대화 서버입니다.
[FE·BE API 계약](docs/CHAT_API_CONTRACT.md)은 **팀 합의 전 잠정 계약**입니다.
실제 아동 데이터를 사용하지 마세요. 현재 구현에는 인증·DB·그림 저장·일기 초안·이미지 생성이 없습니다.

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

AI 주소는 HTTP(S) origin으로 지정합니다. BE는 `/api/chat`만 호출하며 자동 재시도하지 않습니다.
세션별 전체 확정 이력을 전달하고 성공한 발언/답변만 함께 보관합니다.
실패한 요청은 확정 이력을 바꾸지 않습니다. 요청 ID별 성공·실패 기록으로 중복 호출을 방지합니다.
연결 전 실패만 같은 ID·본문으로 명시적 재시도가 가능하고, timeout 후 결과 불명 오류는 캐시합니다.
세션 ID는 인증 수단이 아닙니다.

세션과 요청 기록은 단일 프로세스 메모리에만 있습니다. **BE 재시작 시 모두 사라집니다.**
TTL·영구 저장·자동 정리는 없으며 짧은 로컬 개발 검증용입니다.
`--workers 1`을 유지하고 최초 연동 검증에서는 `--reload`를 사용하지 않습니다.
세부 JSON·오류 코드·2,000자/60개/20,000자 한도는 계약 문서를 확인하세요.

### 모의 테스트와 Python 호환성

2026-11-06 Windows CPython 3.14.3의 BE `.venv`에서 설치·import·OpenAPI·테스트를 검증했습니다.
직접 의존성은 FastAPI 0.162.0, Uvicorn 0.54.0, Pydantic 2.14.2, HTTPX 0.28.1,
개발 의존성은 pytest 9.2.0입니다. pydantic-core 2.50.2의 cp314 Windows x64 wheel을 설치했습니다.
`pip check`에 의존성 충돌이 없었습니다. 직접 의존성만 고정하며 전이 의존성 전체 잠금 파일은 아닙니다.

테스트는 HTTPX ASGITransport/MockTransport를 사용하며 외부 네트워크·실제 모델 호출이 없습니다.
ASGI lifespan은 테스트에서 명시적으로 실행합니다. AnyIO pytest 플러그인(HTTPX의 의존성)을 asyncio로 실행합니다.
검증 항목: 앱/health/CORS/설정, 시작/두 번 왕복/전체 이력, 세션 분리, 재전송/충돌/처리 중 중복,
문자열·Unicode·이력 한도, AI 오류 HTTP·잘못된 응답·통신 실패·timeout·취소·기존 이력 보존.

### 별도 AI 프로세스와 실제 HTTP 통합 — 미실행

현재 AI 로컬 작업 트리에는 README만 있고 `server.py`와 실행 환경이 준비되지 않아 실행하지 않았습니다.
AI 저장소를 임의로 checkout/pull/reset하거나 의존성을 설치하지 않습니다.
AI 담당자가 기존 AI HTTP 코드와 **AI 전용** 가상환경을 준비하고 `AI_MODE=mock`을 보장한 뒤에만 진행합니다.
BE의 Python 호환성 결과를 AI SDK·AI 실행 환경의 호환성 결과로 간주하지 않습니다.
외부 실제 모델 검증은 이번 범위에 포함하지 않습니다.

AI 루트의 별도 PowerShell에서, 준비 완료 후:

```powershell
$env:AI_MODE = 'mock'
$env:PORT = '8002'
.\.venv\Scripts\python.exe server.py
```

AI가 응답의 `mock=true`를 반환하는지 확인하고, BE는 앞의 실행 명령으로 별도 터미널에서 실행합니다.
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
```

이 절차는 실제 소켓으로 기존 AI mock 서버에 연결하는 검증입니다.
FE 브라우저 연동·실제 공급자 호출 성공을 뜻하지 않습니다. FE 연결은 FE 담당자의 API 수정 후 별도로 확인합니다.

### 코드 구조

`app/main.py`는 앱/lifespan/CORS, `config.py`는 설정, `schemas.py`는 경계 자료형,
`errors.py`는 공통 오류를 담당합니다. `api/chat.py`는 요청 처리,
`services/chat.py`는 후보 이력/AI 호출/상태 전이, `session_store.py`는 메모리 예약/저장,
`clients/ai.py`는 기존 AI HTTP와 응답 검증을 담당합니다.

### Git 작업 흐름

변경 전에 `git status`와 현재 브랜치를 확인하고 기존 작업을 보존합니다.
이번 최소 BE 구현은 기존 `feat/be-bootstrap`에서 계속 작업합니다. 새 브랜치·푸시·PR 생성·main 병합은 수행하지 않습니다.
작업과 검증을 완료한 뒤 변경 목적 단위로 커밋합니다. 커밋 전에 `git diff --cached`로 스테이징 내용을 확인합니다.
커밋 메시지는 `chore: BE 초기 개발 환경 설정`처럼 적절한 유형과 구체적인 한국어 설명으로 작성합니다.

실제 환경변수 파일(`.env`, `.env.*`), 비밀키와 `.venv`는 Git에서 제외합니다.
공유용 `.env.example`에는 비밀값 없이 변수 이름과 예시만 작성합니다.
`AGENTS.md`는 각 PC의 `.git/info/exclude`에 `/AGENTS.md`를 등록해 로컬 전용으로 관리합니다.
`PROJECT_STATUS.md`도 같은 방식으로 로컬 전용으로 유지하며 커밋하지 않습니다.
