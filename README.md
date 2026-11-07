# WOOWANGTHON_TEAM_22_BE

오늘,그리고 프로젝트의 BE 저장소입니다. FE와 AI는 각각 별도 Git 저장소에서 관리합니다.

FE → BE → 기존 AI HTTP 서버로 이어지는 최소 텍스트 대화·일기 초안 서버입니다.
아래 FE·BE API 계약은 **팀 합의 전 잠정 계약**입니다.
Supabase 비익명 회원 토큰 인증과 회원별 세션·초안 소유권 검사 지원. 실제 아동 데이터 사용 금지.
DB·그림 저장·최종 일기 확인/저장·이미지 생성은 미구현.

회원별 그림일기 SQL 001·002는 사용자 확인 기준 적용 완료. 현재 API의 DB 연결은 미구현.
스키마·보안 요약과 적용 전 확인 순서는 아래에 있습니다.

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
$env:SUPABASE_URL = 'https://nnbmxtiouvsuszretahg.supabase.co'
$env:SUPABASE_PUBLISHABLE_KEY = Read-Host 'Supabase Publishable key'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

`GET http://127.0.0.1:8000/health`는 `{"status":"ok"}`를 반환합니다.
AI 서버 상태를 검사하는 경로는 아닙니다. API 문서는 `/docs`에서 확인합니다.
`.env.example`은 설정 참고용이며 `.env`를 자동 로딩하지 않습니다. PowerShell 환경변수를 사용하세요.
타임아웃 기본값은 연결 3초, 읽기 130초, 쓰기 10초, 풀 대기 3초, 전체 AI 대기 135초입니다.
환경변수는 아래와 같으며 timeout에 0·음수·무한대는 허용하지 않습니다.

| 환경변수 | 기본값/의미 |
| --- | --- |
| AI_BASE_URL | `http://127.0.0.1:8002`, 인증정보 없는 HTTP(S) origin |
| ALLOWED_ORIGINS | `http://127.0.0.1:5500,http://localhost:5500`, 쉼표로 구분 |
| AI_CONNECT_TIMEOUT_SECONDS | 3, 연결 |
| AI_READ_TIMEOUT_SECONDS | 130, 읽기 |
| AI_WRITE_TIMEOUT_SECONDS | 10, 쓰기 |
| AI_POOL_TIMEOUT_SECONDS | 3, 풀 대기 |
| AI_REQUEST_TIMEOUT_SECONDS | 135, 전체 AI 요청 |
| SUPABASE_URL | `https://nnbmxtiouvsuszretahg.supabase.co`, 인증정보 없는 HTTPS origin |
| SUPABASE_PUBLISHABLE_KEY | 필수, `sb_publishable_` 형식만 허용. Secret/service_role/JWT 형식 키 금지 |
| AUTH_REQUEST_TIMEOUT_SECONDS | 10, Auth HTTP 및 전체 요청 제한 |

변수 예시는 Git에서 공유하는 [.env.example](.env.example)에도 있습니다.
개발 CORS는 지정한 origin만 허용하고 자격증명 전송을 허용하지 않습니다.
허용 헤더: `Authorization`, `Content-Type`, `Idempotency-Key`, `X-Request-ID`.
응답 `X-Request-ID`는 BE 생성값이며 기존과 동일하게 브라우저에 노출.
`file://` 대신 정적 서버를 사용하고 FE 주소와 `ALLOWED_ORIGINS`를 맞추세요.

### API와 저장 방식

세 POST API 모두 `Authorization: Bearer <Supabase access token>` 필수. `GET /health` 공개 유지.
요청 본문·성공 응답 필드와 기존 요청 ID/버전 규칙 유지. `user_id` 등 추가 필드는 기존대로 422.

### Supabase 회원 인증

2026-11-07 대상 프로젝트의 공개 `/auth/v1/.well-known/jwks.json` 조회 결과: ES256 공개키 1개.
공개키 존재는 현재 발급 토큰 전부의 알고리즘을 확정하는 근거는 아니므로 Dashboard의 활성 키도 수동 확인.
JWKS 로컬 검증은 서명·고정 issuer·audience·만료·role 검증과 키 교체/캐시 관리가 필요.
이번 선택은 [공식 Auth `/user` 검증](https://supabase.com/docs/guides/auth/jwts#verifying-with-a-shared-secret-signing-key).
[공식 getUser 안내](https://supabase.com/docs/reference/javascript/auth-getuser)의 서버 재검증·신뢰 가능한 회원 조회와 같은 경로.
매 요청마다 고정된 `SUPABASE_URL/auth/v1/user`에 Publishable key와 회원 Bearer 토큰 전달.
Auth 서버에 서명·토큰 유효성·만료 검증 위임 후, 200 응답의 UUID와
`is_anonymous=false`, `role=authenticated`, `aud=authenticated` 모두 확인.
최신 회원 정보 확인과 ES256/레거시 키 교체 대응, 기존 RLS 도구와 같은 검증 경로가 선택 근거.
JWT 자체 구현·서명 없는 디코딩·관리자 키·인증 성공 캐시·자동 재시도 없음.
대신 매 요청의 Auth 통신 지연/장애 의존성 존재. 운영 환경은 Supabase와 가까운 리전 권장.
설정 누락 시 health는 공개하되 보호 API는 503 `AUTH_NOT_CONFIGURED`로 차단.

| HTTP | 추가 오류 코드 | 의미 / retryable |
| --- | --- | --- |
| 401 | AUTHENTICATION_FAILED | 토큰 없음·형식 오류·Auth 400/401/403(무효·만료 포함), false, `WWW-Authenticate: Bearer` |
| 403 | ACCESS_DENIED | 검증된 회원의 익명 여부·역할·audience 조건 불충족, false |
| 404 | SESSION_NOT_FOUND | 없는 세션·타 회원 세션 모두 같은 코드/메시지, false |
| 503 | AUTH_UNAVAILABLE | 연결 실패·timeout·Auth 429/5xx, true |
| 502 | AUTH_INVALID_RESPONSE | 잘못된 JSON/UUID·예상 밖 상태·redirect, false |
| 503 | AUTH_NOT_CONFIGURED | Publishable key 설정 누락, false |

공통 `{error:{code,message,retryable},request_id}`와 no-store 유지. 공급자 오류/토큰 원문 반환 금지.
기존 세션 오류 코드는 유지하고 메시지만 `세션 없음`, `요청 처리 중`, `요청 ID 충돌` 등으로 축약.
FE는 무효·만료 토큰을 새로 로그인/갱신한 뒤 재요청. BE는 Refresh Token 취급 없음.
Auth 연결 오류는 인증/세션 예약 이전 실패이므로 같은 요청 ID로 명시적 재시도 가능.

### 실제 Supabase Auth 수동 검증

자동 테스트는 MockTransport의 가짜 Auth 응답·가짜 토큰만 사용. 실제 토큰 서명·만료 판정은
Supabase에 위임하므로 아래 실연동 확인 필요. 이번 작업에서 실제 회원 로그인·모델 호출은 미실행.

1. Dashboard에서 대상 프로젝트·활성 JWT signing key·Publishable key 확인. 현재 공개 JWKS는 ES256.
   기존 RLS 검증 도구의 HTTPS 주소·Publishable key 제한·`/user` 재검증 방식을 재사용한 설계.
   대화형 Auth/RLS 도구 자체는 DB 조회도 포함하므로 BE API 인증만 확인할 때는 실행 불필요.
2. 기존 AI의 `/api/status`에서 `mode=mock` 확인 후 위 실행 명령으로 BE 실행. 실모델 모드면 중단.
   이 절차에서는 `verify_mock_diary` 사용 금지: 해당 스크립트의 Auth는 가짜 서버.
3. 별도 브라우저 프로필 A/B로 준비된 비익명 테스트 회원 로그인.
   기존 Supabase 로그인 흐름에서 얻은 Access Token만 각 로컬 `/docs`의 **Authorize**에 입력.
   비밀번호·토큰을 채팅·소스·명령 인수·파일·공유 로그에 기록하지 말고 작업 후 Authorize 해제.
4. A/B 각각 같은 `Idempotency-Key`로 `{}` 전송: 서로 다른 세션과 201 확인.
   각각 발언 2회 후 revision=2 초안 요청. 동일한 메시지/초안 ID 재전송 결과·revision 보존 확인.
5. A로 B 세션의 메시지/초안, B로 A 세션의 기존 캐시 ID 요청: 모두 404 `SESSION_NOT_FOUND` 확인.
   소유 회원으로 다시 요청해 이력·초안 보존 확인.
6. Authorize 해제·변조 토큰·갱신하지 않은 실제 만료 토큰·다른 프로젝트 토큰으로 401 확인.
   익명 회원 토큰은 403 확인. Auth 연결 차단 환경에서는 503 `AUTH_UNAVAILABLE`,
   공개 health는 200 확인. 오류 요청 동안 AI mock 호출과 이력 변경 없음 확인.
7. BE 재시작 후 기존 세션은 404. 이 절차는 DB/RLS/Storage 검증 결과와 구분.

### 영구 저장·배포 후속 작업

단일 프로세스 메모리 구현 유지. 재시작·reload 시 세션/이력/초안/중복 기록 소실, TTL·quota 없음.
다중 worker·다중 인스턴스 전환 전 공유 DB에 회원 UUID/소유권·확정 이력·revision·요청 결과 저장 필요.
회원별 시작 키와 세션별 요청 ID의 고유 제약, 예약/완료 트랜잭션·락·실패 복구·보존/삭제 정책 필요.
배포에서는 HTTPS·정확한 FE origin·Publishable key 환경 설정·Auth 리전/지연/호출 제한·로그 마스킹 확인.
현재 일반 회원 DB 쓰기 차단 정책에 맞춘 최소 권한 저장 경로도 별도 설계 대상. 관리자 키 추가 없음.

### 대화·초안 계약

세션 생성 시 Auth 서버가 확인한 회원 UUID를 서버 내부 `owner_id`로 기록.
시작 키는 `(회원 UUID, Idempotency-Key)`로 분리. 다른 회원의 동일 키는 별도 세션 생성.
메시지·초안은 소유권 확인 후에만 중복 기록·busy·대화 버전 검사 수행.
기존 성공 캐시·실패 캐시·처리 중 요청도 타 회원에게는 모두 `404 SESSION_NOT_FOUND`.
인증 실패·타인 접근 시 AI 호출·세션/이력/버전/초안/요청 기록 변경 없음.
세션별 메시지/초안 ID namespace와 같은 회원 재전송 복구 규칙 유지.

| API | 기능 |
| --- | --- |
| GET /health | BE 생존 확인 |
| POST /api/chat-sessions | `{}`와 Idempotency-Key로 첫 질문/세션 생성 |
| POST /api/chat-sessions/{session_id}/messages | client_message_id와 text를 보내 대화 진행 |
| POST /api/chat-sessions/{session_id}/diary-draft | client_request_id와 history_revision으로 기존 확정 이력의 일기 초안 생성 |

시작 요청은 `Idempotency-Key` 헤더와 JSON `{}`를 보내며 성공 시 201입니다.
발언 요청은 `{"client_message_id":"turn-demo-001","text":"가상 인물과 공놀이했어."}`이고 성공 시 200입니다.
두 응답은 `{session_id,history_revision,reply:{message_id,role:"assistant",text},finished,mock,fallback}`이며,
발언 응답에는 `client_message_id`가 추가됩니다.
시작 키·발언/초안 요청 ID는 ASCII 영문·숫자·밑줄·하이픈 1~128자입니다.
요청은 JSON/UTF-8이며 정의하지 않은 필드는 422로 거절합니다.
응답은 `Cache-Control: no-store`와 `X-Request-ID`를 포함합니다.

AI 주소는 HTTP(S) origin으로 지정합니다. BE는 `/api/chat`, `/api/diary`를 호출하며 자동 재시도하지 않습니다.
세션별 전체 확정 이력을 전달하고 성공한 발언/답변만 함께 보관합니다.
실패한 요청은 확정 이력을 바꾸지 않습니다. 요청 ID별 성공·실패 기록으로 중복 호출을 방지합니다.
연결 전 실패만 같은 ID·본문으로 명시적 재시도가 가능하고, timeout 후 결과 불명 오류는 캐시합니다.
세션 ID는 인증 수단이 아닙니다.

세션과 요청 기록은 단일 프로세스 메모리에만 있습니다. **BE 재시작 시 모두 사라집니다.**
TTL·영구 저장·자동 정리는 없으며 짧은 로컬 개발 검증용입니다.
`--workers 1`을 유지하고 최초 연동 검증에서는 `--reload`를 사용하지 않습니다.
발언은 공백뿐인 값·잘못된 Unicode를 제외한 1~2,000자이며 원문을 보존합니다.
전체 이력은 60개/20,000자 이내이고 AI 답변 150자 여유를 확보하므로 최대 29회 왕복입니다.

대화 시작/발언 응답의 `history_revision`은 시작 시 0, 발언+AI 답변 확정마다 1씩 증가합니다.
초안 요청 예시(발언 1회 성공 후):

```http
POST /api/chat-sessions/{session_id}/diary-draft
Authorization: Bearer <Supabase access token>
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

초안 `pages`는 `{text,source_ids,imageUrl:null}` 1~6개, `sources`는 `{id,text}` 목록입니다.
본문/페이지 글은 최대 20,000자이며 출처는 전체 이력의 child 원문·순서와 일치해야 합니다.
`fallback`, `mock`이 true여도 확인 전 정상 초안일 수 있습니다.
현재는 `finished=true`를 요구하지 않으며 새 요청 ID로 같은 버전을 명시적으로 재생성할 수 있습니다.
FE는 늦게 도착한 과거 버전 응답으로 최신 화면을 덮어쓰지 않아야 합니다.

오류는 `{error:{code,message,retryable},request_id}` 형식입니다.
주요 오류는 404 `SESSION_NOT_FOUND`, 409 `IDEMPOTENCY_CONFLICT`/`REQUEST_IN_PROGRESS`/
`SESSION_BUSY`/`HISTORY_REVISION_CONFLICT`, 422 `NO_CHILD_MESSAGES`/`CONVERSATION_LIMIT_REACHED`입니다.
같은 ID·같은 입력의 성공 재전송은 캐시를 복구합니다. 연결 전 실패인 `AI_CONNECTION_FAILED`만
같은 ID로 명시적 재시도하며, timeout/전송 후 결과 불명인 `AI_OUTCOME_UNKNOWN`은 자동 재시도하거나
새 키로 우회하지 않습니다. 공급자 오류는 공통 502/503/504 오류로 변환합니다.

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
회원 A/B 모의 Auth 서버·가짜 토큰으로 회원별 시작/메시지/초안 분리,
타 회원의 신규/캐시/실패/처리 중 세션 접근 차단, 인증 실패 시 이력 보존 검증 추가.
2026-11-07 BE `.venv` 전체 결과: **345 passed**, 기존 RLS 도구 88개/BE 회귀 179개 포함.

### 기존 AI mock 서버와 실제 HTTP 검증

2026-11-06 기존 `127.0.0.1:8002/api/status`에서 `mode=mock`을 확인하고,
BE ASGI → 별도 AI 프로세스의 실제 HTTP 대화 3회(시작+발언 2회)·초안 1회를 검증했습니다.
전체 확정 메시지 5개, 출처 `[1,3]`, 아이 발언 없는 세션 거절, 같은 초안 재전송 시 추가 AI 호출 없음도 확인했습니다.
아래 스크립트는 BE를 인프로세스로 실행합니다. 별도 BE Uvicorn 소켓·제품 FE 브라우저 연동 검증과 구분합니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_mock_diary
```

스크립트는 먼저 `/api/status`를 조회하고 서버 미실행·오류·mock 이외 모드면 생성 요청 없이 종료합니다.
인증 부분은 스크립트 내부 MockTransport와 가짜 회원 토큰만 사용. 실제 Supabase Auth 연동 검증과 구분.
서버를 시작하거나 AI 저장소를 수정/설치하지 않습니다. 실제 Groq·Cloudflare 등 모델을 호출하지 않습니다.
서버가 없을 때는 AI 담당자가 기존 코드와 **AI 전용** 가상환경을 준비하고 다음처럼 실행한 뒤 재검증합니다.
BE의 Python 호환성 결과를 AI SDK·AI 실행 환경의 호환성 결과로 간주하지 않습니다.

AI 루트의 별도 PowerShell에서, 준비 완료 후:

```powershell
$env:AI_MODE = 'mock'
$env:PORT = '8002'
.\.venv\Scripts\python.exe server.py
```

별도 BE 소켓을 수동 확인하려면 먼저 `GET http://127.0.0.1:8002/api/status`의 `mode=mock`을 확인하고,
앞의 BE 실행 명령으로 서버를 시작한 뒤 `/docs`에서 시작 → 발언 → 일기 초안 순서로 가상 데이터를 전송합니다.
위 스크립트 검증과 별도 BE 소켓·제품 FE 브라우저·실제 공급자 검증은 구분합니다.
FE 연결은 FE 담당자의 API 수정 후 별도로 확인합니다.

미검증/팀 합의 사항: 제품 FE 연결, 실제 모델의 품질·운영 timeout, 종료/재생성 UX,
과거 버전 초안 표시·최종 일기 확인/수정/저장 API, TTL·영속 DB 저장 정책.
현재는 finished 여부와 무관하게 아이 발언이 있으면 명시적 초안 요청을 허용하고,
새 ID로 같은 버전을 명시적으로 재생성할 수 있습니다. 이 구현 정책은 팀 승인 전 잠정안입니다.

### 코드 구조

`app/main.py`는 앱/lifespan/CORS, `config.py`는 설정, `schemas.py`는 경계 자료형,
`errors.py`는 공통 오류를 담당합니다. `api/chat.py`는 요청 처리,
`services/chat.py`는 후보 이력/AI 호출/상태 전이, `session_store.py`는 메모리 예약/저장,
`clients/ai.py`는 기존 AI HTTP와 응답 검증을 담당합니다.
`auth.py`는 Bearer 의존성, `clients/auth.py`는 Supabase Auth 검증과 회원 UUID 반환 담당.

### Supabase DB·Storage 스키마와 정책

다음 SQL 2개는 Git 추적을 유지합니다. 2026-11-07 사용자 확인 기준 `today-and-near`에 001·002 적용,
회원별 RLS·Storage 정책 생성, 비익명 테스트 회원 A/B와 Publishable key 준비가 완료되었습니다.
에이전트가 원격 적용 상태를 다시 조회하거나 실제 회원 로그인을 수행한 결과는 아닙니다. 현재 BE API의 DB 저장 연동은 미구현입니다.
SQL 주석의 `docs/*.md`는 로컬 상세 문서 안내입니다. 새 clone에서도 필요한 공유 정보는 이 절에 정리합니다.

| 파일 | 목적 |
| --- | --- |
| [20261107000100_create_member_diaries.sql](supabase/migrations/20261107000100_create_member_diaries.sql) | 테이블 3개, 제약·인덱스·권한·회원별 RLS |
| [20261107000200_private_diary_storage_policies.sql](supabase/migrations/20261107000200_private_diary_storage_policies.sql) | 비공개 Storage 접근 정책 |

`diaries`는 `auth.users.id` 기반 회원, 서울 기준 `date`, 날씨, 확인한 글/시각, 출처 세션·버전·요청,
선택한 AI 초안 JSON을 보관합니다. `diary_messages`는 원본 role/text/0-based position을,
`diary_assets`는 원본 그림·향후 만화 장면의 파일 키/MIME/크기/해상도/SHA-256을 보관합니다.
회원·날짜당 한 권 고유 제약은 초기 후보이며 자식 복합 FK는 부모 회원까지 일치시킵니다.
파일 바이트·base64·공개/서명 URL은 DB에 보관하지 않습니다.
파일 크기 메타데이터는 **1바이트 이상, 10 MiB(10,485,760바이트) 이하**로 1 MiB 미만 그림도 허용합니다.
실제 JPEG·PNG 디코딩과 파일 유효성 검사는 향후 BE 업로드 코드가 수행하며 현재는 미구현입니다.

DB는 `auth.uid()` 일치와 `is_anonymous='false'` 조건으로 일반 회원의 본인 조회만 허용합니다.
일반 회원의 직접 INSERT·수정·삭제와 미인증·익명 로그인·타 회원 조회는 차단합니다.
Supabase Auth 인증과 메모리 세션·초안 소유권 검사 구현 완료. DB 영구 저장 경로는 미구현.
회원 JWT를 전달하는 것만으로 DB 쓰기는 열리지 않으며 최소 권한 쓰기 경로를 별도로 설계해야 합니다.
토큰 검증과 DB 권한 검증은 별개입니다. FE 요청 user_id를 신뢰하지 않고 검증한 회원 ID를 사용합니다.
service_role/관리자 연결은 RLS를 우회할 수 있으므로 서버의 인증·소유권·내용 검증을 대신하지 못합니다.
관리자 API 키는 서버 전용으로 유지하며 FE에 노출하지 않습니다.
실제 키·DB 비밀번호는 코드·SQL·문서·로그에 기록하지 않습니다.

아래는 새 개발 환경 준비와 후속 구현의 참고 순서입니다. 현재 프로젝트의 적용 상태는 위 사용자 확인 기록을 따릅니다.

1. 회원/복수 아이 모델, 하루 한 권, 서울 날짜 경계·과거/미래 입력, 수정·삭제·탈퇴·원본 보존 기간을 합의합니다.
2. 대상 프로젝트·DB 버전·백업·기존 이름 충돌·권한/RLS와 Auth 함수를 확인하고 로그인·세션 소유권·저장/복구를 구현합니다.
3. 별도 개발 환경에서 `20261107000100_create_member_diaries.sql`을 먼저 검증·적용합니다.
4. Storage API/Dashboard에서 `diary-private`을 **private (`public=false`)**로 준비합니다.
   JPEG/PNG, 최대 10 MiB는 후보 설정입니다. 키는 `{user_id}/{diary_id}/{asset_kind}/{asset_id}.jpg` 또는 `.png`입니다.
5. `20261107000200_private_diary_storage_policies.sql`을 검증·적용합니다. 버킷이 없거나 private가 아니면 중단합니다.
   본인 원본/만화 조회만 허용하고 직접 업로드·덮어쓰기·이동·삭제를 차단합니다. 다른 버킷의 기존 권한은 유지합니다.
   CLI는 pending 파일을 함께 적용하므로 중간 버킷 단계가 필요하면 별도 적용 디렉터리에서 001/002를 단계별로 준비합니다.
6. 가상 회원 A/B로 본인 조회·타인 접근을 검증합니다. 이번 도구는 조회만 수행하며 쓰기/업로드 차단과 Storage 정책 조합은 별도 후속 검증입니다.
   BE 저장 경로 구현 후 중복 날짜·파일 실패·자정 경계도 검증합니다.
   service_role/SQL Editor만 사용한 검증으로 실제 회원 RLS 검증을 대신하지 않습니다.

원본 업로드(`upsert=false`) 후 일기·대화·파일 참조를 DB 트랜잭션으로 묶는 저장 서비스는 미구현입니다.
회원 JWT만 전달하는 BE 업로드도 현재 정책으로는 차단됩니다. 관리자 키로 무조건 우회하는 구현을 전제하지 않습니다.
파일/DB 저장은 원자적이지 않으므로 영속 요청 키·지문·상태와 응답 유실/고아 파일 복구가 필요합니다.
SQL만으로 필수 원본 실존·대화/출처 일관성·최종 확인을 보장하지 않습니다. FK는 RESTRICT이므로 탈퇴 전 정리 정책도 필요합니다.
SQL 파일과 이력 기록의 트랜잭션은 Supabase CLI에 맡깁니다. 버킷 준비까지 하나의 원자적 배포는 아닙니다.
후속 단계 실패 시 저장 기능을 열지 않습니다. 원격 SQL 적용·정책 생성은 사용자 확인이며,
에이전트의 실제 회원 RLS·Storage 실행 검증은 아직 수행하지 않았습니다.

### 로컬 Supabase Auth·RLS 조회 검증

[scripts/verify_supabase_rls.py](scripts/verify_supabase_rls.py)는 기존 BE `.venv`의 HTTPX를 재사용합니다.
**실제 로그인은 사용자가 직접 실행합니다.** 가상 테스트 회원 A/B만 사용하고 실제 아동 데이터는 사용하지 않습니다.
필수 환경변수는 `SUPABASE_PUBLISHABLE_KEY`이며 `sb_publishable_` 형식만 허용합니다.
선택 환경변수 `SUPABASE_URL`은 기본 주소 `https://nnbmxtiouvsuszretahg.supabase.co`와 정확히 같아야 합니다.
Secret/service_role/기존 JWT 형식 키는 거절합니다. `.env` 자동 로딩은 없습니다.

BE 루트의 대화형 PowerShell에서 실행합니다. 아래 명령은 키 값을 명령 이력에 직접 적지 않고 입력받습니다.

```powershell
$env:SUPABASE_PUBLISHABLE_KEY = Read-Host 'today-and-near Publishable key'
.\.venv\Scripts\python.exe -m scripts.verify_supabase_rls
$LASTEXITCODE
Remove-Item Env:SUPABASE_PUBLISHABLE_KEY
```

순서대로 A 이메일 → A 비밀번호 → B 이메일 → B 비밀번호를 입력합니다. 비밀번호는 `getpass`로 숨기며
숨김 입력이 불가능한 터미널/파이프에서는 중단합니다. 비밀번호를 환경변수·명령 인수·파일에 넣지 마세요.
비밀번호·Access/Refresh Token·응답 본문은 출력/파일 저장하지 않습니다. 자동 재시도·토큰 갱신도 없습니다.

도구는 비익명 로그인과 Auth 서버의 회원 정보, A/B의 서로 다른 ID·토큰을 확인한 뒤 회원별 JWT로
`diaries`, `diary_messages`, `diary_assets`의 `id,user_id`만 최대 100행씩 조회합니다.
소유자 필터 없는 본인 표본과 상대 회원 필터 조회를 양방향 검사하고, JWT 없는 요청의 DB 권한 오류
(`401/403` 및 `42501`)도 확인합니다. 빈 배열이나 게이트웨이 인증 오류를 명시적 DB 거절로 표시하지 않습니다.
헤더는 [Supabase API 키 안내](https://supabase.com/docs/guides/getting-started/api-keys)에 따라
`apikey`에 Publishable key, `Authorization`에 회원 JWT를 각각 전달합니다.

종료 코드: `0`은 세 테이블 모두 A/B 본인 데이터와 표본 교차 조회 확인, `1`은 실패/오류,
`2`는 로그인·조회·미인증 거절은 확인했으나 **본인 데이터 부족으로 회원 간 격리는 미완료**입니다.
표본 검증이며 전체 행·모든 RLS 정책의 검증 완료를 뜻하지 않습니다.

향후 별도 승인된 절차로 가상 일기 A/B가 준비되면 알려진 UUID를 지정해 본인 조회와 상대 ID 조회도 확인할 수 있습니다.
`check_fixture`를 다른 가상 데이터의 양방향 조회 검사로 확장할 수 있으며 이 도구는 fixture를 생성하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_supabase_rls --diary-a-id '<가상 A 일기 UUID>' --diary-b-id '<가상 B 일기 UUID>'
.\.venv\Scripts\python.exe -m pytest -q tests/test_verify_supabase_rls.py
.\.venv\Scripts\python.exe -m pytest -q
```

DB INSERT/UPDATE/DELETE와 Storage API 호출은 수행하지 않습니다. 쓰기 거절·Storage 권한·Auth 익명 회원·
만료/변조 토큰 검증은 후속 작업입니다. 모의 테스트 통과와 실제 회원 로그인/RLS 검증은 구분합니다.

### Git 작업 흐름

변경 전에 `git status`와 현재 브랜치를 확인하고 기존 작업을 보존합니다.
기존 `feat/be-bootstrap`에서 작업합니다. 푸시는 작업별 사용자 지시에 따라 일반 푸시하며 강제 푸시·PR 생성·main 병합은 수행하지 않습니다.
작업과 검증을 완료한 뒤 변경 목적 단위로 커밋합니다. 커밋 전에 `git diff --cached`로 스테이징 내용을 확인합니다.
커밋 메시지는 `일기 초안 API와 세션 처리 구현`처럼 접두사 없이 짧고 명확한 한국어로 작성합니다.

실제 환경변수 파일(`.env`, `.env.*`), 비밀키와 `.venv`는 Git에서 제외합니다.
공유용 `.env.example`에는 비밀값 없이 변수 이름과 예시만 작성합니다.
Markdown은 `.gitignore`에서 루트 `README.md`만 추적 대상으로 남깁니다. 하위 폴더 README를 포함한
다른 `.md` 문서는 로컬 전용이며 새 clone에 포함되지 않습니다. 기존 `docs/*.md`는 로컬 파일을 보존하고
`git rm --cached`로 추적만 해제했습니다. 과거 커밋은 재작성하지 않습니다.
`AGENTS.md`와 `PROJECT_STATUS.md`의 기존 `.git/info/exclude` 규칙도 유지하며 강제 추가하지 않습니다.
`supabase/migrations/*.sql`은 계속 추적합니다.
