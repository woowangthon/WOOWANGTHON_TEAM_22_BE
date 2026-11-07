# WOOWANGTHON_TEAM_22_BE

오늘,그리고 프로젝트의 BE 저장소입니다. FE와 AI는 각각 별도 Git 저장소에서 관리합니다.

FE → BE → 기존 AI HTTP 서버로 이어지는 최소 텍스트 대화·일기 초안 서버입니다.
아래 FE·BE API 계약은 **팀 합의 전 잠정 계약**입니다.
Supabase 비익명 회원 토큰 인증과 회원별 세션·초안 소유권 검사 지원. 실제 아동 데이터 사용 금지.
확정 일기·원본 대화의 DB 저장과 월별/상세 조회 구현. 그림 저장·이미지 생성·일기 수정은 미구현.

회원별 그림일기 SQL 001·002·003은 2026-11-07 사용자 확인 기준 실제 Supabase 적용 완료.
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
| SUPABASE_SECRET_KEY | 쓰기 전용 `sb_secret_` 키. BE 서버 환경/secret manager에만 주입, 누락 시 신규 저장 503 |
| DB_REQUEST_TIMEOUT_SECONDS | 10, 개별 DB HTTP와 전체 요청 제한. 자동 재시도 없음 |

변수 예시는 Git에서 공유하는 [.env.example](.env.example)에도 있습니다.
개발 CORS는 지정한 origin만 허용하고 자격증명 전송을 허용하지 않습니다.
허용 헤더: `Authorization`, `Content-Type`, `Idempotency-Key`, `X-Request-ID`.
응답 `X-Request-ID`는 BE 생성값이며 기존과 동일하게 브라우저에 노출.
`file://` 대신 정적 서버를 사용하고 FE 주소와 `ALLOWED_ORIGINS`를 맞추세요.

### API와 저장 방식

모든 대화·초안·확정·조회 API는 `Authorization: Bearer <Supabase access token>` 필수. `GET /health` 공개 유지.
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
인증 경로에는 JWT 자체 구현·서명 없는 디코딩·관리자 키·인증 성공 캐시·자동 재시도 없음.
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
확정된 결과만 서버 전용 RPC로 저장. 일반 회원 직접 쓰기 차단 유지. 아래 확정 일기 계약 참고.

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
| POST /api/chat-sessions/{session_id}/diaries | 검증된 초안과 원본 대화 영구 저장, 최초/재전송 모두 200 |
| GET /api/diaries?month=YYYY-MM | 본인 월별 일기 목록, 날짜 오름차순 |
| GET /api/diaries/{diary_id} | 본인 일기 본문·초안·원본 대화 상세 |

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
미확정 세션의 TTL·영구 저장·자동 정리는 없습니다. 확정 일기와 당시 대화는 별도 DB 저장 대상입니다.
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
2026-11-07 시작 회귀 기준 **345 passed** 유지. 확정 저장·조회 모의/SQL 정적 검증 60개 추가, 최종 **405 passed**.
실제 PostgreSQL·Supabase DB/RPC·RLS 실행 검증 결과는 포함하지 않습니다.

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
과거 버전 초안 표시·일기 수정 UX, 세션 TTL·보존/삭제 정책. 확정 저장·조회 계약은 아래 제안안 참고.
현재는 finished 여부와 무관하게 아이 발언이 있으면 명시적 초안 요청을 허용하고,
새 ID로 같은 버전을 명시적으로 재생성할 수 있습니다. 이 구현 정책은 팀 승인 전 잠정안입니다.

### 코드 구조

`app/main.py`는 앱/lifespan/CORS, `config.py`는 설정, `schemas.py`는 경계 자료형,
`errors.py`는 공통 오류를 담당합니다. `api/chat.py`는 요청 처리,
`services/chat.py`는 후보 이력/AI 호출/상태 전이, `session_store.py`는 메모리 예약/저장,
`clients/ai.py`는 기존 AI HTTP와 응답 검증을 담당합니다.
`auth.py`는 Bearer 의존성, `clients/auth.py`는 Supabase Auth 검증과 회원 UUID 반환 담당.
`diary_schemas.py`는 확정·조회 DTO, `api/diaries.py`는 요청 처리,
`services/diaries.py`는 소유권·초안·버전·저장 예약, `clients/diaries.py`는 회원 JWT 조회/서버 RPC 담당.

### Supabase DB·Storage 스키마와 정책

다음 SQL 파일은 Git 추적을 유지합니다. 2026-11-07 사용자 확인 기준 `today-and-near`에 001·002 적용,
회원별 RLS·Storage 정책 생성, 비익명 테스트 회원 A/B와 Publishable key 준비가 완료되었습니다.
에이전트가 원격 스키마나 실제 회원 로그인을 직접 확인한 결과는 아닙니다. DB 저장 코드 구현 완료, 003 원격 미적용.
SQL 주석의 `docs/*.md`는 로컬 상세 문서 안내입니다. 새 clone에서도 필요한 공유 정보는 이 절에 정리합니다.

| 파일 | 목적 |
| --- | --- |
| [20261107000100_create_member_diaries.sql](supabase/migrations/20261107000100_create_member_diaries.sql) | 테이블 3개, 제약·인덱스·권한·회원별 RLS |
| [20261107000200_private_diary_storage_policies.sql](supabase/migrations/20261107000200_private_diary_storage_policies.sql) | 비공개 Storage 접근 정책 |
| [20261107000300_confirm_member_diary.sql](supabase/migrations/20261107000300_confirm_member_diary.sql) | 서버 전용 원자적 저장 RPC·영속 요청 지문·고유 인덱스, 원격 미적용 |

`diaries`는 `auth.users.id` 기반 회원, 서울 기준 `date`, 날씨, 확인한 글/시각, 출처 세션·버전·요청,
선택한 AI 초안 JSON을 보관합니다. `diary_messages`는 원본 role/text/0-based position을,
`diary_assets`는 원본 그림·향후 만화 장면의 파일 키/MIME/크기/해상도/SHA-256을 보관합니다.
회원·날짜당 한 권 고유 제약은 초기 후보이며 자식 복합 FK는 부모 회원까지 일치시킵니다.
파일 바이트·base64·공개/서명 URL은 DB에 보관하지 않습니다.
파일 크기 메타데이터는 **1바이트 이상, 10 MiB(10,485,760바이트) 이하**로 1 MiB 미만 그림도 허용합니다.
실제 JPEG·PNG 디코딩과 파일 유효성 검사는 향후 BE 업로드 코드가 수행하며 현재는 미구현입니다.

DB는 `auth.uid()` 일치와 `is_anonymous='false'` 조건으로 일반 회원의 본인 조회만 허용합니다.
일반 회원의 직접 INSERT·수정·삭제와 미인증·익명 로그인·타 회원 조회는 차단합니다.
Supabase Auth 인증과 메모리 세션·초안 소유권 검사, 서버 전용 원자적 DB 저장 경로 구현 완료.
회원 JWT로는 SELECT만 허용. 저장은 검증된 Auth UUID를 인수로 전달하는 서버 전용 RPC 사용.
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

현재 사용자 보고 기준 가상 일기 A/B가 준비돼 있으며, 알려진 UUID를 지정해 본인 조회와 상대 ID 조회도 확인할 수 있습니다.
`check_fixture`를 다른 가상 데이터의 양방향 조회 검사로 확장할 수 있으며 이 도구는 fixture를 생성하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_supabase_rls --diary-a-id '<가상 A 일기 UUID>' --diary-b-id '<가상 B 일기 UUID>'
.\.venv\Scripts\python.exe -m pytest -q tests/test_verify_supabase_rls.py
.\.venv\Scripts\python.exe -m pytest -q
```

DB INSERT/UPDATE/DELETE와 Storage API 호출은 수행하지 않습니다. 쓰기 거절·Storage 권한·Auth 익명 회원·
만료/변조 토큰 검증은 후속 작업입니다. 모의 테스트 통과와 실제 회원 로그인/RLS 검증은 구분합니다.

### 실제 Supabase와 BE 일기 GET 검증

[scripts/verify_diary_reads.py](scripts/verify_diary_reads.py)는 기존 `Settings`, `create_app`,
`AuthClient`, `DiaryRepository`와 GET 라우트를 그대로 사용합니다. BE 앱의 lifespan과 ASGI 요청을
로컬 프로세스에서 실행하고 Auth·DB는 실제 today-and-near에 연결합니다. 별도 BE/AI 서버는 필요 없습니다.
실행 중에도 Secret key를 제외하며, 전송 계층은 지정 프로젝트의 Auth 회원 확인·일기/메시지 GET과
비밀번호 로그인 POST만 허용합니다. DB 쓰기·RPC·Storage·AI 호출·자동 재시도·토큰 갱신은 없습니다.

**아래 블록 전체를 한 번에 복사·실행**합니다. Windows PowerShell의 로컬 대화형 터미널을 사용하세요.
`오늘,그리고` 폴더 위치가 다르면 첫 경로만 바꿉니다. 기존 BE `.venv`를 사용하며 추가 패키지는 없습니다.

```powershell
& {
    Set-Location -LiteralPath 'C:\Users\gicha\OneDrive\바탕 화면\오늘,그리고\WOOWANGTHON_TEAM_22_BE'
    $env:SUPABASE_URL = 'https://nnbmxtiouvsuszretahg.supabase.co'
    $diaryReadKey = Read-Host 'Supabase Publishable key (숨김)' -AsSecureString
    try {
        $env:SUPABASE_PUBLISHABLE_KEY = [System.Net.NetworkCredential]::new('', $diaryReadKey).Password
        .\.venv\Scripts\python.exe -m scripts.verify_diary_reads
    }
    finally {
        Remove-Item Env:SUPABASE_PUBLISHABLE_KEY -ErrorAction SilentlyContinue
        $diaryReadKey.Dispose()
    }
}
```

Publishable key(`sb_publishable_...`)는 사용자가 환경변수로 직접 전달하며 명령 기록에 값이 남지 않습니다.
부모 PowerShell의 Publishable key 변수는 종료 시 제거합니다. 실제 Secret key는 입력하지 않습니다.
자식 Python 프로세스는 상속된 `SUPABASE_SECRET_KEY`도 설정 로딩 전에 제거합니다.
이후 **A 이메일 → A 비밀번호 → B 이메일 → B 비밀번호**를 모두 숨김 입력합니다.
파이프·비대화형 터미널·숨김 입력 불가 상태에서는 네트워크 요청 전에 중단합니다.
계정·키·토큰·본문·대화·응답 원문·예외 원문은 출력하거나 파일에 저장하지 않습니다.

검사 결과는 항목별 `성공`/`실패`만 표시합니다. 다음 12개 항목 전부 성공하고 종료 코드가 `0`이면 통과입니다.
설정/입력/실행 자체 오류는 `검증 실행: 실패`와 종료 코드 `1`, 개별 검사 실패도 종료 코드 `1`입니다.
로그인 실패 등으로 선행 조건이 없으면 후속 검사는 실패로 표시하며 성공으로 간주하지 않습니다.

- 미인증 월별·A 상세·B 상세 GET 각각 401: 3개.
- A/B 실제 로그인과 기존 Auth 클라이언트의 비익명 회원 검증: 2개.
- A/B 이메일·Auth 회원 ID·토큰 상이: 1개.
- A/B 각 본인 상세 200·상대 상세 `404 DIARY_NOT_FOUND`·월별 본인 일기만 노출: 6개.

대상은 `GET /api/diaries?month=2026-11`과 `GET /api/diaries/{diary_id}`입니다.
기존 가상 일기는 A `77e0ebce-127d-4bb4-84ef-8d89d6c06a90`, B `49df086e-a986-41ef-8e95-e8997ff270cc`,
날짜는 모두 `2026-11-07`입니다. 월별 목록은 알려진 본인 일기 **정확히 1건**이어야 합니다.
빈 목록·상대 일기·알 수 없는 추가 ID·중복·다른 날짜는 실패입니다. 해당 월에 정상 일기가 추가돼도 실패하므로
데이터 변경 없이 현재 fixture 전제를 먼저 확인하세요. 본문 문구·AI 프롬프트·모델·생성 품질 비교는 하지 않습니다.

자동 테스트는 실제 BE 경로와 HTTPX 모의 Auth/DB로 판정·조회 경계·비밀정보 비노출을 검사합니다.
2026-11-07 **사용자 직접 실행 보고: 실제 Supabase 연동 검사 12/12 성공, 종료 코드 0**.
A/B 로그인, 본인 상세 200, 상대 상세 404, 월별 목록 격리, 미인증 401을 확인했습니다.
에이전트가 실행한 결과와 구분하며 실제 쓰기 성공을 뜻하지 않습니다.
배포된 HTTP 서버·브라우저/CORS·전체 DB 행과 RLS 정책·Storage·저장/RPC·동시성은 이 도구의 검증 범위 밖입니다.
이 도구는 Auth 로그인으로 세션을 발급받지만 DB 데이터나 스키마를 변경하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error tests/test_verify_diary_reads.py
.\.venv\Scripts\python.exe -m pytest -q -W error
```

### 확정 일기 저장·조회 제안 계약 — FE 전달 사항

팀 합의 전 **제안 계약**입니다. 기존 대화/초안 API의 요청·성공 응답은 유지합니다.
회원·서울 날짜당 한 권은 기존 001의 제약을 따릅니다. 날짜는 BE가 생성한 확정 요청 처리 시각을
RPC에서 `Asia/Seoul`로 변환해 결정합니다. 클라이언트 날짜·과거 저장·수정·덮어쓰기 API는 없습니다.
같은 버전에서 만든 초안 중 사용자가 선택한 `draft_id`를 전달합니다. 저장 중 같은 세션의 발언/초안 생성은 409입니다.

```http
POST /api/chat-sessions/ses_11111111111111111111111111111111/diaries
Authorization: Bearer <회원 access token>
Content-Type: application/json

{
  "client_request_id": "confirm-demo-001",
  "history_revision": 1,
  "draft_id": "draft_11111111111111111111111111111111",
  "weather": "sunny"
}
```

`client_request_id`는 확정 작업용 회원별 고유 키(ASCII 영문·숫자·밑줄·하이픈 1~128자)입니다.
초안 생성 키와 namespace는 별도입니다. `history_revision`은 1 이상 정수, `draft_id`는 해당 세션의 실제 초안 ID입니다.
`weather`는 생략/null 또는 `sunny`, `cloudy`, `snowy`, `rainy`입니다. 생략과 null은 동일하게 처리합니다.
`user_id`, `confirmed_text`, `ai_draft`, `messages`, `diary_date` 등 추가 필드는 422입니다.
본문은 저장한 검증 초안의 `text`, 대화는 서버의 전체 확정 이력에서만 가져옵니다.

최초 저장과 동일 요청 복구 모두 **200**입니다. 아래 가상 예시와 같은 상세 객체를 반환합니다.
`GET /api/diaries/44444444-4444-4444-8444-444444444444`도 동일한 응답입니다.

```json
{
  "diary_id": "44444444-4444-4444-8444-444444444444",
  "diary_date": "2026-11-07",
  "weather": "sunny",
  "confirmed_text": "오늘 친구와 공원에서 놀았다.",
  "confirmed_at": "2026-11-06T15:00:00Z",
  "ai_draft": {
    "draft_id": "draft_11111111111111111111111111111111",
    "status": "draft",
    "text": "오늘 친구와 공원에서 놀았다.",
    "pages": [{"text": "오늘 친구와 공원에서 놀았다.", "source_ids": [1], "imageUrl": null}],
    "sources": [{"id": 1, "text": "오늘 친구와 공원에서 놀았다."}],
    "fallback": true,
    "requires_confirmation": true,
    "mock": true
  },
  "messages": [
    {"position": 0, "role": "assistant", "text": "오늘 어떤 일이 있었니?"},
    {"position": 1, "role": "child", "text": "오늘 친구와 공원에서 놀았다."},
    {"position": 2, "role": "assistant", "text": "무엇을 하며 놀았니?"}
  ],
  "imageUrl": null,
  "assets": []
}
```

`ai_draft`는 당시 초안의 원본 구조를 보존하므로 `status=draft`, `requires_confirmation=true`도 그대로입니다.
최종 확정 글과 시각은 별도 `confirmed_text`, `confirmed_at`입니다. 메시지는 position 0부터 오름차순입니다.
그림/만화는 아직 없으며 `imageUrl:null`, `assets:[]`입니다. 가짜 파일 키나 URL은 생성하지 않습니다.

```http
GET /api/diaries?month=2026-11
Authorization: Bearer <회원 access token>
```

```json
{
  "month": "2026-11",
  "diaries": [{
    "diary_id": "44444444-4444-4444-8444-444444444444",
    "diary_date": "2026-11-07",
    "weather": "sunny",
    "preview": "오늘 친구와 공원에서 놀았다."
  }]
}
```

월은 유효한 `YYYY-MM`(0001~9999년), 목록은 본인 데이터만 날짜 오름차순이며 빈 달은 `diaries:[]`입니다.
미리보기는 확정 본문 앞 120자입니다. 서버 내부 조회는 본문을 가져와 자르며 FE에 전체 본문/대화는 보내지 않습니다.
없는 일기와 타인 일기는 모두 `404 DIARY_NOT_FOUND / 일기 없음`입니다. UUID 형식 오류는 422입니다.
현재 메모리에 존재하는 타인 세션은 저장 요청 키와 무관하게 404이며 DB 저장 호출도 없습니다.

동일 회원·동일 `client_request_id`에 세션/초안/버전/날씨가 같으면 DB 결과를 복구합니다.
대화가 이후 바뀌거나 서버가 재시작해도 이미 성공한 요청의 결과를 반환합니다. 이 복구에는 쓰기 키가 필요하지 않습니다.
같은 키의 다른 요청은 409, 같은 날 다른 키의 일기는 409이며 다른 회원의 같은 키는 독립적입니다.
쓰기/읽기 timeout은 이미 커밋됐을 가능성이 있으므로 **같은 키와 본문으로만 재시도**합니다.
신규 키로 우회하지 않습니다. 최초 저장에 성공하지 못한 미확정 세션은 재시작 시 소실됩니다.

| HTTP | 신규 오류 코드 | 의미 / retryable |
| --- | --- | --- |
| 404 | DIARY_DRAFT_NOT_FOUND | 해당 세션 초안 없음 / false |
| 404 | DIARY_NOT_FOUND | 본인 일기 없음 / false |
| 409 | IDEMPOTENCY_CONFLICT | 요청 내용 불일치 / false |
| 409 | DIARY_DATE_CONFLICT | 날짜 중복 / false |
| 409 | HISTORY_REVISION_CONFLICT | 현재 이력/초안 버전 불일치 / false |
| 409 | SESSION_BUSY | 같은 세션의 처리 진행 중 / true |
| 422 | VALIDATION_ERROR | 월·UUID·날씨·본문 필드 형식 오류 / false |
| 503 | DIARY_STORAGE_NOT_CONFIGURED | 쓰기 Secret key 또는 조회 설정 누락 / false |
| 503 | DIARY_STORAGE_UNAVAILABLE | DB 연결/timeout/429/5xx / true |
| 503 | DIARY_STORAGE_FAILED | DB 거절·권한/스키마 오류 등 / false |
| 502 | DIARY_STORAGE_INVALID_RESPONSE | DB 응답 JSON/구조 오류 / false |

인증 오류는 기존 401/403/502/503 계약을 유지합니다. DB 읽기 단계에서 회원 JWT가 거절되면 401입니다.
모든 오류는 기존 `{error:{code,message,retryable},request_id}` 형식이며 원본 DB 오류와 키/토큰은 반환하지 않습니다.
003 미적용 시 기존 인증·대화·초안은 유지되지만, 신규 저장은 스키마 오류로 안전하게 실패합니다.

### AI 담당 유지 계약

AI `/api/chat`와 `/api/diary`의 기존 요청·응답 필드/필수값을 그대로 유지합니다.
BE는 기존 `clients/ai.py`의 응답·출처 검증 결과를 재사용하며 저장 단계에서 새 AI 검증을 추가하지 않습니다.
프롬프트·질문 생성 전략·대화 이력 활용·문장 생성·모델/파라미터·페이지 구분 품질은 자유롭게 개선할 수 있습니다.
BE 저장은 특정 문구·문장 길이 추정·키워드·프롬프트에 의존하지 않습니다.
기존 구조/한도 내의 `text`, `pages`, `sources`, `fallback`, `requires_confirmation`, `mock`을 유지합니다.
BE가 추가하는 `draft_id`, `status`와 초안 응답의 세션/버전은 기존대로입니다.
현재 `pages[].imageUrl=null`, child 출처 인덱스/원문/순서 계약도 유지합니다.

### DB 쓰기 경계와 migration 003

003은 **2026-11-07 사용자 SQL Editor 확인 기준 실제 적용 완료**입니다. 001·002도 적용 완료입니다.
사용자는 `confirmation_fingerprint` 컬럼·중복 방지 인덱스·`confirm_member_diary` RPC 존재와
`SECURITY INVOKER`, anon/authenticated RPC 실행 불가, service_role 실행 가능,
authenticated의 `diaries` 직접 INSERT 불가를 확인했습니다.
에이전트는 실제 원격 구조·권한을 재조회하거나 DB/SQL Editor/Storage를 변경하지 않았습니다.
공식 [API 키 안내](https://supabase.com/docs/guides/getting-started/api-keys)에 따라
`sb_secret_` 키는 BE 서버 전용 `apikey` 헤더로만 전달합니다. 회원 JWT/Secret key를 AI로 보내지 않습니다.
읽기는 Publishable key + 검증된 요청의 회원 JWT로 수행하며 명시적 회원 필터와 기존 RLS를 함께 적용합니다.
회원별 토큰을 공유 클라이언트의 기본 헤더로 저장하지 않습니다. 쓰기 클라이언트는 별도 인스턴스이며 회원 JWT를 포함하지 않습니다.

RPC는 `public.confirm_member_diary(uuid,text,text,integer,text,text,timestamptz,jsonb,jsonb) -> uuid`입니다.
인수 순서는 `p_user_id`, `p_session_id`, `p_client_request_id`, `p_history_revision`, `p_fingerprint`,
`p_weather`, `p_confirmed_at`, `p_ai_draft`, `p_messages`입니다.
대상은 `public.diaries`, `public.diary_messages`와 회원 유효성 확인용 `auth.users`입니다.
공식 [함수 권한 안내](https://supabase.com/docs/guides/database/functions#secure-a-database-function)에 따라
`SECURITY INVOKER`, 빈 `search_path`, 명시적 스키마, PUBLIC/anon/authenticated EXECUTE 회수, service_role만 EXECUTE 허용입니다.
service_role에는 관련 두 테이블 SELECT/INSERT, 회원 ID·익명 여부·역할·삭제 여부의 컬럼 SELECT를 명시합니다.
일반 회원 권한이나 RLS/Storage 정책은 변경하지 않습니다. Secret key 역할의 RLS 우회는 BE의 인증·소유권 검사를 대신하지 않습니다.

003은 `confirmation_fingerprint` nullable 컬럼과 회원+확정 요청 ID의 부분 고유 인덱스를 추가합니다.
지문은 정규화한 세션/요청 ID/초안 ID/버전/날씨의 SHA-256이며 기존 행의 값을 채우거나 수정하지 않습니다.
기존 행은 지문 null로 보존되고 과거 요청의 복구 대상은 아니지만 기존 회원·날짜 고유 제약은 계속 적용됩니다.
RPC는 비익명 활성 회원 확인 → 회원별 트랜잭션 advisory lock → 동일 요청 지문 확인 → 날짜 충돌 확인 →
초안 본문과 원본 메시지 INSERT를 한 트랜잭션으로 수행합니다. 메시지 실패 시 부모도 롤백되며 부분 저장을 재사용하지 않습니다.
실패를 삼키거나 DB 저장을 자동 재시도하지 않습니다. 세션 busy는 취소/실패에도 해제합니다.

충돌 오류: SQLSTATE `PT409` → PostgREST HTTP 409 → BE HTTP 409.
공식 [오류 규칙](https://docs.postgrest.org/en/v16/references/errors.html)에 따른 `PT410` 제거.
SQL `DETAIL` / 응답 `details`의 `IDEMPOTENCY_CONFLICT`·`DIARY_DATE_CONFLICT`로 내부 구분.
사용자 메시지는 각각 `요청 내용 불일치`·`날짜 중복`, `retryable=false`.
날짜 사전 확인과 고유 제약 예외 모두 동일 처리. 알 수 없는 충돌 상세는 저장소 오류로 처리.

서버의 `SUPABASE_SECRET_KEY`는 배포 환경/secret manager에서 주입합니다. 실제 키를 채팅·코드·FE·AI·로그에 넣지 않습니다.
`.env.example`에는 변수 이름/설명만 있으며 현재 사용자에게 실제 키 입력을 요청하지 않습니다.
누락 시 기존 인증/대화/조회는 유지하고 신규 쓰기만 503입니다. 전체 앱 시작에 쓰기 키를 요구하지 않습니다.

### RPC 트랜잭션 검증 준비 — DB 실행 전 사용자 검토 필수

001·002·003은 Production 적용 완료입니다. 기존 migration을 수정하거나 재적용하지 않습니다.
아래 SQL은 **미실행 준비본**이며 실제 BE 영구 저장은 아직 미검증입니다.
실제 Supabase DB 실행·영구 행 생성/삭제·Secret key 사용은 이번 작업에서 수행하지 않습니다.

| 파일 | 용도와 실행 조건 |
| --- | --- |
| [review_confirm_member_diary.sql](tests/sql/review_confirm_member_diary.sql) | 읽기 전용 카탈로그 검토. 함수 정의·권한·RLS·제약·인덱스·기본값·트리거·규칙. 회원 데이터 행 조회 없음 |
| [verify_confirm_member_diary.sql](tests/sql/verify_confirm_member_diary.sql) | 폐기 가능한 독립 로컬 Supabase 전용. 합성 Auth 회원 생성도 내부 롤백. `v_disposable_local=false` 기본 차단 |
| [verify_confirm_member_diary_production.sql](tests/sql/verify_confirm_member_diary_production.sql) | 기존 가상 A/B 전용 원격 검증. `v_reviewed=false` 기본 차단. 사용자 검토 후에만 실행 |

먼저 독립 로컬 DB를 검토합니다. 현재 환경은 `local-tools/supabase-cli/v3.0.0/supabase.exe`만 발견됐고,
Docker·psql·supabase는 PATH에서 발견되지 않았습니다. 로컬 DB는 시작하거나 설치하지 않았습니다.
[공식 로컬 개발 절차](https://supabase.com/docs/guides/local-development/cli/getting-started)의 Docker·CLI를
사용자가 준비한 뒤, **새 빈 디렉터리·고유 project_id·미사용 포트**로 `supabase init`과 `supabase start`를 수행합니다.
현재 BE의 사용자 `supabase/config.toml`·`.gitignore`와 연결 설정은 복사/수정하지 않습니다.
원격 link·db push·원격 reset은 사용하지 않습니다. 기존 로컬 프로젝트의 db reset도 금지합니다.

로컬 Studio의 주소가 CLI가 표시한 loopback 주소인지 확인하고, 로컬 SQL Editor에서 원본 001 → 003을 적용합니다.
002는 Storage 버킷 정책이므로 RPC 전용 로컬 검사에는 불필요합니다. 전체 환경 검사에는 별도 버킷 준비 후 002도 필요합니다.
`verify_confirm_member_diary.sql`의 로컬 확인 플래그를 사용자 사본에서 true로 바꾸고 파일 전체를 한 번에 실행합니다.
예상 결과는 `LOCAL_RPC_SQL_CHECKS_PASSED`, 오류 없음, 마지막 `ROLLBACK`입니다.
합성 UUID가 이미 있으면 중단하며 기존 계정을 삭제하지 않습니다.
로컬 실행 환경을 준비할 수 없으면 **로컬 실행 미검증**으로 남기고, 원격으로 옮겨 실행하기 전에 그 한계까지 사용자 검토를 받습니다.

원격 실행 전 검토 항목과 순서:

1. 사용자가 파일 전체와 대상 프로젝트 `today-and-near`(`nnbmxtiouvsuszretahg`)·Production 표시·백업을 검토합니다.
   다른 회원/아동 자료를 사용하지 않습니다. 기존 A/B의 알려진 가상 일기·계정·fixture는 보존합니다.
2. 사용자가 읽기 전용 `review_confirm_member_diary.sql` 전체를 SQL Editor에서 한 번에 실행하고,
   실제 정의를 001·003과 대조합니다. RPC 인수 9개·순서·uuid 반환·SECURITY INVOKER·빈 search_path를 확인합니다.
   PUBLIC/anon/authenticated 실행 금지, service_role 허용과 일반 회원 두 테이블 INSERT 금지를 확인합니다.
   함수 본문 MD5가 검증 SQL의 값과 같아야 합니다. 줄바꿈 CR만 정규화하며 공백 차이도 안전하게 중단합니다.
   불일치를 피하려고 지문을 임의 교체하지 말고 먼저 정의 차이를 검토합니다.
3. 두 테이블 기본값은 001의 UUID/now() 생성인지, 제약·인덱스·RLS는 예상과 같은지 확인합니다.
   추가 트리거·쓰기 규칙·외부 호출·시퀀스 등 롤백 불가능한 부작용이 있으면 중단합니다.
   스키마 변경 작업과 A/B를 사용하는 다른 저장·로그인 작업도 검사 동안 중지합니다.
4. A `77e0ebce-127d-4bb4-84ef-8d89d6c06a90`, B `49df086e-a986-41ef-8e95-e8997ff270cc`가
   모두 2026-11-07 가상 일기인지 확인합니다. SQL은 이 두 ID로만 회원을 정하고 서로 다른 활성 비익명 회원을 요구합니다.
   테스트 날짜 `2035-01-02`·`2035-01-03`과 `rpc-proof-` 키가 A/B에서 미사용이어야 합니다.
   이미 사용 중이면 삭제·수정 대신 중단하고 새 날짜/키를 검토합니다. SQL의 타임스탬프·날짜 단언도 함께 바꿔야 합니다.
5. **위 항목에 대한 사용자 검토가 끝난 뒤**, 사용자 사본의 `v_reviewed`만 true로 바꿉니다.
   SQL Editor 역할은 postgres로 설정하고, **BEGIN부터 마지막 ROLLBACK까지 파일 전체를 단 한 번에 실행**합니다.
   부분 선택·문장별 실행·RPC 호출만 복사는 금지합니다. 이번 작업은 해당 실행을 승인하거나 대신 수행하지 않습니다.

데이터 불변 경계는 다음과 같습니다.

- 바깥 `BEGIN`과 마지막 `ROLLBACK`, 3초 잠금·30초 문장·60초 유휴 트랜잭션 제한을 사용합니다.
  A/B advisory lock은 RPC와 같은 방식으로 정렬해서 잡습니다. 다른 회원의 행은 조회/변경하지 않습니다.
- 모든 RPC 테스트 쓰기를 단일 내부 예외 블록에 넣고, 정상 검사 완료 후에도 전용 `ZV001`을 발생시켜 전부 취소합니다.
  [PostgreSQL 예외 처리 규칙](https://www.postgresql.org/docs/current/plpgsql-control-structures.html#PLPGSQL-ERROR-TRAPPING)에
  따라 해당 블록의 DB 변경이 되돌아갑니다. 예상 밖 오류·취소·검증 실패는 성공으로 삼키지 않으며 전체 문장이 실패합니다.
  마지막 ROLLBACK이 실행되지 않아도 완료된 DO에 테스트 행이 남는 구조를 피합니다.
- 내부 롤백 후 A/B diaries·diary_messages·diary_assets의 정렬된 전체 행을 전 스냅샷과 비교합니다.
  Auth는 ID·역할·익명·삭제 상태만 비교하며 Auth INSERT/UPDATE/DELETE가 없습니다. 스냅샷은 메모리에서만 비교하고 출력하지 않습니다.
  일기/원본 대화의 빈값 실패 뒤 부모·일부 메시지가 남지 않았는지도 별도로 비교합니다.
- 마지막 결과 `RPC_SQL_CHECKS_PASSED`는 내부 쓰기 취소와 불변 비교가 모두 끝난 뒤에만 설정합니다.
  결과 표식·오류 없음·최종 ROLLBACK을 함께 확인합니다. UI가 결과/NOTICE를 표시하지 않으면 성공 판정을 보류합니다.
  에러 뒤 단순한 `Success. No rows returned`만으로 성공 처리하지 않습니다.
  실패/연결 끊김에는 재실행하지 말고 같은 연결에서 가능하면 `ROLLBACK;`으로 종료합니다.
  연결을 재사용할 수 없으면 종료/유휴 제한을 확인하고, 새 연결에서 A/B fixture와 `rpc-proof-` 잔여 행을 확인한 뒤 검토합니다.
  잔여 행이 보이면 삭제하지 말고 중단합니다. 별도 연결에서 COMMIT하면 테스트를 복구할 수 있다는 가정은 금지합니다.

실행 전후와 연결 끊김 후에는 아래 **읽기 전용 블록 전체**로 대상·잔여 행도 확인할 수 있습니다.
기대값은 `test_members=2`, `known_fixtures=2`, `remaining_test_diaries=0`입니다.
잔여 자식은 부모 FK로 연결되므로 테스트 부모가 없는 상태에서 독립적으로 남을 수 없습니다.
이 확인은 내부 전 행 스냅샷 비교를 대신하지 않습니다. 회원 수/fixture 수 불일치나 잔여 행은 중단 조건입니다.

```sql
begin read only;
with fixture as (
    select id, user_id, diary_date from public.diaries
    where id in ('77e0ebce-127d-4bb4-84ef-8d89d6c06a90',
                 '49df086e-a986-41ef-8e95-e8997ff270cc')
), members as (select distinct user_id from fixture)
select (select count(*) from members) as test_members,
    (select count(*) from fixture where diary_date = date '2026-11-07') as known_fixtures,
    (select count(*) from public.diaries d where d.user_id in (select user_id from members)
        and (d.diary_date in (date '2035-01-02', date '2035-01-03')
            or d.source_client_request_id like 'rpc-proof-%')) as remaining_test_diaries;
rollback;
```

| 검사 | 예상 결과 |
| --- | --- |
| anon/authenticated 직접 SQL 역할의 RPC 호출 | SQLSTATE 42501, 쓰기 없음 |
| service_role 최초 RPC / 동일 요청 재전송 | uuid 반환 / 동일 uuid·원래 확정 시각·3개 메시지, 날짜 이동 없음 |
| B의 같은 요청 키 | A와 다른 uuid, B 소유 |
| 같은 키·다른 지문 | PT409 / DETAIL IDEMPOTENCY_CONFLICT / 요청 내용 불일치 |
| 같은 날짜·다른 키 | PT409 / DETAIL DIARY_DATE_CONFLICT / 날짜 중복 |
| null 회원·잘못된 지문/세션/키/버전/날씨/시각·잘못된 대화 구조/개수/순서/역할/child 부재 | 22023, 잔여 행 없음 |
| 잘못된 초안 필수값 / 마지막 메시지 빈 본문 | 23514 / diaries_ai_draft_ck 또는 diary_messages_text_ck, 부모·대화 쓰기 모두 취소 |
| 원본 보존 / 검사 종료 | position·role·공백/개행 포함 text·초안 JSON 동일 / A/B 기존 행 동일 |

날짜 충돌은 사전 확인 경로를 실행합니다. 고유 제약 예외 분기·실제 동시 경쟁은 단일 연결 검사로 실행 검증하지 못합니다.
RPC는 BE가 계산한 지문을 신뢰합니다. 같은 지문에 바뀐 초안/대화를 넣어도 재검증하는 계약이 아니며,
SQL은 AI 출처·생성 품질을 새로 검증하지 않습니다. 기존 BE 초안 검증과 서버 전용 호출 경계를 유지합니다.
SQL Editor의 `SET LOCAL ROLE`은 DB 권한·트랜잭션 검사입니다. 실제 회원 JWT/게이트웨이/PostgREST/Secret key HTTP는 별도 검사입니다.
`PT409`의 실제 HTTP 409·JSON `details`는 다음 BE 단계에서 확인합니다.
[PostgREST 공식 규칙](https://docs.postgrest.org/en/stable/references/errors.html#raise-errors-with-http-status-codes)상
PTxyz는 HTTP xyz이며 BE는 POST·409·PT409·알려진 details 조합만 해당 충돌로 처리합니다.

### 다음 실제 BE 저장 테스트 — RPC 검사 이후 별도 실행

권장 환경은 **별도 HTTPS Supabase 개발 프로젝트와 전용 가상 회원 C/D**입니다.
현재 BE 설정은 HTTPS origin·sb_publishable_/sb_secret_만 허용하므로 기본 HTTP 로컬 Supabase를 그대로 연결할 수 없습니다.
로컬 PostgreSQL RPC 검사와 실제 BE HTTP 검사를 구분하며 이를 위해 설정 검증을 완화하지 않습니다.
Production에서 하려면 별도 테스트 회원·현재 서울 날짜의 빈 슬롯·신규 영구 데이터 생성 범위를 다시 사용자 검토해야 합니다.
2026-11-07 A/B에는 이미 일기가 있으므로 새 저장은 정상적으로 409입니다. 기존 A/B 삭제·덮어쓰기·기기 시각 변경은 금지합니다.

1. RPC 검사 결과를 먼저 확인합니다. 격리 프로젝트에 001·002·003/버킷과 비익명 C/D를 별도 준비하고,
   C/D의 현재 서울 날짜 목록이 비어 있는지 회원 JWT로 확인합니다. 계정/데이터 생성은 이번 작업에서 수행하지 않습니다.
2. AI 담당자가 기존 mock 서버를 준비합니다. `/api/status`의 `mode=mock`을 확인하고 가상 대화만 사용합니다.
   실모델 모드·상태 미확인 시 중단합니다. FE·AI 저장소나 `/api/chat`, `/api/diary` 계약을 바꾸지 않습니다.
3. 새 BE 전용 PowerShell에서 아래 블록을 **후속 실행 승인 후에만** 사용합니다.
   사용자 로컬에서 실제 키를 숨김 입력하며 Secret key는 BE 환경에만 둡니다. AI 프로세스 실행 셸과 분리합니다.
   이 블록은 서버 시작만 하며 저장 요청은 다음 단계에서 사용자가 직접 전송합니다.

```powershell
& {
    $storagePublicKey = $null
    $storageSecretKey = $null
    try {
        $storageProjectUrl = Read-Host '격리 테스트 프로젝트 HTTPS origin'
        if ($storageProjectUrl.TrimEnd('/') -eq 'https://nnbmxtiouvsuszretahg.supabase.co') {
            throw '격리 테스트 프로젝트 필요'
        }
        $storageAiStatus = Invoke-RestMethod 'http://127.0.0.1:8002/api/status'
        if ($storageAiStatus.mode -ne 'mock') { throw 'AI mock 상태 불일치' }
        $env:SUPABASE_URL = $storageProjectUrl
        $env:AI_BASE_URL = 'http://127.0.0.1:8002'
        $storagePublicKey = Read-Host '격리 프로젝트 Publishable key (숨김)' -AsSecureString
        $storageSecretKey = Read-Host '격리 프로젝트 BE Secret key (숨김)' -AsSecureString
        $env:SUPABASE_PUBLISHABLE_KEY = [System.Net.NetworkCredential]::new('', $storagePublicKey).Password
        $env:SUPABASE_SECRET_KEY = [System.Net.NetworkCredential]::new('', $storageSecretKey).Password
        .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
    }
    finally {
        Remove-Item Env:SUPABASE_SECRET_KEY, Env:SUPABASE_PUBLISHABLE_KEY, Env:SUPABASE_URL, Env:AI_BASE_URL -ErrorAction SilentlyContinue
        if ($null -ne $storageSecretKey) { $storageSecretKey.Dispose() }
        if ($null -ne $storagePublicKey) { $storagePublicKey.Dispose() }
    }
}
```

4. BE 루트에서 실행하며 `--reload`는 사용하지 않습니다. 브라우저 분리 프로필 C/D의 로컬 `/docs` Authorize에는
   해당 회원 JWT만 입력합니다. Secret key를 Swagger/브라우저/FE/AI/채팅/명령 인수/로그/파일에 넣지 않습니다.
   C/D 로그인도 해당 격리 프로젝트에서 수행합니다. `verify_mock_diary`는 가짜 Auth이므로 실제 저장 검사에 사용하지 않습니다.
5. C: 시작 `POST /api/chat-sessions`에 `{}`와 새 `Idempotency-Key` → 201.
   `/messages`에 `{"client_message_id":"storage-turn-1","text":"가상 인물이 공놀이한 이야기야."}` → 200.
   `/diary-draft`에 `{"client_request_id":"storage-draft-1","history_revision":1}` → 200.
   받은 session_id·draft_id와 원본 응답을 로컬 메모리에서만 사용합니다.
   `/diaries`에 `{"client_request_id":"storage-confirm-1","history_revision":1,"draft_id":"<받은 draft_id>","weather":"sunny"}` → 200.
   user_id/본문/대화/diary_date는 보내지 않습니다. AI 응답 문구나 생성 알고리즘에 맞춘 판정은 추가하지 않습니다.
6. 응답 diary_id·서울 날짜·초안 JSON·확정 본문·position/role/text와 시작부터의 대화 전체(1회 발언이면 3개)를 비교합니다.
   `GET /api/diaries?month=<저장 YYYY-MM>`과 `GET /api/diaries/{diary_id}` → 200, D의 C 상세는 404.
   저장 요청 완전 동일 재전송 → 200·동일 ID. 같은 키의 weather 변경 → 409 IDEMPOTENCY_CONFLICT·retryable=false.
   같은 초안의 다른 확정 키 → 409 DIARY_DATE_CONFLICT·retryable=false, 기존 행 유지.
7. D도 별도 세션/대화/초안으로 같은 확정 키를 사용해 200·다른 ID를 확인합니다.
   C↔D 상세·세션 저장은 양방향 404, 월별 목록은 본인만, 무토큰 GET/저장은 401입니다.
   일반 회원의 RPC 직접 HTTP 호출은 Publishable key + 회원 JWT로 거절되는지 별도 확인합니다.
   DB 역할 거절 `42501`과 HTTP 403을 예상하며 게이트웨이 401·다른 코드만으로 RPC 권한 확인을 대신하지 않습니다.
   Secret key를 회원 JWT와 함께 직접 RPC에 보내지 않습니다.
8. BE 중지 후 같은 블록으로 재시작하고 C/D 월별·상세가 그대로 200인지 확인합니다.
   원래 session_id·확정 body 그대로 POST → 200·원래 ID 복구. 저장하지 않은 세션은 재시작 후 404입니다.
   필요하면 쓰기 키 없이 읽기·이미 저장된 요청 복구도 확인합니다. 신규 저장은 키 누락 시 503입니다.
9. timeout/응답 유실 시 성공·실패 판정을 보류하고 같은 회원·키·본문으로만 조회/명시적 재시도합니다.
   새 키로 우회하거나 자동 재시도하지 않습니다. 오류 원문·토큰/키가 노출되면 중단합니다.
   테스트 결과는 ID/본문/비밀값 없이 항목별 상태만 기록합니다. 신규 가상 fixture도 자동 삭제하지 않습니다.

후속 HTTP 저장은 요청별 커밋이므로 SQL Editor의 ROLLBACK에 포함되지 않습니다.
실제 Secret key HTTP·PostgREST schema cache·재시작 DB 복구·실제 동시성·Storage/브라우저 통합은 아직 미검증입니다.
자동 테스트의 모의 HTTP 통과와 SQL 파싱은 이 항목의 실행 성공을 뜻하지 않습니다.

2026-11-07 이번 준비 검증: BE `.venv` 전체 **467 passed**(`pytest -q -W error`), pip check·compileall 통과.
기존 462개에 migration 불변·RPC 본문 지문·기본 차단·롤백 경계·fixture 일치 정적 검사 5개를 추가했습니다.
기존 임시 `pglast 8.7`로 카탈로그 SQL 13문, 로컬 SQL 7문/PLpgSQL 1개,
원격 SQL 8문/PLpgSQL 1개, 위 읽기 전용 SQL 예시 3문 파싱 통과. PowerShell 블록 10개 AST 파싱 통과.
새 Python 의존성과 앱 동작 변경은 없습니다. 해당 구문을 DB나 실제 키로 실행한 결과는 아닙니다.

2026-11-07 **사용자 수동 보고**: A/B 각각 diaries 1행·diary_messages 3행, 비익명 로그인·서로 다른 회원 ID,
본인 조회·상대 조회 차단·알려진 가상 일기 ID 양방향 교차 조회·미인증 DB 거절 확인.
diary_assets는 양쪽 0행으로 격리 미완료이며 종료 코드 2는 미검증 자산 데이터 때문입니다.
이전 전부 0행 결과는 과거 기록입니다. 위 결과는 본 에이전트가 직접 실행한 검증이 아닙니다.

### 그림 원본 업로드 후속 작업

실제 JPEG/PNG 디코딩·크기/픽셀/해시 검증과 서버가 만든 회원/일기/자산 경로를 먼저 구현합니다.
비공개 Storage `upsert=false` 업로드, DB 자산 참조의 영속 요청 상태, 실패/응답 유실 시 고아 파일 복구를 설계합니다.
DB와 Storage는 같은 트랜잭션으로 묶이지 않으므로 재시도·정리 상태가 필요합니다.
자산이 있는 A/B의 실제 조회 격리·회원 직접 업로드 차단·서명 URL 수명도 별도 검증합니다.
공유 세션 저장소를 구현하기 전까지 미확정 대화는 단일 worker 메모리 한계를 유지합니다.

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
