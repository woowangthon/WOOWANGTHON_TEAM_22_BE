# 오늘,그리고 · BE

2026 AI 우왕톤 22팀 **NEAR**의 그림일기·고민상담 서비스 백엔드입니다. 어린 사용자가 AI와 하루를 이야기하고 그림일기와 4컷을 남기며, 우냥이에게 고민을 표현하도록 돕습니다.

[서비스 바로가기](https://today-and-near-team22.pages.dev/) · [시연 영상](https://www.youtube.com/watch?v=1aYGyOrBU4E) · [FE / 전체 프로젝트 소개](https://github.com/woowangthon/WOOWANGTHON_TEAM_22_FE) · [AI 저장소](https://github.com/woowangthon/WOOWANGTHON_TEAM_22_AI)

운영 API 기준 주소: `https://today-and-near-api.onrender.com/` · [상태 확인](https://today-and-near-api.onrender.com/health) · [API 문서](https://today-and-near-api.onrender.com/docs)

루트(`/`)의 404는 웹 화면이 없기 때문입니다. Render 무료 서버가 유휴 상태에서 다시 시작되면 첫 응답에 약 1분 또는 그 이상 걸릴 수 있습니다. [공식 안내](https://render.com/docs/free#spinning-down-on-idle)를 참고해 잠시 기다린 뒤 재시도하세요. 이미 접수된 4컷은 새 생성 요청보다 **기존 작업 상태 조회**가 우선입니다.

## BE의 역할과 구조

BE는 회원 인증과 기록 소유권, 일기·그림·만화·상담 저장, 재화·상점·방·나무, AI 호출과 4컷 작업 처리를 담당합니다. 화면은 FE, 질문·일기 초안·장면·이미지·고민 응답 생성은 AI 저장소에서 관리합니다.

```text
FE ── 회원 access token + 요청 ──> BE API
                                  ├─ Supabase Auth: 회원 검증
                                  ├─ PostgreSQL / Storage: 기록·재화·이미지
                                  └─ AI HTTP 서버: 생성 요청·결과
```

Python·FastAPI·Pydantic·HTTPX·Pillow를 사용하며, 의존성 버전은 [requirements.txt](requirements.txt)에 고정되어 있습니다.

| 경로 | 책임 |
| --- | --- |
| [app/main.py](app/main.py), [app/config.py](app/config.py) | 앱 생성, 클라이언트 생명주기, CORS와 환경 설정 |
| [app/api/](app/api/) | 요청·응답과 API 경로 |
| [app/services/](app/services/) | 일기·그림·4컷·상담·재화 등의 업무 처리 |
| [app/clients/](app/clients/) | Supabase Auth·DB·Storage 및 AI HTTP 통신 |
| [app/auth.py](app/auth.py), `app/*_schemas.py` | 회원 검증과 기능별 데이터 계약 |
| [app/session_store.py](app/session_store.py), [app/persistent_session_store.py](app/persistent_session_store.py) | 일기 대화·초안의 메모리 저장 및 선택형 DB 저장 |
| [app/runtime.py](app/runtime.py) | 명시적으로 선택하는 4컷 worker 실행 팩토리와 `/ready` |
| [supabase/](supabase/), [docs/](docs/) | DB 정의·SQL 후보·기능별 계약과 검증 기록 |
| [tests/](tests/), [scripts/](scripts/) | 자동 검사와 별도 수동 연동 검증 도구 |

## 주요 API

아래 경로는 `/api` 기준입니다. 본문·응답 필드는 실행 중인 `/docs`와 각 기능의 스키마를 참고하세요.

| 기능 | 경로와 동작 |
| --- | --- |
| AI 일기 대화 | `POST /chat-sessions`, `POST /chat-sessions/{session_id}/messages`로 시작·발언 |
| 초안·확정 저장 | `POST /chat-sessions/{session_id}/diary-draft`, `POST /chat-sessions/{session_id}/diaries` |
| 달력·상세·그림 | `GET /diaries?month=YYYY-MM`, `GET /diaries/{diary_id}`, `POST /diaries/{diary_id}/drawing` |
| 일기 수정·삭제 | `PUT /diaries/{diary_id}/edits`, `DELETE /diaries/{diary_id}` |
| 4컷 장면·생성 접수 | `POST /chat-sessions/{session_id}/comic-plans`, `POST /chat-sessions/{session_id}/comic-previews` |
| 4컷 상태·결과 | `GET /chat-sessions/{session_id}/comic-requests/{client_request_id}`, `GET /chat-sessions/{session_id}/comic-previews/{preview_id}`, `POST`·`GET /diaries/{diary_id}/comics` |
| 우냥이 고민상담 | `GET /worry/policy`, `POST`·`GET /worry/sessions`, `GET /worry/sessions/{session_id}`, `POST /worry/sessions/{session_id}/messages`, `POST /worry/sessions/{session_id}/finish` |
| 방·보상·상점 | `GET /me/room`, `POST /me/rewards/sync`, `GET /shop/items`, `POST /shop/purchases`, `POST /shop/refunds` |
| 테마·가구·나무 | `PUT /me/room/theme`, `PUT`·`DELETE /me/room/placements/{item_id}`, `POST /me/tree/water` |
| 결과 불명 요청 조회 | `GET /diaries/{diary_id}/edits/{client_request_id}`, `GET /me/tree/water/{client_request_id}` |

### 회원 인증과 데이터 소유권

- 위 기능 API는 `Authorization: Bearer <Supabase access token>`을 요구합니다. 매 요청에서 Supabase Auth로 **비익명 회원**을 검증하고, 검증된 UUID로 소유권을 확인합니다. 공개 `/health`는 로그인 없이 호출합니다.
- 브라우저가 임의로 보낸 회원 ID를 소유자로 신뢰하지 않습니다. 일기·그림·만화·상담은 본인 기록만 조회·변경할 수 있으며, 타 회원의 기록과 존재하지 않는 기록을 구분해 노출하지 않습니다.
- 공개용 Publishable key와 서버 전용 Secret key의 역할을 분리합니다. 이미지 원본은 비공개 Storage에 보관하고, 소유자에게 유효기간 300초의 서명 URL을 제공합니다.
- 요청 키와 대화·편집 버전을 검사합니다. 결과가 불명확한 쓰기는 같은 요청의 결과를 먼저 확인하며, 새로운 ID로 무조건 반복하지 않습니다.

### 일기·그림·4컷

AI 대화에서 초안을 만든 뒤 사용자가 확인한 `draft_id`와 `history_revision`으로 일기를 저장합니다. **저장 전 초안 확인**과 **저장 후 수정 API**를 구분하며, 일기 수정 시 `edit_version`을 확인합니다.

그림 업로드는 PNG/JPEG를 검사·정규화해 저장합니다. 업로드 실패 시 같은 일기·같은 그림으로 재시도하고, 이미 저장한 그림과 충돌하면 덮어쓰지 않습니다.

4컷은 원문과 장면 확인 후 작업을 DB에 접수하고 worker가 AI에 컷별 생성을 요청합니다. 확정 실패(`failed`)와 결과 불명(`unknown`)을 구분하며, 응답 유실 시 기존 요청 ID로 조회합니다. 생성 결과를 확보한 뒤 DB 기록이 실패하면 결과 저장을 재시도합니다. **프로세스 종료 전에 DB/Storage에 기록하지 못한 모든 결과의 복구를 보장하지는 않습니다.**

관련 계약: [4컷 작업](docs/COMIC_JOBS_V1_20261111.txt) · [일기 수정·환불](docs/DIARY_EDITS_REFUNDS_V04.txt) · [방·상점·나무](docs/ROOM_REWARDS_V031.txt) · [요청 복구](docs/REQUEST_RECOVERY_API_PROPOSAL_20261111.txt)

### 우냥이 고민상담

일기와 별도로 대화·요청 상태를 DB에 저장합니다. 기능 활성화 시 정상 로그인 회원이 JSON `{}`와 `Idempotency-Key`로 상담을 시작합니다. 본인의 진행 중인 상담을 조회해 이어 쓰고, 종료 후에는 읽기만 가능합니다. 생성 후 **168시간(7일)**이 지나면 조회·이어쓰기를 제한하며, 실제 삭제 시점은 정리 작업의 실행 상태에 따라 달라집니다.

기본 실행 설정에서는 비활성입니다. 개발 환경의 DB 계약과 BE·AI 활성화 설정을 함께 준비해야 합니다. 현재 요청 계약, 소유권·만료 처리와 DB 재현 순서는 [우냥이 배포 계약](docs/WORRY_DEPLOY_CONTRACT_20261115.txt)에 정리되어 있습니다. 전문 심리치료나 진단 기능으로 제공하지 않습니다.

## BE ↔ AI 통신

| BE 처리 | AI 경로 |
| --- | --- |
| 일기 질문·초안 | `POST /api/chat`, `POST /api/diary` |
| 4컷 장면·컷별 이미지 | `POST /api/visual-story`, `POST /api/visual-story/image` |
| 우냥이 응답 | `POST /api/worry/chat` |

BE가 필요한 대화 이력과 확인한 입력을 전달하고 AI의 JSON 응답을 검증합니다. AI 서버는 회원 인증이나 영구 DB 저장을 담당하지 않습니다. HTTPX 통신에는 제한시간을 적용하며, 오류를 사용자용 코드로 변환하고 지원되는 경우 `Retry-After`를 전달합니다.

## 로컬 실행 · PowerShell

**Python 실행과 패키지 설치는 BE 루트의 `.venv`를 사용합니다.** 기존 개발 환경은 Python 3.14.3이며, 이 버전을 모든 환경의 필수 버전으로 고정한 것은 아닙니다. 정상 동작하는 `.venv`가 있으면 재사용하세요.

```powershell
git clone https://github.com/woowangthon/WOOWANGTHON_TEAM_22_BE.git
cd WOOWANGTHON_TEAM_22_BE
# .venv가 없을 때만 생성
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
```

[AI README](https://github.com/woowangthon/WOOWANGTHON_TEAM_22_AI#readme)에 따라 별도 터미널에서 AI를 실행하고, BE 터미널에 개발 환경의 아래 변수를 설정합니다. [.env.example](.env.example)은 참고 양식이며 **BE는 `.env`를 자동 로딩하지 않습니다.** 운영 설정을 복사하지 말고 개발용 Supabase를 사용하세요.

| 환경변수 이름 | 용도 |
| --- | --- |
| `AI_BASE_URL` | AI 서버 주소 |
| `ALLOWED_ORIGINS` | FE에서 접속하는 origin 목록 |
| `SUPABASE_URL` | 개발용 Supabase 주소 |
| `SUPABASE_PUBLISHABLE_KEY` | 회원 인증·조회용 Publishable key |
| `SUPABASE_SECRET_KEY` | DB·Storage 쓰기에 사용하는 서버 전용 Secret key |
| `SESSION_STORE_MODE` | 일기 대화 저장 방식. 기본 메모리 모드의 한계는 아래 참고 |
| `WORRY_ENABLED` | 우냥이 API 활성화 여부. AI의 `WORRY_ENABLE_MODEL`과 별도 설정 |
| `COMIC_WORKER_ENABLED` | `app.runtime` 팩토리에서 사용하는 4컷 worker 활성화 여부 |
| `AI_CONNECT_TIMEOUT_SECONDS`, `AI_READ_TIMEOUT_SECONDS`, `AI_WRITE_TIMEOUT_SECONDS`, `AI_POOL_TIMEOUT_SECONDS`, `AI_REQUEST_TIMEOUT_SECONDS` | AI 연결·읽기·쓰기·풀 대기·전체 요청 제한시간 |
| `AUTH_REQUEST_TIMEOUT_SECONDS`, `DB_REQUEST_TIMEOUT_SECONDS` | 인증·DB 요청 제한시간 |

환경변수 값이나 키는 README·커밋·FE에 기록하지 않습니다. PowerShell의 `$env:변수명` 또는 사용 중인 secret 주입 방식으로 설정합니다. BE 기본 AI 주소는 로컬 8002 포트이며, FE 개발 origin은 로컬 5500 포트입니다.

일반 API 개발 실행:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

개발 API 문서는 `http://127.0.0.1:8000/docs`, 상태 확인은 `http://127.0.0.1:8000/health`입니다. 인증·저장 설정이 없으면 해당 기능은 사용할 수 없습니다. `/health` 성공만으로 AI·DB 연결 성공을 판정하지 않습니다.

**4컷 worker까지 실행할 때**는 개발 DB의 작업 스키마·키·AI를 준비하고 `COMIC_WORKER_ENABLED`를 활성화한 뒤, 위 명령 대신 다음 팩토리를 사용합니다. 두 BE 서버를 같은 포트에 동시에 실행하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.runtime:create_runtime_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

일반 `app.main:app` 실행은 worker를 켜지 않습니다. `app.runtime`은 메모리 일기 세션 설정과 worker용 DB 설정을 검사하며 `/ready`를 제공합니다. 이는 선택하는 실행 구성으로, Render 통합 배포의 전체 설정을 대신하지 않습니다.

## Supabase 스키마 재현 자료

| 자료 | 범위 |
| --- | --- |
| [supabase/migrations/](supabase/migrations/) | 001~004: 회원 일기·자산, 비공개 Storage 정책, 확정 저장, 그림 업로드 |
| [수동 설치·검증 안내](docs/sql/dashboard_phase2_20261111/README_APPLY_V04.txt) | 방·보상, 4컷, 수정·환불, 4컷 작업 SQL의 순서와 확인 사항 |
| [supabase/candidates/](supabase/candidates/) | 기능별 SQL 후보와 선택형 세션 영속화 005 |
| [우냥이 현재 계약·재현 순서](docs/WORRY_DEPLOY_CONTRACT_20261115.txt) | 초기 상담 스키마 → v2 정상 회원 접근 패치 → 선택형 만료 정리 일정 |

빈 **별도 개발 DB**에서 문서의 전제와 적용 순서를 검토하는 자료입니다. 이미 구성한 운영 DB에 재실행하는 안내가 아니며, 서버 시작 시 자동으로 SQL을 적용하지 않습니다.

기본 `SESSION_STORE_MODE=memory`에서는 저장 전 일기 대화와 초안이 서버 재시작 시 사라질 수 있습니다. [005 후보](supabase/candidates/session_persistence_v005.sql)는 별도 적용·실DB 검증 전까지 사용하지 않습니다. 우냥이의 DB 저장·이어쓰기와 일기 세션의 저장 방식은 별개입니다.

## 테스트와 검증 범위

BE 루트에서 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

- `tests/`는 인증·소유권, 대화·초안·일기·그림, 4컷 작업, 상점·방·나무, 상담, 재시도·응답 유실을 검사합니다. 외부 Auth·DB·Storage·AI는 주로 합성 대역을 사용합니다.
- 우냥이 PostgreSQL 검사는 `WORRY_TEST_PG_ROOT`와 허용된 로컬 포트를 준비한 별도 격리 환경에서 실행합니다. 설정이 없으면 관련 검사는 건너뜁니다. 상세 조건은 [우냥이 계약 문서](docs/WORRY_DEPLOY_CONTRACT_20261115.txt)를 따릅니다.
- `scripts/verify_*.py`는 일부 실제 Auth·DB·Storage 접근을 포함하는 별도 수동 도구입니다. 일반 자동 테스트와 구분하며 운영 계정으로 자동 실행하지 않습니다.
- 과거 검사 범위와 당시 제약은 [BE 검토 기록](docs/BE_MAIN_REVIEW_20261112.txt)에 있습니다. 작성 시점의 제안·미구현 표시는 현재 코드와 구분해 읽어야 합니다. 합성 테스트 성공은 실제 모델 품질이나 운영 DB 상태의 증거가 아닙니다.

현재 README는 최신 main의 구현과 공개 API 명세를 기준으로 정리했습니다. **Render의 실제 실행 Live SHA는 미확인**이며, 공개 상태 응답·명세 일치만으로 운영 코드 버전까지 같다고 단정하지 않습니다.
