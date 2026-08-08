from __future__ import annotations

import json
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


_COMPLEXITY_COMPLEX_HINTS = (
    "saas", "multi-tenant", "멀티테넌트", "subscription", "구독", "payment", "결제",
    "role", "permission", "권한", "admin", "관리자", "realtime", "실시간", "websocket",
    "api", "backend", "백엔드", "database", "데이터베이스", "auth", "인증", "login", "로그인",
    "deploy", "배포", "production", "운영", "analytics", "분석",
)

_SMALL_APP_HINTS = (
    "todo", "to-do", "투두", "memo", "메모", "calculator", "계산기", "timer", "타이머",
    "simple", "small", "tiny", "간단", "작은", "가벼운", "landing", "랜딩", "one page", "단일 페이지",
)

_CLASSIFIER_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "appforge_pipeline_route",
        "schema": {
            "type": "object",
            "required": ["pipeline", "confidence", "complexity", "rationale"],
            "properties": {
                "pipeline": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "complexity": {"type": "string", "enum": ["trivial", "small", "standard", "complex"]},
                "rationale": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "strict": True,
    },
}


def classify_prompt_complexity(prompt: str) -> str:
    """Cheap local complexity router used before/after the optional LLM router."""
    text = prompt.casefold()
    if any(hint in text for hint in _COMPLEXITY_COMPLEX_HINTS):
        return "standard"
    if len(prompt) <= 80:
        return "trivial"
    if any(hint in text for hint in _SMALL_APP_HINTS) or len(prompt) <= 220:
        return "small"
    return "standard"


def _keyword_scores(prompt: str, *, existing_repo: bool = False) -> tuple[str, dict[str, int]]:
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
        (
            "api-service",
            (
                "api service", "api server", "api backend", "backend api", "rest api",
                "graphql api", "webhook api", "microservice", "마이크로서비스", "백엔드 서비스",
            ),
        ),
        ("automation", ("automation", "자동화", "workflow", "워크플로", "bot", "봇")),
        ("library-sdk", ("sdk", "library", "라이브러리", "package", "패키지")),
        ("data-app", ("dashboard", "대시보드", "etl", "analytics", "분석", "data app")),
        ("prototype", ("prototype", "프로토타입", "poc", "proof of concept", "빠른 mvp")),
    ]
    for pipeline, needles in hard_rules:
        if any(needle in text for needle in needles):
            scores[pipeline] = scores.get(pipeline, 0) + 8

    complexity = classify_prompt_complexity(prompt)
    scores["_complexity_small"] = 1 if complexity in {"trivial", "small"} else 0

    if existing_repo and scores.get("feature", 0) == 0 and scores.get("bugfix", 0) == 0:
        scores["feature"] = 4

    strong_non_web = max((value for key, value in scores.items() if key not in {"web-app", "web-app-lite", "_complexity_small"}), default=0)
    if not existing_repo and strong_non_web < 8 and complexity in {"trivial", "small"}:
        scores["web-app-lite"] = scores.get("web-app-lite", 0) + 7

    winner = max(scores, key=lambda key: (scores[key], key == "web-app-lite", key == "web-app"))
    if winner.startswith("_") or scores[winner] <= 0:
        winner = "feature" if existing_repo else ("web-app-lite" if complexity in {"trivial", "small"} else "web-app")
    return winner, scores


def _route_prompt_for_llm(prompt: str, *, existing_repo: bool) -> str:
    choices = [
        {
            "name": item.name,
            "category": item.category,
            "description": item.description,
            "keywords": list(item.keywords[:8]),
        }
        for item in all_pipelines()
    ]
    return (
        "Classify this AppForge request into exactly one pipeline. Prefer feature or bugfix "
        "when existing_repo is true. Prefer web-app-lite for trivial/small static web apps; "
        "prefer web-app for standard web apps; prefer fullstack-saas only for complex SaaS/backend/auth/data needs.\n\n"
        f"existing_repo: {existing_repo}\n"
        f"request:\n{prompt}\n\n"
        f"pipelines:\n{choices}\n"
    )


def _coerce_llm_route(payload: Any, *, prompt: str, existing_repo: bool) -> tuple[str, str, float, str] | None:
    if not isinstance(payload, dict):
        return None
    available = set(list_pipeline_names())
    pipeline = str(payload.get("pipeline") or "").strip()
    complexity = str(payload.get("complexity") or classify_prompt_complexity(prompt)).strip()
    try:
        confidence = float(payload.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    rationale = str(payload.get("rationale") or "LLM classifier returned no rationale.").strip()
    if pipeline not in available:
        return None
    if complexity in {"trivial", "small"} and pipeline == "web-app":
        pipeline = "web-app-lite"
    if complexity == "complex" and pipeline in {"web-app", "web-app-lite"}:
        pipeline = "fullstack-saas"
    if existing_repo and pipeline not in {"feature", "bugfix"}:
        pipeline = "feature"
        confidence = min(confidence, 0.75)
        rationale = rationale or "Existing repository work should use feature/bugfix pipelines."
    return pipeline, complexity, max(0.0, min(1.0, confidence)), rationale



def _extract_json_object(text: str) -> Any:
    """Extract the first JSON object from a provider response."""
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty LLM route response")
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    start = stripped.find("{")
    while start != -1:
        try:
            payload, _ = decoder.raw_decode(stripped[start:])
            return payload
        except json.JSONDecodeError:
            start = stripped.find("{", start + 1)
    raise ValueError("route response did not contain a JSON object")

def _try_llm_route(
    prompt: str,
    *,
    existing_repo: bool,
    llm_bridge_url: str | None,
    provider: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    timeout: int = 8,
) -> tuple[str, dict[str, int], dict[str, Any]] | None:
    if not llm_bridge_url:
        return None
    try:
        from . import llm_bridge

        response = llm_bridge.generate(
            llm_bridge_url,
            prompt=_route_prompt_for_llm(prompt, existing_repo=existing_repo),
            system=(
                "You are a conservative software-production request router. "
                "Return only the requested JSON decision."
            ),
            provider=provider,
            model=model,
            max_tokens=600 if max_tokens is None else max_tokens,
            temperature=0 if temperature is None else temperature,
            top_p=top_p,
            response_format=_CLASSIFIER_SCHEMA,
            timeout=timeout,
        )
        payload = _extract_json_object(str(response.get("text") or "{}"))
        decision = _coerce_llm_route(payload, prompt=prompt, existing_repo=existing_repo)
        if decision is None:
            return None
        pipeline, complexity, confidence, rationale = decision
        scores = {name: 0 for name in list_pipeline_names()}
        scores[pipeline] = int(round(confidence * 100))
        scores["_llm_confidence"] = int(round(confidence * 100))
        scores["_llm_complexity_small"] = 1 if complexity in {"trivial", "small"} else 0
        scores["_llm_route"] = 1
        routing = {
            "source": "llm-router",
            "pipeline": pipeline,
            "confidence": confidence,
            "complexity": complexity,
            "rationale": rationale,
            "usage": response.get("usage") or {},
        }
        return pipeline, scores, routing
    except Exception as exc:
        return None


def auto_select_pipeline(
    prompt: str,
    *,
    existing_repo: bool = False,
    llm_bridge_url: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    timeout: int = 8,
) -> tuple[str, dict[str, int]]:
    llm = _try_llm_route(
        prompt,
        existing_repo=existing_repo,
        llm_bridge_url=llm_bridge_url,
        provider=llm_provider,
        model=llm_model,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        timeout=timeout,
    )
    if llm is not None:
        pipeline, scores, routing = llm
        if float(routing.get("confidence") or 0.0) >= 0.6:
            return pipeline, scores
    return _keyword_scores(prompt, existing_repo=existing_repo)


def select_pipeline(
    prompt: str,
    *,
    existing_repo: bool = False,
    bridge_url: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    timeout: int = 8,
) -> tuple[str, dict[str, Any]]:
    """Return a pipeline and user-visible routing metadata.

    The LLM classifier is optional and conservative: if it is unavailable or low
    confidence, the local keyword/complexity router remains the source of truth.
    """
    fallback, scores = _keyword_scores(prompt, existing_repo=existing_repo)
    complexity = classify_prompt_complexity(prompt)
    routing: dict[str, Any] = {
        "source": "keyword-fallback",
        "pipeline": fallback,
        "confidence": 0.55,
        "complexity": complexity,
        "rationale": "키워드/복잡도 폴백 라우터가 파이프라인을 선택했습니다.",
        "scores": scores,
    }
    llm = _try_llm_route(
        prompt,
        existing_repo=existing_repo,
        llm_bridge_url=bridge_url,
        provider=provider,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        timeout=timeout,
    )
    if llm is None:
        return fallback, routing
    selected, llm_scores, llm_routing = llm
    confidence = float(llm_routing.get("confidence") or 0.0)
    llm_routing["scores"] = llm_scores
    llm_routing["fallback_pipeline"] = fallback
    if confidence < 0.6:
        routing.update({
            "source": "llm-router-low-confidence-fallback",
            "llm_candidate": selected,
            "llm_confidence": confidence,
            "llm_rationale": llm_routing.get("rationale"),
            "usage": llm_routing.get("usage") or {},
            "low_confidence_candidates": [candidate for candidate in (selected, fallback) if candidate],
        })
        return fallback, routing
    return selected, llm_routing
