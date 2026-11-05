# WOOWANGTHON_TEAM_22_BE

오늘,그리고 프로젝트의 BE 저장소입니다. FE와 AI는 각각 별도 Git 저장소에서 관리합니다.

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
현재는 애플리케이션 코드와 의존성 명세가 없어 서버 실행과 테스트 명령은 추후 추가합니다.

### Git 작업 흐름

변경 전에 `git status`와 현재 브랜치를 확인하고 기존 작업을 보존합니다.
깨끗한 작업 트리에서 다음과 같이 최신 `main`을 기준으로 목적별 작업 브랜치를 만듭니다.

```powershell
git switch main
git fetch origin main
git merge --ff-only origin/main
git switch -c feat/작업명
```

초기 환경 설정 브랜치는 `feat/be-bootstrap`입니다. 작업과 검증을 완료한 뒤 변경 목적 단위로 커밋하고,
작업 브랜치를 푸시하여 PR을 통해 `main`에 반영합니다. 커밋 전에 `git diff --cached`로 스테이징 내용을 확인합니다.
커밋 메시지는 `chore: BE 초기 개발 환경 설정`처럼 적절한 유형과 구체적인 한국어 설명으로 작성합니다.

실제 환경변수 파일(`.env`, `.env.*`), 비밀키와 `.venv`는 Git에서 제외합니다.
공유용 `.env.example`에는 비밀값 없이 변수 이름과 예시만 작성합니다.
`AGENTS.md`는 각 PC의 `.git/info/exclude`에 `/AGENTS.md`를 등록해 로컬 전용으로 관리합니다.
