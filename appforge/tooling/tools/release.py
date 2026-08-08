from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from appforge.constants import IGNORED_DIRS
from appforge.models import ToolResult
from appforge.util import atomic_write_json, iter_files, safe_resolve

from ..base import Tool
from ..detection import detect_stack
from .security import scan_file_bytes, scan_paths_for_secrets


class ArtifactInventoryTool(Tool):
    name = "artifact_inventory"
    description = "Inventory source, build, container, CI, documentation, and release artifacts."
    capability = "release"

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        candidates = [
            "dist",
            "build",
            "target/release",
            ".next",
            "out",
            "Dockerfile",
            "compose.yaml",
            "docker-compose.yml",
            ".github/workflows",
            "README.md",
            "LICENSE",
            "CHANGELOG.md",
        ]
        found: list[dict[str, Any]] = []
        for candidate in candidates:
            path = workspace / candidate
            if path.exists():
                found.append({"path": candidate, "type": "directory" if path.is_dir() else "file"})
        return ToolResult(success=True, data={"artifacts": found, "stack": detect_stack(workspace)})


class ReleaseReadinessTool(Tool):
    name = "release_readiness"
    description = "Check whether the project has the minimum files and reports for a release-ready handoff."
    capability = "release"

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        checks: list[dict[str, Any]] = []

        def check(name: str, passed: bool, detail: str, required: bool = True) -> None:
            checks.append({"name": name, "passed": passed, "detail": detail, "required": required})

        source_files = [p for p in iter_files(workspace, ignored_dirs=IGNORED_DIRS, max_files=20_000) if not p.relative_to(workspace).as_posix().startswith(".appforge/")]
        check("source_present", bool(source_files), f"{len(source_files)} source/project files")
        check("readme", (workspace / "README.md").exists(), "README.md exists")
        check("license", (workspace / "LICENSE").exists(), "LICENSE exists", required=False)
        check("gitignore", (workspace / ".gitignore").exists(), ".gitignore exists")
        check("verification_artifact", (workspace / ".appforge/artifacts/verification_report.json").exists() or (workspace / ".appforge/artifacts/regression_report.json").exists(), "verification or regression report exists")
        check("security_artifact", (workspace / ".appforge/artifacts/security_report.json").exists(), "security report exists", required=False)
        required_failures = [item for item in checks if item["required"] and not item["passed"]]
        return ToolResult(
            success=not required_failures,
            error=None if not required_failures else f"{len(required_failures)} required readiness check(s) failed",
            data={"passed": not required_failures, "checks": checks},
        )


class ArchiveWorkspaceTool(Tool):
    name = "archive_workspace"
    description = "Create a source archive while excluding secrets, VCS state, dependencies, and caches."
    capability = "release"
    destructive = False

    def execute(self, workspace: Path, inputs: dict[str, Any]) -> ToolResult:
        name = str(inputs.get("name", f"{workspace.name}-source.zip"))
        output = safe_resolve(workspace, str(inputs.get("output", f".appforge/reports/{name}")))
        output.parent.mkdir(parents=True, exist_ok=True)
        sensitive_names = {
            ".env", ".env.local", ".env.production", ".env.development", ".env.test",
            "id_rsa", "id_ed25519", "credentials", "credentials.json", "service-account.json",
            ".npmrc", ".pypirc", ".netrc", "auth.json",
        }

        def safe_for_archive(path: Path) -> bool:
            rel = path.relative_to(workspace).as_posix()
            name_lower = path.name.casefold()
            if path == output or rel.startswith(".appforge/"):
                return False
            if name_lower in sensitive_names:
                return False
            if name_lower.startswith(".env.") and name_lower != ".env.example":
                return False
            if path.suffix.casefold() in {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}:
                return False
            return True

        files = [
            path
            for path in iter_files(workspace, ignored_dirs=IGNORED_DIRS, max_files=100_000)
            if safe_for_archive(path)
        ]
        max_file_bytes = int(inputs.get("max_file_bytes", 20_000_000))
        findings = scan_paths_for_secrets(
            workspace,
            files,
            max_file_bytes=max_file_bytes,
        )
        if findings:
            return ToolResult(
                success=False,
                error=f"Archive blocked by {len(findings)} secret-scan finding(s)",
                data={
                    "code": "ARCHIVE_SECRET_SCAN_FAILED",
                    "passed": False,
                    "findings": findings,
                },
            )

        temporary_handle = tempfile.NamedTemporaryFile(
            prefix=f".{output.name}.",
            suffix=".tmp",
            dir=output.parent,
            delete=False,
        )
        temporary = Path(temporary_handle.name)
        temporary_handle.close()
        expected_names = [path.relative_to(workspace).as_posix() for path in files]
        try:
            exact_findings: list[dict[str, Any]] = []
            with zipfile.ZipFile(
                temporary,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as archive:
                for path, relative in zip(files, expected_names, strict=True):
                    try:
                        raw = path.read_bytes()
                    except OSError:
                        exact_findings.append(
                            {
                                "rule": "unreadable_file",
                                "path": relative,
                                "line": None,
                                "preview": "[REDACTED]",
                            }
                        )
                        break
                    exact_findings.extend(
                        scan_file_bytes(raw, relative, max_file_bytes=max_file_bytes)
                    )
                    if exact_findings:
                        break
                    archive.writestr(relative, raw)
            if exact_findings:
                return ToolResult(
                    success=False,
                    error=f"Archive blocked by {len(exact_findings)} secret-scan finding(s)",
                    data={
                        "code": "ARCHIVE_SECRET_SCAN_FAILED",
                        "passed": False,
                        "findings": exact_findings,
                    },
                )
            with zipfile.ZipFile(temporary, "r") as archive:
                actual_names = archive.namelist()
                if actual_names != expected_names or any(
                    name.startswith("/") or ".." in Path(name).parts
                    for name in actual_names
                ):
                    return ToolResult(
                        success=False,
                        error="Archive manifest verification failed",
                        data={"code": "ARCHIVE_MANIFEST_MISMATCH", "passed": False},
                    )
                archived_findings: list[dict[str, Any]] = []
                for relative in actual_names:
                    archived_findings.extend(
                        scan_file_bytes(
                            archive.read(relative),
                            relative,
                            max_file_bytes=max_file_bytes,
                        )
                    )
                if archived_findings:
                    return ToolResult(
                        success=False,
                        error=(
                            "Archive blocked by "
                            f"{len(archived_findings)} post-write secret-scan finding(s)"
                        ),
                        data={
                            "code": "ARCHIVE_SECRET_SCAN_FAILED",
                            "passed": False,
                            "findings": archived_findings,
                        },
                    )
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        manifest = {"archive": output.relative_to(workspace).as_posix(), "files": len(files), "bytes": output.stat().st_size}
        atomic_write_json(output.with_suffix(output.suffix + ".manifest.json"), manifest)
        return ToolResult(success=True, data=manifest, artifacts=[str(output), str(output.with_suffix(output.suffix + ".manifest.json"))])
