코드를 전부 확인했습니다. FastAPI + Bun LLM 브리지 + Vue UI, 12개 파이프라인 정의(YAML), 스테이지별 아티팩트 스키마 검증, 게이트/체크포인트/메모리(JSONL) 구조로 상당히 잘 설계된 프로젝트입니다. 개선 제안을 세 축으로 정리합니다.

**1. 파이프라인**

- **고정 폭포수 구조가 과도합니다.** web-app 기준 intake→spec→workflow→memory→loop→architecture→experience→구현까지 7단계의 문서 생성 후에야 코드가 나옵니다. "투두앱 만들어줘" 같은 요청도 동일 비용을 치릅니다. 프롬프트 복잡도를 LLM으로 분류해 spec/workflow/memory/loop를 하나의 "engineering_spec" 스테이지로 접는 경량 트랙을 두세요(prototype.yaml 라우팅 강화 포함).
- **라우팅이 키워드 매칭이라 취약합니다**(match.keywords). "장부 정리 프로그램" 같은 표현은 오분류됩니다. 저비용 LLM 1콜로 분류하고, 라우팅 근거를 UI에 노출해 사용자가 정정할 수 있게 하세요.
- **검증 실패 시 재시도가 스테이지 전체 재생성입니다.** run_tests 실패 → implementation 프롬프트 재실행인데, 실패한 테스트 로그를 targeted-fix 프롬프트(해당 파일 내용 + 에러만)로 좁히는 "수리 루프"를 별도로 두면 성공률과 비용이 크게 개선됩니다. REPEATED_FAILURE_LOOP 가드는 좋으나, 감지 후 "전략 변경"이 실제로는 동일 프롬프트 재전송이라 형식적입니다.
- **review_stage가 기계적 게이트 집계일 뿐** LLM 리뷰가 아닙니다(reviewer.md 스킬이 있는데 미사용). 구현/검증 스테이지만이라도 별도 LLM 리뷰 패스를 붙이는 것을 권합니다.

**2. 하네스 (드라이버/브리지)**

- **가장 큰 구조적 한계: 스테이지당 단발(single-shot) JSON 봉투 방식입니다.** LLM이 도구를 못 쓰고, 전체 소스 파일을 한 번의 completion에 JSON 문자열로 담아야 합니다. → 출력 토큰 한도에서 잘리면 JSON 파싱 실패, 중간 규모 앱 이상은 물리적으로 불가능, 기존 파일을 읽고 diff 편집하는 것도 불가. 브리지에 최소한의 tool-use 루프(read_file/write_file/run_command 프록시)를 추가하는 것이 v5의 핵심 과제여야 합니다. 이미 runner에 ToolRegistry가 있으니 이를 브리지 프로토콜로 노출하면 됩니다.
- **자기보고 신뢰 문제.** `_default_stage_result`가 JSON 파싱만 성공하면 `checks: passed=true`를 자동 생성합니다. "no fake success" 원칙과 모순되며, 검증 게이트 없는 초기 스테이지들은 사실상 무검증 통과입니다. 자동 생성 체크는 `passed: null/unverified`로 표기하세요.
- **구현 스테이지에 파일 "내용"이 안 들어갑니다.** workspace_tree(경로만)와 prior artifacts는 주입되지만 기존 코드 본문이 없어, 재시도 시 LLM이 자기 코드를 못 보고 통째로 다시 씁니다. 변경 대상 파일 내용을 컨텍스트에 포함하세요.
- 그 외: 스트리밍 미사용(긴 생성 중 타임아웃/무소식), 단일 활성 잡 제한, `_extract_json_object`의 문자열 스캔 대신 provider의 structured output/JSON mode 활용 권장.

**3. UI/UX**

- **결과 미리보기가 없습니다.** 최종 산출이 ZIP 다운로드뿐이라, 사용자가 앱을 확인하려면 직접 압축 풀고 빌드해야 합니다. 정적/웹앱은 샌드박스 iframe 프리뷰, 최소한 파일 트리 + 코드 뷰어를 잡 패널에 넣으세요. "결과를 보고 → 수정 요청" 반복 루프(대화형 피드백)가 없는 것이 자율 빌더로서 가장 큰 UX 공백입니다.
- **중간 아티팩트가 안 보입니다.** requirements_spec 등 스테이지 산출물을 타임라인에서 펼쳐볼 수 있어야 신뢰가 생기고, architecture/experience의 approval:true 흐름(승인 대기)도 UI에서 검토→승인 UX로 살려야 합니다(현재 web은 auto_approve로 우회).
- **폴링 기반 갱신**(setTimeout) 대신 SSE/WebSocket으로 이벤트를 푸시하면 지연·부하가 줄고, 구현 스테이지 중 토큰 스트림 일부라도 보여주면 수 분간의 "침묵" 체감이 사라집니다.
- **ProviderSettings(774줄)가 첫 진입 장벽입니다.** 키 미설정 시 온보딩 마법사로 안내하고, HealthBanner에 원인별 해결 버튼(브리지 시작/키 입력)을 직접 연결하세요.
- 실패 시 ErrorPanel에 "어느 스테이지, 어떤 게이트, 어떤 로그" 드릴다운과 "이 스테이지부터 재시도" 버튼(runner는 only_stage를 이미 지원)을 노출하면 복구 UX가 완성됩니다.

**우선순위 요약:** ① 브리지 tool-use 루프 도입(단발 JSON 한계 해소) ② 실패-수리 targeted 루프 ③ 프리뷰 + 대화형 수정 UX ④ 경량 파이프라인 트랙 순을 권합니다.