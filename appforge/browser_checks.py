"""AppForge-owned browser observations over local snapshot bytes only."""
from __future__ import annotations

import mimetypes
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import jsonschema

from .models import ToolResult
from .evidence import context_value
from .util import redact, safe_resolve, truncate

CHECKS_SCHEMA = {
    "type": "array", "minItems": 1, "maxItems": 40,
    "items": {
        "type": "object", "additionalProperties": False,
        "required": ["criterion_id", "actions", "assertions"],
        "properties": {
            "criterion_id": {"type": "string", "minLength": 1, "maxLength": 200},
            "path": {"type": "string", "pattern": "^/[^:]*$", "maxLength": 500},
            "actions": {"type": "array", "maxItems": 20, "items": {
                "type": "object", "additionalProperties": False, "required": ["kind", "selector"],
                "properties": {
                    "kind": {"enum": ["click", "fill", "press", "check", "select"]},
                    "selector": {"type": "string", "minLength": 1, "maxLength": 500},
                    "value": {"type": "string", "maxLength": 2000},
                }}},
            "assertions": {"type": "array", "minItems": 1, "maxItems": 20, "items": {
                "type": "object", "additionalProperties": False, "required": ["kind", "selector"],
                "properties": {
                    "kind": {"enum": ["visible", "hidden", "text", "value", "count"]},
                    "selector": {"type": "string", "minLength": 1, "maxLength": 500},
                    "expected": {"type": ["string", "integer"], "maxLength": 2000},
                }}},
        },
    },
}


def criterion_ids(spec: dict[str, Any]) -> set[str]:
    identifiers = set()
    for item in spec.get("requirements", []) + spec.get("functional_requirements", []) + spec.get("non_functional_requirements", []):
        criteria = item.get("acceptance") or item.get("acceptance_criteria") or [item.get("measure")]
        for index, criterion in enumerate(criteria, 1):
            if not isinstance(criterion, str) or not criterion.strip():
                raise ValueError(f"Empty acceptance criterion in {item.get('id')}")
            identifier = f"{item.get('id')}/AC-{index:02d}"
            if identifier in identifiers:
                raise ValueError(f"Duplicate acceptance criterion {identifier}")
            identifiers.add(identifier)
    return identifiers


def contract_errors(spec: dict[str, Any]) -> list[str]:
    try:
        checks = spec.get("browser_checks")
        jsonschema.validate(checks, CHECKS_SCHEMA)
        expected = criterion_ids(spec)
        reported = [case["criterion_id"] for case in checks]
        if len(set(reported)) != len(reported):
            raise ValueError("Duplicate browser criterion IDs")
        if not expected or expected != set(reported):
            raise ValueError(f"Browser criteria must match the spec; missing={sorted(expected - set(reported))}, unknown={sorted(set(reported) - expected)}")
        for case in checks:
            for action in case["actions"]:
                if action["kind"] in {"fill", "press", "select"} and "value" not in action:
                    raise ValueError("Input actions require value")
            for assertion in case["assertions"]:
                if assertion["kind"] in {"text", "value", "count"} and "expected" not in assertion:
                    raise ValueError("Comparison assertions require expected")
                if assertion["kind"] == "count" and (not isinstance(assertion["expected"], int) or isinstance(assertion["expected"], bool) or assertion["expected"] < 0):
                    raise ValueError("Count assertions require a non-negative integer")
    except (jsonschema.ValidationError, ValueError) as exc:
        return [f"Invalid browser acceptance contract: {getattr(exc, 'message', str(exc))}"]
    return []


def run_browser_checks(root: Path, checks: list[dict[str, Any]], *, timeout: float = 90) -> ToolResult:
    """No app server, host loopback access, project test hooks, or external network."""
    try:
        from playwright.sync_api import sync_playwright, expect
    except ImportError:
        return ToolResult(False, error="Install AppForge browser runtime: python -m pip install playwright; python -m playwright install chromium",
                          data={"code": "BROWSER_UNAVAILABLE", "status": "blocked"})
    deadline = time.monotonic() + timeout
    def remaining_ms() -> float:
        cancellation = context_value("cancel_event")
        if cancellation is not None and cancellation.is_set():
            raise TimeoutError("Browser verification cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Browser verification budget exhausted")
        return max(1, min(5000, remaining * 1000))
    results = []
    errors: list[str] = []
    origin = "https://appforge.invalid"
    policy = "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; connect-src 'none'; frame-src 'none'; object-src 'none'; worker-src 'none'; form-action 'none'; base-uri 'self'"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, chromium_sandbox=True, timeout=remaining_ms())
            try:
                for case in checks:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Browser verification budget exhausted")
                    context = browser.new_context(accept_downloads=False, service_workers="block", permissions=[])
                    try:
                        def route_request(route):
                            url = urlsplit(route.request.url)
                            if url.scheme != "https" or url.netloc != "appforge.invalid":
                                errors.append("External resource blocked")
                                route.abort()
                                return
                            try:
                                relative = unquote(url.path).lstrip("/") or "index.html"
                                path = safe_resolve(root, relative)
                                if path.is_dir():
                                    path = safe_resolve(root, relative + "/index.html")
                                if not path.is_file() or path.stat().st_size > 20_000_000:
                                    raise ValueError("Missing or oversized page asset")
                                route.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                                              headers={"Content-Security-Policy": policy, "X-Content-Type-Options": "nosniff"})
                            except (OSError, ValueError):
                                errors.append("Missing or unsafe page asset")
                                route.abort()

                        context.route("**/*", route_request)
                        context.route_web_socket("**/*", lambda socket: socket.close())
                        page = context.new_page()
                        page.set_default_timeout(remaining_ms())
                        page.on("pageerror", lambda error: errors.append(redact(truncate(str(error), 1000))))
                        page.on("download", lambda download: download.cancel())
                        page.goto(origin + case.get("path", "/"), wait_until="load")
                        for action in case["actions"]:
                            page.set_default_timeout(remaining_ms())
                            locator = page.locator(action["selector"])
                            kind = action["kind"]
                            if kind in {"click", "check"}:
                                getattr(locator, kind)()
                            elif kind == "select":
                                locator.select_option(action["value"])
                            else:
                                getattr(locator, kind)(action["value"])
                        for assertion in case["assertions"]:
                            locator = page.locator(assertion["selector"])
                            kind = assertion["kind"]
                            if kind == "visible":
                                expect(locator).to_be_visible(timeout=remaining_ms())
                            elif kind == "hidden":
                                expect(locator).to_be_hidden(timeout=remaining_ms())
                            elif kind == "text":
                                expect(locator).to_have_text(str(assertion["expected"]), timeout=remaining_ms())
                            elif kind == "value":
                                expect(locator).to_have_value(str(assertion["expected"]), timeout=remaining_ms())
                            elif kind == "count":
                                expect(locator).to_have_count(int(assertion["expected"]), timeout=remaining_ms())
                        if errors:
                            raise ValueError("; ".join(errors[:5]))
                        results.append({"criterion_id": case["criterion_id"], "status": "passed"})
                    except Exception as exc:
                        results.append({"criterion_id": case["criterion_id"], "status": "failed",
                                        "reason": redact(truncate(str(exc), 2000))})
                    finally:
                        context.close()
            finally:
                browser.close()
    except Exception as exc:
        return ToolResult(False, error=redact(truncate(str(exc), 2000)),
                          data={"code": "BROWSER_UNAVAILABLE", "status": "blocked", "acceptance_results": results})
    passed = bool(results) and all(result["status"] == "passed" for result in results)
    return ToolResult(passed, error=None if passed else "Browser acceptance failed",
                      data={"acceptance_results": results, "test_origin": "appforge_harness"})
