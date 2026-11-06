# 최소 텍스트 대화·일기 초안 API — 팀 합의 전 잠정 계약

2026-11-06 기준 BE 구현 계약입니다. 기존 로컬 PROJECT_STATUS.md의 초안을 구현한 것이며,
FE·AI 담당자의 합의 또는 브라우저 통합 완료를 뜻하지 않습니다.
사용자 식별·날짜·소유권·종료 후 UX는 후속 합의 사항입니다.

## 범위와 경계

브라우저 FE → FastAPI BE → 기존 AI HTTP 서버의 `POST /api/chat`, `POST /api/diary` 경로입니다.
BE는 AI 소스·프롬프트·생성 로직·SDK를 가져오지 않습니다.
첫 질문과 전체 확정 이력을 BE가 관리합니다. FE는 자신의 최신 발언만 보냅니다.
DB, 그림 저장, 최종 일기 확인/저장, 이미지 생성, 음성, 로그인은 포함하지 않습니다.

단일 프로세스·한 이벤트 루프의 메모리 저장입니다. 재시작·reload 시 세션과 요청 캐시가 사라지고
이전 session_id는 404가 됩니다. 여러 worker를 사용하면 세션/중복 방지가 공유되지 않습니다.
세션/캐시에 TTL·영구 저장·자동 정리가 없으므로 가상 데이터로 하는 짧은 로컬 개발용입니다.
session_id와 Idempotency-Key는 인증 수단이 아닙니다. CORS도 접근 권한 검사를 대신하지 않습니다.
공개 서비스 배포 전 사용자 인증·소유권·저장 정책을 별도로 정해야 합니다. 실제 아동 데이터는 사용하지 않습니다.

## 공통 전송 규칙

- JSON/UTF-8, 요청 `Content-Type: application/json`.
- 정의하지 않은 본문 필드는 422로 거절합니다. FE는 전체 messages·역할·AI 주소를 지정할 수 없습니다.
- 시작 키, client_message_id, client_request_id: ASCII 영문/숫자/밑줄/하이픈으로 구성한 1~128자 문자열.
- API 응답의 `X-Request-ID`는 HTTP 전송별 새 ID이며 오류 본문의 request_id와 같습니다.
  성공 캐시 본문과 reply ID는 재사용하되 이 헤더는 달라집니다.
- 응답에 `Cache-Control: no-store`를 적용합니다.
- 개발 origin 기본값: `http://127.0.0.1:5500`, `http://localhost:5500`.
  허용 메서드 GET/POST, 요청 헤더 Content-Type/Idempotency-Key, 노출 헤더 X-Request-ID.
  origin은 환경변수 ALLOWED_ORIGINS의 쉼표 목록으로 변경하며 자격증명 전송은 허용하지 않습니다.

## GET /health

200:

```json
{"status":"ok"}
```

BE 앱 생존 확인입니다. AI 연결·키·외부 모델 준비 상태를 검사하지 않습니다.

## POST /api/chat-sessions

`Idempotency-Key: start-demo-001`을 필수로 보냅니다. 본문도 필수입니다.

```json
{}
```

201:

```json
{
  "session_id": "ses_example",
  "history_revision": 0,
  "reply": {
    "message_id": "msg_example_1",
    "role": "assistant",
    "text": "오늘은 무슨 일이 있었어?"
  },
  "finished": false,
  "mock": true,
  "fallback": false
}
```

BE는 AI에 `{"messages":[]}`를 보내고 검증한 첫 답변을 assistant 이력으로 보관한 뒤 세션을 공개합니다.
실패 시 활성 세션은 만들지 않습니다. 동일 키의 성공 재전송은 동일 201 본문·세션·답변을 반환하며
AI를 다시 호출하지 않습니다. 시작 키는 현재 BE 프로세스 전체에서 공유하므로 FE가 매 시작 시 고유하게 생성합니다.
FE 응답 유실 시에는 해당 키를 유지합니다.

## POST /api/chat-sessions/{session_id}/messages

요청:

```json
{
  "client_message_id": "turn-demo-001",
  "text": "가상 인물과 공놀이한 이야기야."
}
```

200:

```json
{
  "session_id": "ses_example",
  "history_revision": 1,
  "client_message_id": "turn-demo-001",
  "reply": {
    "message_id": "msg_example_2",
    "role": "assistant",
    "text": "그때 어떤 일이 있었는지 더 들려줄래?"
  },
  "finished": false,
  "mock": true,
  "fallback": false
}
```

ID는 불투명한 BE 발급 문자열이며 예시 접두사/UUID 내부 형식에 의존하지 않습니다.
client_message_id는 세션 안에서 발언별로 고유해야 합니다. 응답 유실 재전송에는 같은 ID·같은 text를 보냅니다.
동일한 문장을 다른 발언으로 다시 보내려면 새 ID를 사용합니다. 원문의 앞뒤 공백·줄바꿈도 내용에 포함됩니다.

FE `{who:'user',text}` → 요청 text → AI `{role:'child',text}`,
AI question → BE reply.text → FE `{who:'ai',text}`로 연결합니다.
FE의 Date.now 책 ID는 BE session_id를 대신하지 않습니다.
mock/fallback/finished는 엄격한 bool로 검사하고 그대로 전달합니다.
finished는 종료 의사 신호이며 일기 저장 완료가 아닙니다.
현재 구현은 finished=true 뒤에도 세션을 유지하고 추가 발언을 허용합니다.
자동 일기 생성·저장·세션 폐쇄는 없습니다. 종료 이후 UX는 팀 합의가 필요합니다.

`history_revision`은 이번에 추가한 응답 필드입니다. 첫 질문만 있을 때 0이며, 발언+답변을
확정할 때마다 1씩 증가합니다. 실패/초안 생성/성공 재전송은 증가시키지 않습니다.
캐시된 과거 발언 응답은 그때의 버전을 유지하므로 FE는 늦은 응답으로 최신 버전을 낮추지 않아야 합니다.

## POST /api/chat-sessions/{session_id}/diary-draft

요청(추가 Idempotency-Key 헤더 불필요):

```json
{"client_request_id":"draft-demo-001","history_revision":1}
```

history_revision은 FE가 마지막으로 확정한 대화 응답의 버전이며 엄격한 0 이상 정수입니다.
메시지·날짜·아이 식별자·AI 옵션·확인 여부는 받지 않습니다. 미정의 필드는 422입니다.
FE가 보내는 대화 원문을 신뢰해 초안을 만들지 않고 BE 메모리의 확정 이력만 사용합니다.

200 예시(가상 발언 1회 후 AI mock):

```json
{
  "session_id":"ses_example",
  "client_request_id":"draft-demo-001",
  "history_revision":1,
  "draft":{
    "draft_id":"draft_example",
    "status":"draft",
    "text":"가상 인물과 공놀이한 이야기야.",
    "pages":[{"text":"가상 인물과 공놀이한 이야기야.","source_ids":[1],"imageUrl":null}],
    "sources":[{"id":1,"text":"가상 인물과 공놀이한 이야기야."}],
    "fallback":true,
    "requires_confirmation":true,
    "mock":true
  }
}
```

BE의 공개 응답은 AI 원본 응답을 draft 안에 검증·매핑한 뒤 세션/요청/버전/draft_id/status를 붙인 DTO입니다.
AI로부터 받은 text/pages/sources/세 bool 값을 임의로 보충·수정하지 않습니다.
draft_id는 불투명한 초안 식별자이며 DB ID·영구 저장 URL·아이의 확인 증명이 아닙니다.
항상 status=draft, requires_confirmation=true입니다. 최종 일기 확정/수정/저장 API는 이번에 구현하지 않습니다.
실패해도 세션·확정 대화·이전 성공 초안을 지우거나 수정하지 않습니다.

BE는 다음 본문만 `POST ${AI_BASE_URL}/api/diary`에 전송합니다.
마지막 확정 assistant 답변까지 포함하며 중간 이력을 자르거나 요약하지 않습니다.

```json
{
  "messages":[
    {"role":"assistant","text":"오늘은 무슨 일이 있었어?"},
    {"role":"child","text":"가상 인물과 공놀이한 이야기야."},
    {"role":"assistant","text":"그때 어떤 일이 있었는지 더 들려줄래?"}
  ]
}
```

AI 원본 응답은 위 공개 예시의 draft에서 draft_id/status를 제외한 6개 필드입니다.
AI main의 server.py/ConversationRequest/DiaryResponse/DiaryEngine.make_diary와 대조했습니다.
`sources[].id` 및 `pages[].source_ids`는 **이 요청의 전체 messages 배열의 0-based 인덱스**입니다.
assistant도 배열 위치에 포함되므로 위 예시의 child는 1입니다. BE message_id/client_message_id로 변환하지 않습니다.
다음 child는 3이며, 이 인덱스는 응답의 history_revision에 해당하는 이력을 기준으로 해석합니다.

BE의 응답 검증은 AI main의 실제 생성 결과보다 느슨한 모델 자료형에만 의존하지 않습니다.

- 6개 최상위 필드와 각 page/source 필드 필수, 미지정 필드 거절. bool·int·str 엄격 검사.
- text/page.text는 공백뿐이지 않은 유효 Unicode 문자열, 각 1~20,000자.
  AI의 정상 문장 생성(최대 20문장×500자)과 원문 fallback을 모두 수용하며 1,500자 이미지 입력 한도를 적용하지 않습니다.
- pages는 1~6개, sources는 1~60개이며 source.text는 1~2,000자.
  sources는 전체 child 발언의 인덱스·원문·순서와 정확히 같아야 합니다.
- 각 page.source_ids는 비어 있지 않은 중복 없는 정수 배열이며 실제 child 인덱스만 가리켜야 합니다.
  모든 출처를 반드시 페이지에서 사용하도록 강제하지는 않습니다(AI 생성 계약도 이를 요구하지 않음).
- text와 pages의 연결 내용이 공백/줄바꿈 차이 외에 달라지면 거절합니다. 검사할 때만 공백을 정규화하며 응답 원문은 보존합니다.
- imageUrl은 null이어야 하고 requires_confirmation은 true여야 합니다. 일기 단계에서 이미지·확정 상태를 수용하지 않습니다.

빈 초안·누락/잘못된 필드·근거 인덱스/원문 불일치는 502 AI_BAD_RESPONSE로 기록합니다.
BE의 구조 검사는 생성 문장의 사실성/의미 검증을 보장하지 않습니다. 아이의 확인 단계가 별도로 필요합니다.
fallback=true는 AI 검토 실패/원문 대체 신호이며 오류가 아니라 유효한 초안일 수 있습니다.
mock=true는 실제 모델 생성 결과가 아닙니다. fallback/mock은 FE에서 구분해 표시해야 합니다.

### 초안 예약·중복·충돌

client_request_id는 세션 내 초안 요청별로 고유합니다. 발언 ID와는 별도 namespace입니다.
같은 ID의 동일 요청은 history_revision까지 같아야 합니다.

| 상황 | 결과 |
| --- | --- |
| child가 없는 세션(실패한 발언은 child에 포함하지 않음) | 422 NO_CHILD_MESSAGES, AI 호출/예약 없음 |
| 새 요청의 버전이 현재 확정 버전과 다름 | 409 HISTORY_REVISION_CONFLICT, AI 호출/예약 없음 |
| 같은 ID+다른 버전 | 409 IDEMPOTENCY_CONFLICT, 이전 성공/실패/연결 실패 ID에도 적용 |
| 같은 ID+버전 처리 중 재전송 | 409 REQUEST_IN_PROGRESS, retryable=true |
| 새 ID 초안 또는 새 발언이 같은 세션의 처리와 충돌 | 409 SESSION_BUSY, retryable=true, 자동 큐 없음 |
| 같은 ID+버전 성공 재전송 | 같은 200 본문/draft_id, AI 추가 호출 없음 |
| 과거 성공 초안을 이후 발언/새 초안 생성 뒤 또는 다른 요청 처리 중 재전송 | 그때의 버전/초안 그대로 반환, 현재 대화의 초안이라고 해석하면 안 됨 |
| 연결 전 실패 | 기존 대화 API처럼 같은 ID+버전으로 명시적 재시도 가능. 그 사이 대화 버전이 바뀌면 409 |
| AI 오류/불량 응답/timeout/취소 | 기존 공통 오류 및 캐시 정책 사용. 같은 ID로 AI 재호출 없음 |
| 같은 버전+새 ID | 사용자가 명시적으로 요청한 별도 생성 시도 허용. 자동 반복 금지 |

예약·스냅샷·응답 반영 사이에는 같은 세션의 새 메시지/초안을 막습니다. 다른 세션은 독립 처리합니다.
이미 성공한 발언/초안의 캐시 조회는 busy/현재 버전 검사보다 먼저 수행합니다.
초안 생성은 history_revision과 확정 대화 이력을 바꾸지 않습니다. AI 오류 후에도 대화는 이어갈 수 있습니다.
AI timeout 뒤 원격 처리는 계속될 수 있으므로 새 ID로 자동 재요청하지 않습니다.
전체 프로세스 밖의 정확히 한 번 실행은 보장하지 않으며 재시작 시 초안·요청 기록도 모두 소실됩니다.

finished=true를 선행 조건으로 요구하지 않습니다. child 발언이 있으면 FE의 명시적 생성 요청을 받습니다.
종료 버튼 UX, 같은 버전 재생성 허용/최신 초안 선택, 과거 초안 표시, 최종 확인/보관 정책은 팀 합의 전 잠정입니다.

## BE → 기존 AI

첫 후속 요청 `POST ${AI_BASE_URL}/api/chat`:

```json
{
  "messages": [
    {"role":"assistant","text":"오늘은 무슨 일이 있었어?"},
    {"role":"child","text":"가상 인물과 공놀이한 이야기야."}
  ]
}
```

기대하는 HTTP 200 본문:

```json
{
  "question":"그때 어떤 일이 있었는지 더 들려줄래?",
  "finished":false,
  "mock":true,
  "fallback":false
}
```

다음 발언에서는 직전 question을 assistant로 넣은 전체 확정 이력과 새 child를 순서대로 전송합니다.
세션/요청 ID·사용자·날짜는 AI 본문에 넣지 않습니다.
AI 질문은 공백뿐이지 않은 1~150자 문자열이어야 하고 세 bool 필드는 모두 필수입니다.
추가 필드·숫자 bool·문자열 bool·비JSON·오류 HTTP·리다이렉트는 성공으로 처리하지 않습니다.
이 150자 한도는 조사된 기존 AI 질문 검사 상한을 따른 잠정 규칙입니다.
조사 기준: AI `main` 커밋 `cca79ebda092b53fcfc36d0b26ec2d7cc960842a`.
2026-11-06 git ls-remote로 원격 main과 같은 해시임을 확인했습니다. 실행 중 서버의 커밋은 HTTP 응답으로 확인할 수 없습니다.

## 입력 한도와 순서

- text는 엄격한 문자열, 원문 기준 Unicode 코드 포인트 1~2,000자입니다.
  공백만인 입력·잘못된 Unicode surrogate는 거절하고 유효 원문은 trim하지 않습니다.
  JS 문자열.length는 이모지에서 다를 수 있으므로 FE의 표시도 코드 포인트 기준으로 맞춥니다.
- 전체 메시지는 최대 60개, 전체 텍스트는 최대 20,000자입니다.
- 새 요청은 `기존 메시지 수 + child 1 + assistant 1 <= 60`,
  `기존 글자 수 + 새 text 길이 + 답변 여유 150 <= 20,000`일 때만 예약합니다.
- 첫 assistant를 포함하므로 최대 29회 왕복입니다. 길이 한도로 더 일찍 막힐 수 있습니다.
  이력을 자르거나 자동 요약하지 않습니다. 이미 성공한 요청의 재전송은 현재 한도보다 먼저 조회합니다.
- 한 세션의 요청 예약·중복 확인·성공 반영에는 await가 없으며 같은 이벤트 루프에서 원자적으로 실행됩니다.
  AI 대기는 세션별로 독립적입니다. 다른 세션을 막거나 요청을 자동으로 줄 세우지 않습니다.
- AI 성공 검증 뒤 child+assistant와 성공 캐시를 함께 반영합니다. 실패/timeout/취소는 확정 이력을 바꾸지 않습니다.
  AI 대기는 요청 안에서 await하며 별도 백그라운드 생성 작업은 남기지 않습니다.

## 중복·실패·재시도

| 상황 | 결과 |
| --- | --- |
| 같은 시작 키의 성공 재전송 | 기존 201 본문/세션/첫 답변, AI 재호출 없음 |
| 같은 세션+같은 발언 ID+같은 원문 성공 재전송 | 기존 200 본문/답변, 이력 추가 없음 |
| 같은 발언 ID+다른 원문 | 409 IDEMPOTENCY_CONFLICT. 실패한 ID에도 원문 일치 필요 |
| 같은 키/발언이 처리 중 | 409 REQUEST_IN_PROGRESS, retryable=true |
| 같은 세션에 다른 발언 처리 중 | 409 SESSION_BUSY, retryable=true |
| 입력/세션/한도 검증 실패 | AI 호출·요청 예약·이력 변경 없음 |
| 연결/연결 timeout/풀 확보 실패 | 503 AI_CONNECTION_FAILED, retryable=true. 같은 ID·원문으로 명시적 재시도 가능 |
| AI 오류 HTTP/잘못된 본문 | 오류 기록 보관. 같은 ID 재전송은 같은 오류 코드·상태 반환, AI 재호출 없음 |
| 읽기/쓰기/전체 timeout | 504 AI_OUTCOME_UNKNOWN, retryable=false. 오류 캐시, 확정 이력 불변 |
| 쓰기/읽기/프로토콜 오류 또는 취소 | 502 AI_OUTCOME_UNKNOWN, retryable=false. 취소된 원 요청은 응답을 받지 못할 수 있음 |
| BE가 성공한 뒤 FE만 응답 유실 | 같은 ID 재전송으로 성공 본문 복구 |
| BE 재시작/다른 프로세스 | 세션/캐시 보장 없음. 이전 세션 404 |

AI 자동 재시도는 하지 않습니다. retryable은 같은 ID·원문으로 BE에 **명시적 재요청**할 수 있다는 뜻입니다.
AI_UNAVAILABLE는 공급자 키·권한·한도 문제가 섞일 수 있으므로 retryable=false입니다.
새 ID로 다시 보내면 별도 AI 생성 시도가 되므로 결과 불명 상태를 숨겨 자동 반복하면 안 됩니다.
AI 자체는 무상태이고 중복 방지가 없으므로 프로세스 밖이나 timeout 이후 외부 처리의 정확히 한 번 실행은 보장하지 않습니다.
전송이 취소되어도 AI 서버/공급자의 실행·비용 취소를 보장하지 않습니다.

## 오류 JSON

```json
{
  "error": {
    "code":"AI_OUTCOME_UNKNOWN",
    "message":"응답을 확인하지 못했어요. 자동으로 다시 보내지 않습니다.",
    "retryable":false
  },
  "request_id":"req_example"
}
```

| HTTP | 코드 |
| --- | --- |
| 422 | VALIDATION_ERROR, CONVERSATION_LIMIT_REACHED, NO_CHILD_MESSAGES |
| 404 | SESSION_NOT_FOUND |
| 409 | IDEMPOTENCY_CONFLICT, REQUEST_IN_PROGRESS, SESSION_BUSY, HISTORY_REVISION_CONFLICT |
| 502 | AI_BAD_RESPONSE, AI_REQUEST_REJECTED, AI_OUTCOME_UNKNOWN |
| 503 | AI_CONNECTION_FAILED, AI_UNAVAILABLE |
| 504 | AI_OUTCOME_UNKNOWN |
| 500 | INTERNAL_ERROR |

알 수 없는 경로/허용하지 않은 방법은 HTTP_ERROR로 공통화합니다.
AI의 4xx는 FE의 4xx로 그대로 노출하지 않습니다. AI 429/503은 AI_UNAVAILABLE,
그 외 4xx는 AI_REQUEST_REJECTED, 그 외 200 아닌 응답은 AI_BAD_RESPONSE입니다.
오류 본문/일반 로그에 사용자 입력 전문·공급자 원본 오류·키를 싣지 않습니다.
처리 중/세션 busy/연결 실패 외에는 retryable=false입니다.

## FE 연결 시 확인할 사항

1. 시작 직전에 고유 Idempotency-Key를 생성하고, 최초 assistant 질문을 화면에 표시합니다.
2. 발언 전 client_message_id를 만들고 성공/실패/응답 유실 동안 원문과 함께 유지합니다.
3. 요청 중 신규 전송·종료·세션 전환을 제어합니다. 실패 시 입력을 보존하고 확정 이력처럼 표시하지 않습니다.
4. reply.message_id 기준으로 중복 말풍선을 막고 응답 당시 session_id가 현재 화면과 같은지 확인합니다.
5. mock/fallback/finished와 한도/오류 상태 UX, 종료 후 다음 단계를 담당자와 합의합니다.
6. 초안 생성 시 최신 history_revision과 고유 client_request_id를 보내고 응답 유실 시 그대로 재전송합니다.
   늦게 도착한 과거 버전 초안을 현재 대화의 결과로 덮어쓰지 않습니다. status=draft를 저장 완료로 표시하지 않습니다.

## 검증 범위와 참고

`pytest`는 ASGITransport로 BE를 호출하고 MockTransport로 AI HTTP 경계를 검사합니다.
실제 AI 서버·외부 모델·브라우저 연결 없이도 전체 이력, 순서, 오류, 동시 요청, 취소를 검증합니다.
2026-11-06 BE .venv: 179 passed(기존 70개 포함). 별도 명시적 mock 통합 스크립트는
BE ASGI → 실제 로컬 AI HTTP 대화 3회/초안 1회, 전체 확정 이력·출처·재전송을 통과했습니다.
FE 브라우저, 별도 BE 소켓 연결, 실제 모델 품질, 최종 확인/영구 저장은 이번 검증에 포함하지 않습니다.
실제 AI 별도 프로세스 연동 절차와 미실행 조건은 [README](../README.md)를 참조하세요.
HTTP client의 수명은 [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/),
모의 HTTP 경계는 [HTTPX transports](https://www.python-httpx.org/advanced/transports/) 방식에 따릅니다.
