from __future__ import annotations

from appforge.artifacts import load_artifact_schema
from appforge.constants import SKILLS_DIR
from appforge.pipelines import all_pipelines, auto_select_pipeline, list_pipeline_names
from appforge.tooling.registry import ToolRegistry


def test_all_builtin_pipelines_are_valid_and_resolvable() -> None:
    expected = {
        "api-service",
        "automation",
        "bugfix",
        "cli-tool",
        "data-app",
        "desktop-app",
        "feature",
        "fullstack-saas",
        "library-sdk",
        "mobile-app",
        "prototype",
        "web-app",
    }
    assert set(list_pipeline_names()) == expected
    tool_names = set(ToolRegistry().names())
    for pipeline in all_pipelines():
        assert pipeline.stages
        assert len({stage.name for stage in pipeline.stages}) == len(pipeline.stages)
        for stage in pipeline.stages:
            assert (SKILLS_DIR / stage.skill).is_file()
            for artifact in stage.produces:
                assert load_artifact_schema(artifact)["type"] == "object"
            assert set(stage.tools).issubset(tool_names)
            assert {gate.tool for gate in stage.gates}.issubset(tool_names)


def test_router_handles_english_and_korean_requests() -> None:
    cases = [
        ("Build a Flutter mobile app for field inspections", False, "mobile-app"),
        ("멀티테넌트 구독형 SaaS를 만들어라", False, "fullstack-saas"),
        ("간헐적으로 결제가 두 번 생성되는 버그를 고쳐라", True, "bugfix"),
        ("Create a command line log analyzer", False, "cli-tool"),
        ("기존 프로젝트에 관리자 감사 로그 기능 추가", True, "feature"),
        ("Build an idempotent webhook API backend", False, "api-service"),
        ("빠른 MVP 프로토타입을 만들어라", False, "prototype"),
    ]
    for prompt, existing, expected in cases:
        selected, scores = auto_select_pipeline(prompt, existing_repo=existing)
        assert selected == expected, scores
