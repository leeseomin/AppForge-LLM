"""Bounded adapters for project-owned test results (not independent proof)."""
from __future__ import annotations

import json
import re
import shlex
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def reporter_command(workspace: Path, command: list[str]) -> tuple[list[str], str, Path | None]:
    report_dir = workspace / ".appforge-validation"
    report_dir.mkdir(exist_ok=True)
    report = report_dir / (uuid.uuid4().hex + ".json")
    if "pytest" in command:
        return [*command, f"--junitxml={report.with_suffix('.xml')}"], "junit", report.with_suffix(".xml")
    script = ""
    package = workspace / "package.json"
    if package.exists() and command[0] in {"npm", "pnpm", "yarn"}:
        script = str((json.loads(package.read_text()).get("scripts") or {}).get("test") or "")
    tokens = shlex.split(script) if script else list(command)
    if tokens and tokens[0] == "node" and "--test" in tokens and not any(token in tokens for token in ("&&", ";", "|")):
        # NODE_OPTIONS selects the reporter without bypassing package-manager
        # hooks or moving flags after positional test paths.
        return command, "node-junit", report.with_suffix(".xml")
    if tokens and tokens[0] in {"vitest", "jest"}:
        flags = (["--run", "--reporter=json", f"--outputFile={report}"] if tokens[0] == "vitest"
                 else ["--runInBand", "--json", f"--outputFile={report}"])
        separator = ["--"] if script and command[0] == "npm" else []
        return [*command, *separator, *flags], "json", report
    return command, "unsupported", None


def test_summary(kind: str, report: Path | None, stdout: str) -> dict[str, Any]:
    cases: list[dict[str, str]] = []
    if report:
        if not report.is_file() or report.is_symlink() or report.stat().st_size > 5_000_000:
            raise ValueError("Test runner did not produce a bounded structured report")
        raw = report.read_text(encoding="utf-8")
    else:
        raw = stdout
    if kind in {"junit", "node-junit"}:
        if "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper():
            raise ValueError("DTD/entity declarations are forbidden in test reports")
        root = ET.fromstring(raw)
        for case in root.iter("testcase"):
            status = "failed" if case.find("failure") is not None or case.find("error") is not None else "passed"
            if case.find("skipped") is not None:
                status = "skipped"
            cases.append({"id": f"{case.get('classname', '')}::{case.get('name', '')}", "status": status})
    elif kind == "json":
        data = json.loads(raw)
        for suite in data.get("testResults", []):
            for case in suite.get("assertionResults", []):
                status = case.get("status")
                status = "skipped" if status in {"pending", "disabled", "skipped", "todo"} else status
                if status not in {"passed", "failed", "skipped"}:
                    raise ValueError("Unknown test result status")
                cases.append({"id": f"{suite.get('name')}::{case.get('fullName') or case.get('title')}", "status": status})
    elif kind == "tap":
        # Node TAP is a project reporter. Browser acceptance is separately host-owned.
        for match in re.finditer(r"^(not )?ok\s+\d+\s*-?\s*(.+)$", raw, re.MULTILINE):
            name = match.group(2).strip()
            status = "skipped" if re.search(r"#\s*(SKIP|TODO)\b", name, re.I) else "failed" if match.group(1) else "passed"
            cases.append({"id": name, "status": status})
    else:
        raise ValueError("No supported structured test reporter; use node --test, pytest, Vitest, or Jest")
    identifiers = [case["id"] for case in cases]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Duplicate test identifiers in report")
    counts = {status: sum(case["status"] == status for case in cases) for status in ("passed", "failed", "skipped")}
    return {"collected": len(cases), "executed": counts["passed"] + counts["failed"],
            **counts, "test_ids": identifiers,
            "executed_test_ids": [case["id"] for case in cases if case["status"] != "skipped"],
            "failed_test_ids": [case["id"] for case in cases if case["status"] == "failed"]}
