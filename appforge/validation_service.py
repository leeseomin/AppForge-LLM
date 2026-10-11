"""One host-owned candidate validator for agent, runner, release and download."""
from __future__ import annotations

import hashlib
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .browser_checks import contract_errors, run_browser_checks
from .evidence import (POLICY_VERSION, artifact_digests, control_store, environment_digest,
                       context_value, execution_context, policy_digest, read_host, record_execution,
                       requirements_digest, validation_epoch, write_host)
from .models import ProjectLayout, ToolResult
from .pipelines import load_pipeline
from .projects import load_project
from .snapshots import digest, export_manifest, source_manifest
from .util import atomic_write_json, read_json, utc_now

WEB_PIPELINES = {"web-app", "web-app-lite"}


def harness_digest() -> str:
    root = Path(__file__).parent
    return digest({name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in (
        "browser_checks.py", "validation_service.py", "evidence.py", "snapshots.py",
        "tooling/tools/quality.py", "tooling/test_results.py", "tooling/command.py", "tooling/sandbox.py")})


def _check_budget(deadline: float) -> None:
    cancellation = context_value("cancel_event")
    if cancellation is not None and cancellation.is_set():
        raise TimeoutError("Candidate validation cancelled")
    if time.monotonic() >= deadline:
        raise TimeoutError("Candidate validation time budget exhausted")


def _failure(code: str, message: str, *, blocked: bool = False) -> dict[str, Any]:
    return {"passed": False, "status": "blocked" if blocked else "failed", "code": code,
            "reason": message, "records": [{"kind": "tool", "name": "candidate_validation",
                "required": True, "passed": False, "result": ToolResult(False, error=message,
                    data={"code": code, "reason": message}).to_dict()}]}


def verification_profile(workspace: Path, spec: dict[str, Any]) -> str:
    declared = spec.get("verification_profile")
    ranks = {"static-web": 0, "javascript-web": 1, "typescript-web": 2}
    if declared not in ranks:
        raise ValueError("The accepted spec must declare verification_profile: static-web, javascript-web, or typescript-web")
    files = source_manifest(workspace)
    package = read_json(workspace / "package.json", {})
    typed = any(path.endswith((".ts", ".tsx")) and not path.endswith(".d.ts") for path in files)
    detected = "typescript-web" if typed else "javascript-web" if (package.get("scripts") or {}).get("build") else "static-web"
    if ranks[detected] < ranks[declared]:
        raise ValueError("Project structure cannot downgrade the accepted verification profile")
    return detected


def _copy_snapshot(owner: Path, manifest: dict[str, Any], deadline: float) -> Path:
    parent = control_store(owner) / "snapshots"
    parent.mkdir(exist_ok=True, mode=0o700)
    snapshot = parent / uuid.uuid4().hex
    snapshot.mkdir(mode=0o700)
    for relative, entry in manifest.items():
        _check_budget(deadline)
        source = owner / relative
        raw = source.read_bytes()
        if source.is_symlink() or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("Source changed while creating validation snapshot")
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        target.chmod(0o755 if entry["executable"] else 0o644)
    for name in ("node_modules", ".venv"):
        source = owner / name
        if source.is_symlink():
            raise ValueError("Dependency root must not be a symlink")
        if source.is_dir():
            for path in source.rglob("*"):
                if path.is_symlink() and name == "node_modules" and not path.resolve().is_relative_to(source.resolve()):
                    raise ValueError("Node dependency symlink escapes the project")
            def copy_dependency(src, dst):
                _check_budget(deadline)
                return shutil.copy2(src, dst)
            shutil.copytree(source, snapshot / name, symlinks=True, copy_function=copy_dependency)
    artifacts = owner / ".appforge" / "artifacts"
    if artifacts.is_dir():
        target = snapshot / ".appforge" / "artifacts"
        target.mkdir(parents=True)
        for path in artifacts.glob("*.json"):
            if path.is_symlink():
                raise ValueError("Artifact symlink is not supported")
            shutil.copyfile(path, target / path.name)
    return snapshot


def _prune_snapshots(owner: Path, keep: Path) -> None:
    """Only AppForge-created copies are disposable; receipts and ZIPs are retained."""
    parent = control_store(owner) / "snapshots"
    copies = sorted((path for path in parent.iterdir() if re.fullmatch(r"[a-f0-9]{32}", path.name)
                     and path.is_dir() and not path.is_symlink()), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in copies[2:]:
        if path != keep:
            shutil.rmtree(path)


def validate_candidate(layout: ProjectLayout, *, timeout: float = 180, reuse: bool = True) -> dict[str, Any]:
    from .tooling.registry import ToolRegistry
    from .tooling.detection import quality_commands

    deadline = min(time.monotonic() + timeout, context_value("deadline", float("inf")))
    owner = layout.root
    project = load_project(layout)
    pipeline = load_pipeline(project["pipeline"])
    is_web = pipeline.name in WEB_PIPELINES
    spec_name = "engineering_spec" if pipeline.name == "web-app-lite" else "requirements_spec"
    spec = read_json(layout.artifacts / f"{spec_name}.json", {})
    if is_web:
        errors = contract_errors(spec)
        if errors:
            return _failure("ACCEPTANCE_CONTRACT_INVALID", "; ".join(errors))
        stage_name = "engineering_spec" if pipeline.name == "web-app-lite" else "specification"
        accepted = read_host(owner, f"checkpoint_{stage_name}.json") or {}
        if (accepted.get("status") != "completed" or accepted.get("policy_digest") != policy_digest(pipeline)
                or accepted.get("artifact_digests") != artifact_digests(owner, (spec_name,))):
            return _failure("SPEC_NOT_ACCEPTED", "Browser checks must come from the unchanged accepted specification")
    try:
        _check_budget(deadline)
        profile = verification_profile(owner, spec) if is_web else "project-checks"
        pinned = read_host(owner, "verification-profile.json")
        ranks = {"static-web": 0, "javascript-web": 1, "typescript-web": 2, "project-checks": 0}
        if pinned and ranks[profile] < ranks.get(pinned.get("profile"), 0):
            return _failure("PROFILE_DOWNGRADE", "Verification profile cannot be weakened by deleting project files")
        write_host(owner, "verification-profile.json", {"profile": profile})
        manifest = export_manifest(owner)
        identity = {"input_snapshot_id": digest(manifest), "requirements_digest": requirements_digest(owner),
                    "policy_digest": policy_digest(pipeline), "environment_digest": environment_digest(owner),
                    "harness_digest": harness_digest(),
                    "profile": profile}
        epoch = validation_epoch()
        previous = read_host(owner, "candidate.json")
        if reuse and epoch and previous and previous.get("epoch") == epoch and previous.get("passed"):
            if all(previous.get(key) == value for key, value in identity.items()):
                snapshot = Path(previous["snapshot_path"])
                if digest(export_manifest(snapshot)) == identity["input_snapshot_id"]:
                    return previous
        snapshot = _copy_snapshot(owner, manifest, deadline)
        atomic_write_json(snapshot / ".appforge" / "project.json", project)
        if environment_digest(snapshot) != identity["environment_digest"]:
            return _failure("ENVIRONMENT_CHANGED", "Installed dependencies changed while snapshotting")
        commands = quality_commands(snapshot)
        gates: list[tuple[str, bool]] = []
        if profile != "static-web" or commands.get("tests"):
            gates.append(("run_tests", True))
        if profile != "static-web":
            gates.append(("run_build", True))
        if profile == "typescript-web":
            gates.append(("run_typecheck", True))
        gates.append(("secret_scan", True))
        records = []
        execution_ids = []
        acceptance_results = []
        with execution_context(owner=str(owner), frozen_workspace=str(snapshot.resolve()),
                               profile=profile, deadline=deadline,
                               requirements_digest=identity["requirements_digest"], policy_digest=identity["policy_digest"]):
            for name, required in gates:
                _check_budget(deadline)
                tool = ToolRegistry().get(name)
                result = tool.run(snapshot, {"timeout": max(1, int(deadline - time.monotonic())),
                                             "allow_network": False, "allow_dependency_install": False})
                if name == "secret_scan":
                    result.data["execution_id"] = record_execution(snapshot, name, digest(manifest), result)
                passed = result.success and not result.data.get("skipped")
                if result.data.get("execution_id"):
                    execution_ids.append(result.data["execution_id"])
                records.append({"kind": "tool", "name": name, "required": required,
                                "passed": passed, "result": result.to_dict()})
            if is_web and all(record["passed"] for record in records):
                root = snapshot
                if profile != "static-web":
                    entry = next(((record["result"]["data"].get("entrypoint")) for record in records if record["name"] == "run_build"), None)
                    if not entry:
                        return _failure("BUILD_OUTPUT_MISSING", "No verified browser build entry point")
                    root = (snapshot / entry).parent
                result = run_browser_checks(root, spec["browser_checks"], timeout=max(1, deadline - time.monotonic()))
                execution_id = record_execution(snapshot, "browser_acceptance", digest(manifest), result)
                execution_ids.append(execution_id)
                acceptance_results = result.data.get("acceptance_results", [])
                records.append({"kind": "tool", "name": "browser_acceptance", "required": True,
                                "passed": result.success, "result": result.to_dict(), "execution_id": execution_id})
        _check_budget(deadline)
        if export_manifest(snapshot) != manifest or export_manifest(owner) != manifest:
            return _failure("SNAPSHOT_CHANGED", "Source changed during validation; discard the candidate")
        if requirements_digest(owner) != identity["requirements_digest"]:
            return _failure("SPEC_CHANGED", "Accepted requirements changed during validation")
        passed = bool(records) and all(record["passed"] for record in records)
        blocked = any(record["result"]["data"].get("status") == "blocked" or
                      record["result"]["data"].get("code") in {"TOOLCHAIN_UNAVAILABLE", "EXECUTION_SANDBOX_UNAVAILABLE"}
                      for record in records)
        receipt = {"schema_version": POLICY_VERSION, "receipt_id": "receipt-" + uuid.uuid4().hex,
                   "created_at": utc_now(), "epoch": epoch, **identity,
                   "passed": passed, "status": "verified" if passed else "blocked" if blocked else "failed",
                   "snapshot_path": str(snapshot), "manifest": manifest, "execution_ids": execution_ids,
                   "acceptance_results": acceptance_results, "records": records}
        write_host(owner, receipt["receipt_id"] + ".json", receipt)
        write_host(owner, "candidate.json", receipt)
        _prune_snapshots(owner, snapshot)
        return receipt
    except TimeoutError as exc:
        return _failure("VALIDATION_BUDGET_EXHAUSTED", str(exc), blocked=True)
    except (OSError, ValueError) as exc:
        return _failure("VALIDATION_INPUT_INVALID", str(exc))


def current_receipt(layout: ProjectLayout) -> dict[str, Any] | None:
    try:
        receipt = read_host(layout.root, "candidate.json")
        return receipt if _receipt_is_current(layout, receipt) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _receipt_is_current(layout: ProjectLayout, receipt: dict[str, Any] | None) -> bool:
    if not receipt or receipt.get("passed") is not True or receipt.get("schema_version") != POLICY_VERSION:
        return False
    project = load_project(layout)
    if (receipt.get("input_snapshot_id") != digest(export_manifest(layout.root))
            or receipt.get("requirements_digest") != requirements_digest(layout.root)
            or receipt.get("policy_digest") != policy_digest(load_pipeline(project["pipeline"]))
            or receipt.get("harness_digest") != harness_digest()):
        return False
    if not receipt.get("execution_ids"):
        return False
    for execution_id in receipt.get("execution_ids", []):
        record = read_host(layout.root, execution_id + ".json")
        if (not record or record.get("status") != "passed"
                or record.get("input_snapshot_id") != receipt["input_snapshot_id"]
                or record.get("output_snapshot_id") != receipt["input_snapshot_id"]
                or record.get("requirements_digest") != receipt["requirements_digest"]
                or record.get("policy_digest") != receipt["policy_digest"]
                or digest(record.get("result")) != record.get("result_digest")):
            return False
    return True


def archive_verification(layout: ProjectLayout, archive: Path, *, archive_sha256: str | None = None) -> dict[str, Any]:
    """Download uses protected records, never the mutable ZIP sidecar."""
    manifest = read_host(layout.root, "archive-" + archive.name + ".json")
    if not manifest or not manifest.get("receipt_id"):
        return {"status": "legacy_unverified", "reason": "Archive has no host verification receipt"}
    receipt = read_host(layout.root, manifest["receipt_id"] + ".json")
    if not receipt or not receipt.get("passed"):
        return {"status": "unverified", "reason": "Verification receipt is missing"}
    if not current_receipt(layout):
        return {"status": "stale", "reason": "Current validation is missing, failed, or no longer matches the source"}
    if not archive.is_file() or archive.is_symlink() or (archive_sha256 or hashlib.sha256(archive.read_bytes()).hexdigest()) != manifest.get("archive_sha256"):
        return {"status": "stale", "reason": "Archive bytes differ from the verified package"}
    if (not _receipt_is_current(layout, receipt)
            or receipt.get("input_snapshot_id") != manifest.get("input_snapshot_id")
            or receipt.get("manifest") != manifest.get("members")
            or digest(export_manifest(layout.root)) != receipt.get("input_snapshot_id")
            or requirements_digest(layout.root) != receipt.get("requirements_digest")
            or policy_digest(load_pipeline(load_project(layout)["pipeline"])) != receipt.get("policy_digest")):
        return {"status": "stale", "reason": "Source, requirements, or verification policy changed; revalidation required"}
    return {"status": "verified", "receipt_id": receipt["receipt_id"],
            "verified_at": receipt["created_at"], "profile": receipt["profile"],
            "criteria_verified": len(receipt.get("acceptance_results", [])),
            "archive_sha256": manifest["archive_sha256"]}
