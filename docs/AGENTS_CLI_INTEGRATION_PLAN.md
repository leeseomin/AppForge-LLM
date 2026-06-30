# google/agents-cli 로직 내장 계획

작성일: 2026-06-30

이 문서는 `google/agents-cli`의 핵심 로직을 `OpenAppForge`(`AppForge-LLM`) 안에 내장하거나 어댑터로 연결하기 위해 수정해야 할 영역을 정리한다. 핵심 방향은 **agents-cli를 그대로 실행하는 별도 도구**로만 두지 않고, AppForge의 기존 원칙인 **파이프라인, 스킬, JSON 산출물, 결정적 게이트, 체크포인트, 안전 정책** 안으로 흡수하는 것이다.

## 1. 통합 목표

`agents-cli`는 Gemini Enterprise Agent Platform / Google Cloud 기반 에이전트를 스캐폴드, 평가, 배포, 게시하기 위한 CLI와 스킬 묶음이다. AppForge에 내장할 때의 목표는 다음과 같다.

- 자연어 요청이 Google ADK / Gemini / Vertex AI / Agent Runtime / Cloud Run 계열이면 전용 파이프라인으로 라우팅한다.
- AppForge가 Google agent 프로젝트의 요구사항, 스캐폴드, 구현, 평가, 배포 계획, 보안 검토, 릴리즈 핸드오프를 단계별로 통제한다.
- `agents-cli`의 템플릿 처리, 원격 템플릿 해석, 프로젝트 매니페스트, 평가, 배포 로직은 AppForge의 Tool/Gate/Artifact 경계로 감싼다.
- 실제 Google Cloud 리소스 생성, 배포, 게시, 과금 가능 작업은 기본값으로 실행하지 않고 명시적 opt-in이 있을 때만 허용한다.

## 2. 권장 통합 방식

### 2.1 단기: 래퍼/어댑터 방식

초기 MVP는 설치된 `agents-cli`를 subprocess로 호출하는 AppForge Tool을 추가한다. 빠르게 검증할 수 있고, AppForge의 안전 정책과 게이트를 먼저 적용할 수 있다.

예시:

- `agents-cli` 설치 여부 확인
- `agents-cli scaffold ... --dry-run` 또는 로컬 템플릿 기반 생성
- `agents-cli eval generate`, `agents-cli eval grade` 실행 결과를 AppForge 산출물로 변환
- `agents-cli deploy --dry-run` 결과를 배포 계획 산출물로 저장

### 2.2 중기: 내부 모듈화 방식

검증 후에는 `agents-cli` 핵심 로직을 AppForge 내부 네임스페이스에 옮긴다.

권장 위치:

```text
appforge/integrations/google_agents_cli/
├── __init__.py
├── project_config.py        # agents-cli-manifest.yaml 읽기/쓰기/마이그레이션
├── tool_resolution.py       # uv, npx, git, gcloud, terraform, gh 확인
├── subprocesses.py          # AppForge식 redaction/truncation이 적용된 실행 유틸
├── remote_template.py       # adk@, GitHub tree URL, owner/repo/path@ref 해석
├── templates.py             # cookiecutter/조건부 파일/템플릿 병합
├── scaffold.py              # create/enhance/upgrade 로직
├── eval.py                  # generate/grade/compare/analyze 래퍼 또는 내장
├── deploy.py                # dry-run 우선 배포 계획/실행 로직
└── publish.py               # Gemini Enterprise 등록 계획/실행 로직
```

`google.agents.cli.*` 네임스페이스를 그대로 복사하면 Google namespace package 충돌 가능성이 있으므로, AppForge 내부 네임스페이스로 옮기는 편이 안전하다.

## 3. AppForge 수정 대상 요약

| 영역 | 수정 파일/디렉터리 | 해야 할 일 |
|---|---|---|
| 의존성 | `pyproject.toml` | Google agent 기능을 core dependency가 아니라 optional extra로 추가한다. 예: `google-agents = ["cookiecutter", "click", "google-auth", ...]`. Node.js, `uv`, `git`, `gcloud`, `terraform`, `npx`는 Python dependency가 아니라 doctor/tool 검사 대상으로 둔다. |
| CLI | `appforge/cli.py` | `appforge google-agent ...` 또는 `appforge agent ...` Typer sub-app 추가. `setup`, `create`, `enhance`, `eval`, `deploy plan`, `deploy`, `publish plan`, `doctor` 명령을 단계적으로 추가한다. |
| 드라이버 | `appforge/drivers.py` | `agents-cli`는 코딩 에이전트가 아니라 에이전트 개발 툴체인이므로 기본 `AgentDriver`로 넣지 않는다. 필요할 때만 `AgentsCliCommandDriver`를 실험적으로 추가하고, 기본은 Tool/Gate로 감싼다. |
| 실행/안전 | `appforge/runner.py` | 현재 `allow_deploy`는 항상 `False`로 기록된다. Google Cloud 배포 단계를 실제 실행하려면 `allow_deploy` 파라미터와 CLI 플래그를 추가한다. 기본은 `False` 유지. |
| 프로젝트 메타 | `appforge/projects.py` | Google agent 프로젝트에서는 루트의 `agents-cli-manifest.yaml`을 생성/읽고, `.appforge/project.json`에는 manifest snapshot과 Google agent profile 정보를 저장한다. |
| 모델 | `appforge/models.py` | MVP에서는 기존 `StageSpec`/`GateSpec`를 유지한다. 이후 cloud side-effect를 명시하려면 `Tool` 또는 gate metadata에 `external_side_effect`, `requires_deploy_permission` 같은 속성을 추가한다. |
| 라우터 | `appforge/pipelines.py` | `agent`, `adk`, `gemini`, `vertex`, `agent runtime`, `cloud run`, `google cloud`, `rag agent`, `평가`, `배포` 키워드를 `google-agent` 파이프라인으로 라우팅한다. |
| 프롬프트 컴파일 | `appforge/prompting.py` | `agents-cli-manifest.yaml`, Google agent config, ADK/Google Cloud 관련 스킬을 stage packet에 포함한다. `_relevant_knowledge()`에 ADK/GCP/Agent Runtime/Cloud Run 매핑을 추가한다. |
| 파이프라인 | `appforge/resources/pipeline_defs/google-agent.yaml` | Google agent 전용 파이프라인 추가. 요구사항 → 스캐폴드 → ADK 구현 → 평가 데이터셋 → 평가 실행 → 배포 계획 → 보안 → 릴리즈/핸드오프 순서를 권장한다. |
| 스킬 | `appforge/resources/skills/stages/*.md` | `google-agent-intake.md`, `google-agent-scaffold.md`, `google-agent-adk-implementation.md`, `google-agent-eval.md`, `google-agent-deployment-plan.md`, `google-agent-release.md` 추가. |
| 지식 스킬 | `appforge/resources/skills/stacks/*.md`, `appforge/resources/skills/domains/*.md` | `google-adk.md`, `google-cloud-agent-platform.md`, `agent-runtime.md`, `cloud-run.md`, `agent-evaluation.md`, `observability.md` 추가. |
| 산출물 스키마 | `appforge/resources/schemas/artifacts/*.json` | `google_agent_brief`, `google_agent_config`, `agent_scaffold_report`, `agent_eval_dataset`, `agent_eval_report`, `google_deployment_plan`, `google_publish_plan`, `agent_observability_plan` 스키마 추가. |
| 툴 | `appforge/tooling/tools/` | `agents_cli_doctor`, `google_agent_scaffold`, `google_agent_manifest`, `agent_eval_generate`, `agent_eval_grade`, `google_deploy_plan`, `google_deploy`, `google_publish_plan`, `remote_agent_template_fetch` 추가. |
| 템플릿 | `appforge/resources/templates/google_agents/**` | agents-cli의 base templates, deployment targets, frontends, locks를 AppForge 패키지 데이터로 포함하거나, 초기에는 외부 `agents-cli` 설치본을 참조한다. |
| 패키지 데이터 | `pyproject.toml` | `appforge/resources/templates/google_agents/**/*`와 필요한 lock/config 파일이 wheel에 포함되도록 package-data 갱신. |
| 문서 | `README.md`, `README.ko.md`, `docs/ARCHITECTURE.md`, `docs/EXTENDING.md`, `docs/SAFETY.md` | Google agent profile 사용법, 배포 안전 정책, optional dependency 설치법, 예제 명령 추가. |
| 테스트 | `tests/test_pipelines.py`, `tests/test_runner.py`, 신규 테스트 | 새 파이프라인 expected set 반영, remote spec parser, template config merge, deploy guard, eval artifact 검증 테스트 추가. |

## 4. agents-cli 로직별 AppForge 매핑

| agents-cli 원본 영역 | 내장 위치 | 통합 방식 |
|---|---|---|
| `src/google/agents/cli/main.py` | `appforge/cli.py` | Click root group을 그대로 옮기기보다 Typer sub-app으로 재구성한다. import 비용이 커지면 command 함수 내부 lazy import로 대응한다. |
| `src/google/agents/cli/_click.py` | 선택 사항 | AppForge가 Typer를 쓰므로 `LazyGroup`은 필수 아님. 단, Google agent 하위 명령이 커지면 Typer callback 내부 local import로 동일 효과를 낸다. |
| `src/google/agents/cli/_project.py` | `appforge/integrations/google_agents_cli/project_config.py` | `agents-cli-manifest.yaml` 읽기/쓰기, legacy pyproject fallback, GCP project/region resolution을 분리 이식한다. |
| `src/google/agents/cli/_runner.py` | `appforge/integrations/google_agents_cli/subprocesses.py` 또는 `appforge/util.py` | list argv, no shell, cwd 제한, timeout, AppForge redaction/truncation을 결합한다. |
| `src/google/agents/cli/_tools.py` | `appforge/integrations/google_agents_cli/tool_resolution.py` | `uv`, `npx`, `git`, `gcloud`, `terraform`, `gh` 탐지와 설치 힌트를 AppForge doctor/tool contract로 노출한다. |
| `setup/cmd_setup.py` | `appforge/tooling/tools/agents_cli_setup.py`, `appforge/cli.py` | `npx skills add`와 `uv tool install`은 opt-in 명령으로만 제공한다. `forge` 실행 중 자동 글로벌 설치는 하지 않는다. |
| `scaffold/commands/create.py` | `appforge/integrations/google_agents_cli/scaffold.py` | 옵션 해석, non-interactive 기본값, `--adk`, `--prototype`, datastore/session/deployment/cicd 선택 로직을 AppForge stage/tool 입력으로 변환한다. |
| `scaffold/utils/remote_template.py` | `appforge/integrations/google_agents_cli/remote_template.py` | `adk@sample`, GitHub URL, owner/repo/path@ref, flat structure inference를 내장한다. 네트워크 필요 Tool로 표시한다. |
| `scaffold/utils/template.py` | `appforge/integrations/google_agents_cli/templates.py` | cookiecutter 처리, 조건부 파일 제거, base/deployment/frontend 병합, remote overlay, `.env` 생성 정책을 AppForge 안전 정책으로 감싼다. |
| `eval/*` | `appforge/tooling/tools/agent_eval_*.py` | generate/grade/run을 Tool로 제공하고 결과 JSON을 `agent_eval_report` 산출물로 변환한다. |
| `deploy/*`, `infra/*` | `appforge/tooling/tools/google_deploy*.py` | 기본은 `deploy plan`/`dry-run`. 실제 배포는 `--allow-deploy`, `--allow-network`, 명시적 project/region이 모두 있을 때만 실행한다. |
| `publish/*` | `appforge/tooling/tools/google_publish*.py` | Gemini Enterprise 등록은 기본적으로 계획 산출물만 생성한다. 실제 publish는 별도 opt-in. |
| `skills/google-agents-cli-*` | `appforge/resources/skills/...` | 그대로 복사하지 말고 AppForge stage skill 형식으로 요약/재구성한다. 필요한 reference는 attribution과 함께 포함한다. |
| `scaffold/agents`, `base_templates`, `deployment_targets`, `frontends`, `resources/locks` | `appforge/resources/templates/google_agents/**` | 중기부터 패키징한다. 초기 MVP에서는 설치된 `agents-cli`를 호출하거나 local template path를 받는다. |

## 5. 새 파이프라인 초안

파일: `appforge/resources/pipeline_defs/google-agent.yaml`

권장 stage 구성:

```yaml
name: google-agent
version: "1.0"
category: agent-platform
description: Build, evaluate, and prepare Google ADK / Gemini Enterprise agent projects.
match:
  keywords:
    - agent
    - adk
    - gemini
    - vertex
    - agent runtime
    - google cloud
    - cloud run
    - rag agent
    - agent evaluation
    - 에이전트
    - 제미나이
    - 구글 클라우드
orchestration:
  default_mode: autonomous
  max_stage_attempts: 3
stages:
  - name: intake
    skill: stages/google-agent-intake.md
    produces: [google_agent_brief]
  - name: scaffold
    skill: stages/google-agent-scaffold.md
    produces: [google_agent_config, agent_scaffold_report]
  - name: implementation
    skill: stages/google-agent-adk-implementation.md
    produces: [implementation_report]
  - name: eval_design
    skill: stages/google-agent-eval-design.md
    produces: [agent_eval_dataset]
  - name: eval_run
    skill: stages/google-agent-eval-run.md
    produces: [agent_eval_report]
  - name: deployment_plan
    skill: stages/google-agent-deployment-plan.md
    produces: [google_deployment_plan]
  - name: security
    skill: stages/security-review.md
    produces: [security_review]
  - name: release
    skill: stages/google-agent-release.md
    produces: [release_manifest, handoff_report]
```

실제 YAML은 기존 pipeline schema의 `review_focus`, `success_criteria`, `tools`, `gates` 필드를 모두 채워야 한다.

## 6. 새 산출물 스키마 제안

| 스키마 | 목적 | 필수 필드 예시 |
|---|---|---|
| `google_agent_brief.schema.json` | 사용 사례, 채널, 모델, 도구, 데이터, 배포 목표 정리 | `schema_version`, `agent_goal`, `target_users`, `capabilities`, `constraints`, `deployment_intent` |
| `google_agent_config.schema.json` | `agents-cli-manifest.yaml`의 AppForge snapshot | `schema_version`, `project_name`, `agent_directory`, `base_template`, `language`, `deployment_target`, `region`, `datastore`, `session_type` |
| `agent_scaffold_report.schema.json` | 생성/수정된 파일과 템플릿 선택 근거 | `schema_version`, `template_source`, `files_created`, `files_modified`, `commands_run`, `warnings` |
| `agent_eval_dataset.schema.json` | 평가 케이스 설계 | `schema_version`, `cases`, `metrics`, `rubric`, `coverage_notes` |
| `agent_eval_report.schema.json` | eval generate/grade 결과 | `schema_version`, `dataset`, `traces_path`, `grade_results`, `metrics`, `failures`, `recommendations` |
| `google_deployment_plan.schema.json` | 배포 전 계획/검증 결과 | `schema_version`, `target`, `project`, `region`, `service_name`, `required_apis`, `secrets`, `cost_risk`, `dry_run_commands`, `rollback` |
| `google_publish_plan.schema.json` | Gemini Enterprise 등록 계획 | `schema_version`, `target`, `display_name`, `identity`, `access`, `approval_required`, `publish_commands` |
| `agent_observability_plan.schema.json` | 로그/트레이스/BigQuery analytics 계획 | `schema_version`, `signals`, `dashboards`, `alerts`, `privacy_notes` |

## 7. Tool 구현 우선순위

### 7.1 공통 유틸

먼저 다음 유틸을 추가한다.

- `require_external_tool(name, install_hint)`
- `run_external_command(argv, cwd, timeout, env=None, capture=True)`
- `sanitize_cloud_output(text)`
- `resolve_google_project(explicit=None, required=False)`
- `resolve_google_region(config=None, explicit=None)`

AppForge의 기존 원칙에 맞게 `shell=False`, bounded capture, secret redaction, workspace cwd 제한을 유지한다.

### 7.2 Tool 목록

| Tool 이름 | network | destructive/cloud side-effect | 역할 |
|---|---:|---:|---|
| `agents_cli_doctor` | false | false | `uv`, `node`, `npx`, `git`, `gcloud`, `terraform`, `gh`, `agents-cli` 가용성 점검 |
| `agents_skills_install` | true | user/global write 가능 | `npx skills add` 실행. 기본은 dry-run 또는 workspace scope 권장 |
| `google_agent_manifest` | false | false | `agents-cli-manifest.yaml` 읽기/쓰기/검증 |
| `remote_agent_template_fetch` | true | false | `adk@`, GitHub URL 템플릿 fetch. `GIT_TERMINAL_PROMPT=0` 적용 |
| `google_agent_scaffold` | 선택적 true | workspace write | 템플릿 처리 또는 외부 `agents-cli scaffold/create` 호출 |
| `agent_eval_generate` | true 가능 | false | eval traces 생성 |
| `agent_eval_grade` | true 가능 | false | metrics 기반 grading |
| `google_deploy_plan` | false/true | false | `agents-cli deploy --dry-run` 또는 자체 dry-run 명령 생성 |
| `google_deploy` | true | true | 실제 Cloud 배포. `allow_deploy` 없으면 실패 |
| `google_publish_plan` | false/true | false | Gemini Enterprise 등록 계획 산출 |
| `google_publish` | true | true | 실제 등록. `allow_deploy` 또는 별도 `allow_publish` 없으면 실패 |

## 8. CLI 변경안

권장 명령 형태:

```bash
appforge google-agent doctor
appforge google-agent setup --workspace --dry-run
appforge google-agent create my-agent --agent adk --prototype --skip-checks
appforge google-agent eval run --dataset eval_cases.json --metrics final_response_quality
appforge google-agent deploy plan --target agent_runtime --project <PROJECT_ID> --region us-east1
appforge google-agent deploy --allow-deploy --allow-network --project <PROJECT_ID> --region us-east1
```

`forge`에 바로 연결할 경우:

```bash
appforge forge \
  "Build a Gemini ADK support agent with evals and Cloud Run deployment plan" \
  --pipeline google-agent \
  --allow-network
```

이 명령은 기본적으로 **배포 계획까지만** 만든다. 실제 배포는 다음처럼 별도 플래그가 있어야 한다.

```bash
appforge run . --stage deployment --allow-network --allow-deploy --project <PROJECT_ID>
```

## 9. 안전 정책 변경

현재 AppForge는 배포, 게시, 과금 리소스 생성, production data 변경을 기본 허용하지 않는다. Google agent 통합에서도 이 원칙을 유지한다.

필수 정책:

1. `--allow-network` 없이는 `git clone`, `uv`, `npx`, `gcloud`, remote eval, dependency install을 실행하지 않는다.
2. `--allow-deploy` 없이는 Cloud Run, Agent Runtime, GKE, Terraform apply, Gemini Enterprise publish를 실행하지 않는다.
3. `--project` 또는 명시적 project config 없이는 실제 배포를 막는다.
4. 비대화형 실행에서 project가 gcloud config로만 추론되면 실패시킨다. 사용자가 project를 명시하거나 `--no-confirm-project`에 준하는 옵션을 명시해야 한다.
5. `.env`, service account JSON, API key, secret 이름/값은 `.appforge/logs`, checkpoint, archive에 남기지 않는다.
6. `.env`, `*.json` credential, `uv.lock` 내 민감 정보 여부, Terraform state, `.terraform/`은 archive 제외 목록에 추가한다.
7. 실제 deploy/publish Tool은 `destructive=True` 또는 새 risk flag(`external_side_effect=True`)로 표시한다.
8. `agents-cli` 명령의 성공 메시지는 stage 완료 증거가 아니다. AppForge의 `stage-result.json`, artifact schema, gate, review가 모두 통과해야 완료로 인정한다.

## 10. 테스트 계획

| 테스트 파일 | 추가/수정 내용 |
|---|---|
| `tests/test_pipelines.py` | expected set에 `google-agent` 추가. 새 pipeline의 skills, artifacts, tools, gates 존재 검증. |
| `tests/test_router.py` 또는 기존 router 테스트 | `ADK agent`, `Gemini Enterprise agent`, `Agent Runtime`, `구글 클라우드 에이전트` 요청이 `google-agent`로 라우팅되는지 확인. |
| `tests/test_google_agents_remote_template.py` | `adk@sample`, GitHub tree URL, `owner/repo/path@ref`, `local@` 해석 테스트. |
| `tests/test_google_agents_manifest.py` | `agents-cli-manifest.yaml` 읽기/쓰기, legacy pyproject fallback, AppForge project snapshot 테스트. |
| `tests/test_google_agent_scaffold.py` | 네트워크 없이 fixture template로 scaffold 실행. `.env`/secret 미포함 검증. |
| `tests/test_google_agent_tools.py` | `allow_network=False`에서 remote fetch 실패, `allow_deploy=False`에서 deploy 실패, dry-run은 허용. |
| `tests/test_runner.py` | `google-agent` stage도 stale completion을 통과하지 못하는지 확인. |
| `tests/test_cli_google_agent.py` | `appforge google-agent doctor`, `deploy plan --dry-run` smoke test. |

## 11. 단계별 구현 순서

### Phase 0 — 정책/라이선스/범위 확정

- `google/agents-cli`에서 직접 복사할 파일과 재작성할 파일을 구분한다.
- Apache-2.0 헤더, NOTICE, attribution 반영 여부를 확정한다.
- 실제 Cloud deploy/publish를 MVP에 포함할지, 배포 계획까지만 할지 결정한다.

### Phase 1 — 외부 agents-cli 래퍼 Tool

- `agents_cli_doctor`
- `google_agent_manifest`
- `google_deploy_plan`
- `agent_eval_generate`, `agent_eval_grade`
- CLI sub-app skeleton

이 단계에서는 내부 템플릿 엔진 없이 설치된 `agents-cli`를 호출해도 된다.

### Phase 2 — `google-agent` 파이프라인/스킬/스키마

- `google-agent.yaml` 추가
- stage skills 추가
- artifact schemas 추가
- router keyword 추가
- tests expected set 갱신

### Phase 3 — 스캐폴드 엔진 내장

- `remote_template.py` 이식/재작성
- `templates.py` 이식/재작성
- base templates 패키징 또는 외부 template path 지원
- cookiecutter optional dependency 추가

### Phase 4 — 평가 게이트 강화

- eval dataset schema와 grade report schema를 AppForge gate에 연결한다.
- 평가 실패 cluster/analyze 결과를 다음 stage prompt의 prior failure로 넣는다.

### Phase 5 — 배포/게시 opt-in 실행

- `--allow-deploy` 추가
- `google_deploy` Tool 추가
- deploy stage는 기본 pipeline에 넣지 말고 별도 `deployment` stage 또는 `appforge google-agent deploy` 명령으로 분리한다.
- `--dry-run` 결과를 handoff report에 포함한다.

### Phase 6 — 문서/예제/회귀 테스트

- README/README.ko에 Google agent 예제 추가
- SAFETY에 Google Cloud resource policy 추가
- EXTENDING에 Google agent profile 확장법 추가
- CI에서 네트워크 없는 fixture tests만 기본 실행

## 12. 구현 시 피해야 할 것

- `agents-cli setup`을 `appforge forge` 중 자동 실행하지 않는다. 글로벌 skill 설치는 사용자 opt-in이어야 한다.
- `agents-cli deploy`를 release stage의 기본 gate로 넣지 않는다. 기본은 배포 계획과 dry-run evidence만 생성한다.
- Google Cloud project를 gcloud config에서 몰래 추론해 바로 배포하지 않는다.
- `agents-cli`의 Click command 객체를 AppForge Typer 앱에 억지로 섞지 않는다. 하위 명령은 AppForge CLI 스타일로 재정의한다.
- Google namespace package를 AppForge wheel 안에 그대로 vendoring하지 않는다.
- `.env`, service account key, Terraform state, deployment credentials를 archive에 넣지 않는다.

## 13. 추천 MVP 범위

가장 안전하고 빠른 MVP는 다음이다.

1. `appforge google-agent doctor`
2. `appforge google-agent create --agent adk --prototype --skip-checks`
3. `google-agent` 파이프라인 추가
4. ADK 구현 stage skill 추가
5. eval dataset/report artifact 추가
6. `deploy plan`까지만 지원
7. 실제 deploy/publish는 문서와 별도 opt-in CLI만 제공

이 범위는 AppForge의 기존 production pipeline 모델을 유지하면서, `agents-cli`의 핵심 가치인 **Google agent 스캐폴드, 평가, 배포 준비**를 흡수할 수 있다.

## 14. 최종 변경 체크리스트

- [ ] `pyproject.toml` optional extra와 package-data 추가
- [ ] `appforge/integrations/google_agents_cli/` 신설
- [ ] `appforge/cli.py`에 `google-agent` sub-app 추가
- [ ] `appforge/resources/pipeline_defs/google-agent.yaml` 추가
- [ ] Google agent stage skills 추가
- [ ] Google agent artifact schemas 추가
- [ ] Google agent Tool subclasses 추가
- [ ] `appforge/pipelines.py` router 키워드 추가
- [ ] `appforge/prompting.py` relevant knowledge 매핑 추가
- [ ] `appforge/runner.py`에 `allow_deploy` 정책 추가
- [ ] `.gitignore`/archive 제외 목록에 Google Cloud credential/Terraform state 보강
- [ ] `tests/test_pipelines.py` expected set 수정
- [ ] remote template/config/scaffold/eval/deploy guard 테스트 추가
- [ ] README/README.ko/SAFETY/EXTENDING 문서 갱신
