from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .constants import PIPELINE_DIR, SCHEMAS_DIR
from .models import PipelineSpec


class PipelineValidationError(ValueError):
    pass


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    import json

    with (SCHEMAS_DIR / "pipeline.schema.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def list_pipeline_names(directory: Path | None = None) -> list[str]:
    base = directory or PIPELINE_DIR
    return sorted(path.stem for path in base.glob("*.yaml"))


def load_pipeline(name: str, directory: Path | None = None) -> PipelineSpec:
    base = directory or PIPELINE_DIR
    path = base / f"{name}.yaml"
    if not path.exists():
        available = ", ".join(list_pipeline_names(base))
        raise FileNotFoundError(f"Pipeline {name!r} not found. Available: {available}")
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    try:
        jsonschema.validate(raw, _schema())
    except jsonschema.ValidationError as exc:
        raise PipelineValidationError(f"Invalid pipeline {name!r}: {exc.message}") from exc
    spec = PipelineSpec.from_dict(raw)
    stage_names = [stage.name for stage in spec.stages]
    if len(stage_names) != len(set(stage_names)):
        raise PipelineValidationError(f"Pipeline {name!r} contains duplicate stage names")
    return spec


def all_pipelines(directory: Path | None = None) -> list[PipelineSpec]:
    return [load_pipeline(name, directory) for name in list_pipeline_names(directory)]


def auto_select_pipeline(prompt: str, *, existing_repo: bool = False) -> tuple[str, dict[str, int]]:
    text = prompt.casefold()
    scores: dict[str, int] = {}
    for pipeline in all_pipelines():
        score = sum(2 if " " in keyword else 1 for keyword in pipeline.keywords if keyword.casefold() in text)
        scores[pipeline.name] = score

    hard_rules = [
        ("bugfix", ("bug", "버그", "오류", "crash", "fix", "고쳐", "회귀")),
        ("feature", ("existing repo", "기존 저장소", "기존 프로젝트", "add feature", "기능 추가")),
        ("fullstack-saas", ("saas", "multi-tenant", "멀티테넌트", "subscription", "구독")),
        ("mobile-app", ("mobile", "모바일", "android", "ios", "flutter", "react native")),
        ("desktop-app", ("desktop", "데스크톱", "electron", "tauri")),
        ("cli-tool", ("cli", "command line", "커맨드라인", "터미널 도구")),
        ("api-service", ("api", "backend", "백엔드", "microservice", "마이크로서비스")),
        ("automation", ("automation", "자동화", "workflow", "워크플로", "bot", "봇")),
        ("library-sdk", ("sdk", "library", "라이브러리", "package", "패키지")),
        ("data-app", ("dashboard", "대시보드", "etl", "analytics", "분석", "data app")),
        ("prototype", ("prototype", "프로토타입", "poc", "proof of concept", "빠른 mvp")),
    ]
    for pipeline, needles in hard_rules:
        if any(needle in text for needle in needles):
            scores[pipeline] = scores.get(pipeline, 0) + 8

    if existing_repo and scores.get("feature", 0) == 0 and scores.get("bugfix", 0) == 0:
        scores["feature"] = 4

    winner = max(scores, key=lambda key: (scores[key], key == "web-app"))
    if scores[winner] <= 0:
        winner = "feature" if existing_repo else "web-app"
    return winner, scores
