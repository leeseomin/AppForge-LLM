# OpenAppForge

**자연어 한 줄을 검증된 소프트웨어 앱의 소스 인계본까지 진행하는 개발 에이전트입니다.**

[English](README.md) · [아키텍처](docs/ARCHITECTURE.md) · [안전 모델](docs/SAFETY.md) · [에이전트 실행 규약](AGENT_GUIDE.md)

OpenAppForge는 AI 코딩 어시스턴트를 소프트웨어 제작 에이전트로 바꾸는 파이프라인형 실행 시스템입니다. 입력 명령을 적합한 개발 파이프라인으로 분류하고, 단계별 작업 패킷을 코딩 에이전트에 전달한 뒤, 구조화 산출물·테스트·빌드·보안 검사·릴리스 준비 상태를 검증합니다. 각 단계는 JSON 체크포인트로 저장되어 실패 후 재시도하거나 중단 지점부터 재개할 수 있습니다.

핵심 분리는 다음과 같습니다.

- **코딩 에이전트:** 추론, 소스 수정, 구현 문제 해결
- **OpenAppForge:** 제작 절차, 도구 계약, 산출물 스키마, 안전 경계, 리뷰 게이트, 재개 가능한 상태

별도의 다중 LLM 오케스트레이션 서버는 필요하지 않습니다. Codex/Claude Code를 자동 호출하는 오토파일럿 모드와, Cursor·Copilot·Windsurf·Gemini CLI 등이 저장소의 실행 규약을 직접 따르는 에이전트 네이티브 모드를 모두 지원합니다.

## 한 줄 실행

필수 조건은 Python 3.11 이상과 Codex CLI, Claude Code CLI 또는 사용자 정의 코딩 에이전트 명령 중 하나입니다.

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e .

appforge forge \
  "개인 지출을 CSV로 불러오고 예산을 관리하는 반응형 웹앱을 만들어라. 로컬 우선 저장, 테스트, Docker 구성을 포함하라" \
  --driver auto \
  --allow-network
```

`forge` 한 명령이 파이프라인 선택, 프로젝트 초기화, 요구사항·설계·구현·검증·보안·릴리스·인계 단계를 순서대로 수행합니다. 네트워크 사용은 명시적으로 켜야 합니다. 이 명령은 배포, 패키지 게시, Git push, 유료 자원 생성, 운영 데이터 변경 권한을 부여하지 않습니다.

결과는 `projects/<프로젝트명>/` 아래 생성됩니다. 진행 상태와 검증 증거는 `.appforge/`, 최종 소스 압축본은 `.appforge/reports/`에 저장됩니다.

## 기존 저장소 변경

```bash
cd existing-project
appforge forge "기존 비밀번호 로그인을 유지하면서 패스키 로그인을 추가하라" \
  --target . --driver auto --allow-network
```

버그·오류·수정 표현이 있는 요청은 `bugfix`, 일반 기능 추가는 `feature` 파이프라인으로 이동합니다. 구현 전 기존 동작과 테스트 상태를 기준선으로 기록하고, 무관한 변경을 보존하며, 회귀 증거를 요구합니다.

## 코딩 어시스턴트 안에서 직접 사용

이 저장소를 코딩 어시스턴트로 연 뒤 다음과 같이 명령합니다.

```text
AGENT_GUIDE.md를 실행 규약으로 사용하라. 바코드 검색과 역할별 권한이 있는
재고관리 웹앱을 만들고 테스트, Docker 구성, 릴리스 가능한 소스 압축본까지 완성하라.
계획에서 멈추지 말고 모든 파이프라인 단계를 계속 수행하라.
```

에이전트는 내부적으로 다음 루프를 사용합니다.

```bash
appforge new "<요청>" --pipeline auto --mode autonomous
appforge prompt <project> --output <project>/.appforge/current-stage.md
appforge complete <project> --auto-approve
appforge status <project>
```

`AGENTS.md`, `CLAUDE.md`, `CODEX.md`, Gemini, GitHub Copilot, Cursor, Windsurf용 어댑터가 포함되어 있습니다.

## 제작 구조

```text
자연어 앱 명령
      │
      ▼
자동 파이프라인 라우터
      │
      ▼
YAML 단계 정의 ──► 단계 스킬 + 이전 산출물 + 저장소 문맥
      │                                  │
      │                                  ▼
      │                           코딩 에이전트 드라이버
      │                                  │
      ▼                                  ▼
JSON 산출물 계약 ◄────────────── 소스 수정 + stage-result.json
      │
      ▼
결정론적 게이트: 테스트 · 린트 · 타입 · 빌드 · 비밀정보 · 릴리스 준비
      │
      ▼
리뷰 + 체크포인트 + 실패 재시도/재개
      │
      ▼
릴리스 준비 소스 압축본 + 인계 보고서
```

포함 구성은 다음과 같습니다.

- **12개 파이프라인:** 웹앱, 풀스택 SaaS, API, CLI, 데스크톱, 모바일, 데이터 앱, 자동화, 라이브러리/SDK, 프로토타입, 기존 기능 추가, 버그 수정
- **55개 이상 스킬:** 단계별 플레이북, 기술 스택, 도메인, 실패 복구, 리뷰, 보안 경계, 완료 정의
- **20개 이상 도구:** 저장소 분석, 제한된 파일 작업, 명령 실행, 스택 감지, 테스트·린트·타입·빌드, 비밀정보 검사, 취약점 감사, SBOM, 릴리스 준비, 안전한 압축
- **22개 JSON 산출물 스키마:** 제품 정의, 요구사항, 아키텍처, UX, API/데이터 계약, 구현, 검증, 보안, 운영, 릴리스, 인계, 원인 진단, 회귀 보고서 등

## 주요 명령

```bash
appforge pipelines                         # 파이프라인 목록
appforge route "Flutter 앱을 만들어라"       # 자동 분류 점수 확인
appforge new "CLI 도구를 만들어라"           # 실행 없이 프로젝트 초기화
appforge run projects/cli-도구를-만들어라     # 설치된 에이전트로 재개
appforge status <project>                  # 체크포인트 상태
appforge prompt <project>                  # 다음 단계 작업 패킷
appforge complete <project>                # 수동 완료 작업 검증
appforge preflight <project>               # 스택과 품질 명령 감지
appforge doctor                            # 에이전트/도구 환경 검사
appforge tool list                         # 도구 계약 목록
```

사용자 정의 에이전트 프로세스도 연결할 수 있습니다.

```bash
appforge run <project> \
  --driver generic \
  --agent-cmd 'my-agent --workspace {workspace} --prompt {prompt_file}'
```

사용 가능한 치환자는 `{workspace}`, `{prompt_file}`, `{result_file}`, `{stage}`, `{attempt}`입니다. `{prompt_file}`을 쓰지 않으면 작업 패킷이 표준입력으로 전달됩니다.

## 중단 후 재개 가능한 상태

```text
.appforge/
├── project.json          # 원 요청, 파이프라인, 모드, 안전 정책
├── state.json            # 현재/완료 단계
├── stage-result.json     # 각 시도마다 새로 작성해야 하는 완료 기록
├── artifacts/            # 스키마 검증된 제품·엔지니어링 증거
├── checkpoints/          # 단계별 원자적 체크포인트
├── prompts/              # 실제 사용된 단계 작업 패킷
├── logs/                 # 드라이버 출력과 시도 기록
└── reports/              # SBOM, 인벤토리, 증거, 소스 압축본
```

중단된 실행은 `appforge run <project>`로 재개합니다. 완료 단계는 반복하지 않으며, 실패한 단계에는 직전 리뷰 결과가 다음 작업 패킷으로 전달됩니다. 매 시도마다 새로운 `stage-result.json`을 요구하므로 과거 성공 기록이 새 실행을 허위 통과시키지 못합니다.

## 안전 경계와 완료 정의

기본값에서 OpenAppForge는 작업공간 밖 쓰기, 네트워크 의존 도구, 파괴적 도구, 에이전트 권한 우회를 허용하지 않습니다. 명령 출력의 비밀정보를 가리고, 릴리스 전 소스의 자격증명을 검사하며, `.env`·개인키·자격증명·Git 데이터·의존성·캐시·`.appforge/` 내부 파일을 최종 소스 압축에서 제외합니다.

`--allow-network`는 OpenAppForge 도구 정책이며 운영체제 방화벽은 아닙니다. 테스트와 빌드 스크립트는 저장소 코드를 실행하므로 신뢰하지 않는 요청·저장소는 컨테이너나 가상머신에서 실행해야 합니다.

“완료”는 실제 구현, 실행된 검증 증거, 필수 게이트 통과, 보안 검토, 재현 가능한 시작/빌드 절차, 소스 인계본 생성을 뜻합니다. 외부 배포가 수행되었다는 뜻은 아닙니다.

상세 내용은 [docs/SAFETY.md](docs/SAFETY.md), 확장 방법은 [docs/EXTENDING.md](docs/EXTENDING.md)를 참고하십시오.

## 개발 및 테스트

```bash
python -m pip install -e '.[dev]'
pytest
python -m build
```

## 라이선스와 영감

OpenAppForge는 Apache License 2.0으로 제공되는 독립적인 원본 구현입니다. 선언형 파이프라인, 조합 가능한 스킬, 자동 발견 도구, 체크포인트, 리뷰 게이트를 저장소 중심으로 결합하는 접근은 OpenMontage에서 영감을 받았습니다. OpenMontage 소스 코드는 포함하지 않았습니다. 자세한 내용은 [ATTRIBUTION.md](ATTRIBUTION.md)에 기록했습니다.
