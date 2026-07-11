from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .models import ProjectLayout, StageSpec
from .util import redact, truncate

REVIEW_STAGES = {"implementation", "verification", "fix", "regression"}


def independent_llm_review(
    layout: ProjectLayout,
    *,
    stage: StageSpec,
    stage_result: dict[str, Any],
    records: list[dict[str, Any]],
    driver: Any,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Run an optional low-cost LLM review pass after local gates pass.

    The pass is deliberately best-effort: bridge/network failures are recorded as
    skipped concerns rather than turning a locally valid build into a hard fail.
    """
    if stage.name not in REVIEW_STAGES:
        return {"enabled": False, "skipped": True, "passed": True, "reason": "stage_not_reviewed"}
    if os.environ.get("APPFORGE_DISABLE_INDEPENDENT_REVIEW", "").strip().lower() in {"1", "true", "yes", "on"}:
        return {"enabled": False, "skipped": True, "passed": True, "reason": "disabled_by_env"}
    bridge_url = getattr(driver, "bridge_url", None)
    if not bridge_url:
        return {"enabled": True, "skipped": True, "passed": True, "reason": "driver_has_no_bridge_url"}

    from . import llm_bridge

    files_changed = [str(item) for item in stage_result.get("files_changed") or []]
    evidence = _collect_review_evidence(layout, files_changed)
    prompt = {
        "task": "Review the stage result independently. Focus on correctness, missing requirements, unsafe behavior, and false success claims.",
        "stage": stage.name,
        "success_criteria": list(stage.success_criteria),
        "stage_result": stage_result,
        "gate_records": records,
        "files_changed": files_changed[:80],
        "file_evidence": evidence,
        "verdict_contract": {
            "verdict": "pass | concerns | block",
            "findings": [
                {
                    "severity": "info | warning | critical",
                    "file": "relative/path or null",
                    "finding": "specific issue",
                    "proposed_fix": "targeted fix",
                }
            ],
        },
    }
    try:
        response = llm_bridge.generate(
            str(bridge_url),
            prompt=json.dumps(prompt, ensure_ascii=False, indent=2),
            system="You are an independent code reviewer. Return strict JSON only.",
            provider=getattr(driver, "provider", None),
            model=os.environ.get("APPFORGE_REVIEW_MODEL") or getattr(driver, "model", None),
            max_tokens=1200,
            temperature=0,
            response_format=_review_response_format(),
            timeout=timeout,
        )
        payload = _extract_json_object(str(response.get("text") or ""))
        verdict = str(payload.get("verdict") or "concerns").casefold()
        if verdict not in {"pass", "concerns", "block"}:
            verdict = "concerns"
        findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
        normalized_findings = [_normalize_finding(item) for item in findings[:20] if isinstance(item, dict)]
        return {
            "enabled": True,
            "skipped": False,
            "passed": verdict != "block",
            "verdict": verdict,
            "findings": normalized_findings,
            "usage": response.get("usage") or {},
            "model": response.get("model"),
            "provider": response.get("provider"),
        }
    except Exception as exc:
        return {
            "enabled": True,
            "skipped": True,
            "passed": True,
            "reason": f"review_unavailable: {type(exc).__name__}: {exc}",
        }


def _review_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "appforge_independent_review",
            "schema": {
                "type": "object",
                "required": ["verdict", "findings"],
                "properties": {
                    "verdict": {"type": "string", "enum": ["pass", "concerns", "block"]},
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["severity", "finding", "proposed_fix"],
                            "properties": {
                                "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                                "file": {"type": ["string", "null"]},
                                "finding": {"type": "string"},
                                "proposed_fix": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
        },
    }


def _collect_review_evidence(layout: ProjectLayout, files_changed: list[str]) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for rel in files_changed:
        if rel.startswith(".appforge/"):
            continue
        path = _safe_relpath(layout.root, rel)
        if path is None or not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if len(raw) > 180_000 or b"\x00" in raw[:8192]:
            continue
        text = raw.decode("utf-8", errors="replace")
        evidence.append({"path": rel, "content": redact(truncate(text, 30_000))})
        if sum(len(item["content"]) for item in evidence) >= 80_000:
            break
    return evidence


def _safe_relpath(root: Path, rel: str) -> Path | None:
    candidate = Path(rel)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        return None
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return None
    return resolved


def _normalize_finding(item: dict[str, Any]) -> dict[str, Any]:
    severity = str(item.get("severity") or "warning").casefold()
    if severity not in {"info", "warning", "critical"}:
        severity = "warning"
    return {
        "severity": severity,
        "file": str(item.get("file") or "") or None,
        "finding": redact(truncate(str(item.get("finding") or ""), 2_000)),
        "proposed_fix": redact(truncate(str(item.get("proposed_fix") or ""), 2_000)),
        "source": "independent_llm_review",
    }


def _extract_json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text.strip())
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if match:
        value = json.loads(match.group(1))
        if isinstance(value, dict):
            return value
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        value = json.loads(text[start : end + 1])
        if isinstance(value, dict):
            return value
    raise ValueError("LLM review did not return a JSON object")
