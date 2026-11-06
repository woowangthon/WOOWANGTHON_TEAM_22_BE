# 최소 텍스트 대화 API — 팀 합의 전 잠정 계약

2026-11-06 기준 BE 구현 계약입니다. 기존 로컬 PROJECT_STATUS.md의 초안을 구현한 것이며,
FE·AI 담당자의 합의 또는 브라우저 통합 완료를 뜻하지 않습니다.
사용자 식별·날짜·소유권·종료 후 UX는 후속 합의 사항입니다.

## 범위와 경계

브라우저 FE → FastAPI BE → 기존 AI HTTP 서버의 `POST /api/chat` 경로입니다.
BE는 AI 소스·프롬프트·생성 로직·SDK를 가져오지 않습니다.
첫 질문과 전체 확정 이력을 BE가 관리합니다. FE는 자신의 최신 발언만 보냅니다.
DB, 그림 저장, 일기 초안, 이미지 생성, 음성, 로그인은 포함하지 않습니다.

단일 프로세스·한 이벤트 루프의 메모리 저장입니다. 재시작·reload 시 세션과 요청 캐시가 사라지고
이전 session_id는 404가 됩니다. 여러 worker를 사용하면 세션/중복 방지가 공유되지 않습니다.
세션/캐시에 TTL·영구 저장·자동 정리가 없으므로 가상 데이터로 하는 짧은 로컬 개발용입니다.
session_id와 Idempotency-Key는 인증 수단이 아닙니다. CORS도 접근 권한 검사를 대신하지 않습니다.
공개 서비스 배포 전 사용자 인증·소유권·저장 정책을 별도로 정해야 합니다. 실제 아동 데이터는 사용하지 않습니다.

## 공통 전송 규칙

- JSON/UTF-8, 요청 `Content-Type: application/json`.
- 정의하지 않은 본문 필드는 422로 거절합니다. FE는 전체 messages·역할·AI 주소를 지정할 수 없습니다.
- 시작 키와 client_message_id: ASCII 영문/숫자/밑줄/하이픈으로 구성한 1~128자 문자열.
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
조사 기준: AI `origin/main`의 로컬 캐시 커밋 `cca79ebda092b53fcfc36d0b26ec2d7cc960842a`.
현재 원격 최신 상태나 AI 실행 성공을 새로 확인한 것은 아닙니다.

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
| 422 | VALIDATION_ERROR, CONVERSATION_LIMIT_REACHED |
| 404 | SESSION_NOT_FOUND |
| 409 | IDEMPOTENCY_CONFLICT, REQUEST_IN_PROGRESS, SESSION_BUSY |
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

## 검증 범위와 참고

`pytest`는 ASGITransport로 BE를 호출하고 MockTransport로 AI HTTP 경계를 검사합니다.
실제 AI 서버·외부 모델·브라우저 연결 없이도 전체 이력, 순서, 오류, 동시 요청, 취소를 검증합니다.
실제 AI 별도 프로세스 연동 절차와 미실행 조건은 [README](../README.md)를 참조하세요.
HTTP client의 수명은 [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/),
모의 HTTP 경계는 [HTTPX transports](https://www.python-httpx.org/advanced/transports/) 방식에 따릅니다.
