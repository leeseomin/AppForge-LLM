from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from appforge.util import command_exists, python_module_exists


def detect_stack(workspace: Path) -> dict[str, Any]:
    files = {path.name for path in workspace.iterdir() if path.is_file()}
    languages: list[str] = []
    frameworks: list[str] = []
    package_managers: list[str] = []
    manifests: list[str] = []

    def add(target: list[str], value: str) -> None:
        if value not in target:
            target.append(value)

    if {"pyproject.toml", "requirements.txt", "setup.py", "Pipfile"} & files:
        add(languages, "python")
        manifests.extend(sorted({"pyproject.toml", "requirements.txt", "setup.py", "Pipfile"} & files))
        if "pyproject.toml" in files:
            text = (workspace / "pyproject.toml").read_text(encoding="utf-8", errors="ignore").lower()
            for needle, framework in (("fastapi", "fastapi"), ("django", "django"), ("flask", "flask"), ("streamlit", "streamlit")):
                if needle in text:
                    add(frameworks, framework)

    package_json = workspace / "package.json"
    package_data: dict[str, Any] = {}
    if package_json.exists():
        add(languages, "javascript/typescript")
        manifests.append("package.json")
        try:
            package_data = json.loads(package_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            package_data = {}
        deps = {**(package_data.get("dependencies") or {}), **(package_data.get("devDependencies") or {})}
        for needle, framework in (
            ("next", "nextjs"),
            ("react", "react"),
            ("vue", "vue"),
            ("svelte", "svelte"),
            ("@angular/core", "angular"),
            ("electron", "electron"),
            ("@tauri-apps/api", "tauri"),
            ("express", "express"),
            ("nestjs", "nestjs"),
        ):
            if needle in deps:
                add(frameworks, framework)
        if "pnpm-lock.yaml" in files:
            package_managers.append("pnpm")
        elif "yarn.lock" in files:
            package_managers.append("yarn")
        else:
            package_managers.append("npm")

    marker_map = {
        "Cargo.toml": ("rust", "cargo"),
        "go.mod": ("go", "go"),
        "pom.xml": ("java", "maven"),
        "build.gradle": ("java", "gradle"),
        "build.gradle.kts": ("kotlin", "gradle"),
        "pubspec.yaml": ("dart", "flutter"),
        "Package.swift": ("swift", "swiftpm"),
        "composer.json": ("php", "composer"),
        "Gemfile": ("ruby", "bundler"),
    }
    for marker, (language, manager) in marker_map.items():
        if marker in files:
            add(languages, language)
            add(package_managers, manager)
            manifests.append(marker)
            if marker == "pubspec.yaml":
                add(frameworks, "flutter")

    for path in workspace.glob("*.csproj"):
        add(languages, "csharp")
        add(package_managers, "dotnet")
        manifests.append(path.name)

    if (workspace / "Dockerfile").exists() or (workspace / "compose.yaml").exists() or (workspace / "docker-compose.yml").exists():
        add(frameworks, "docker")

    return {
        "languages": languages,
        "frameworks": frameworks,
        "package_managers": package_managers,
        "manifests": manifests,
        "package_json": package_data,
        "empty": not any(path for path in workspace.iterdir() if path.name != ".appforge"),
    }


def quality_commands(workspace: Path) -> dict[str, list[str] | None]:
    detected = detect_stack(workspace)
    commands: dict[str, list[str] | None] = {"tests": None, "lint": None, "typecheck": None, "build": None, "format": None}

    package_data = detected.get("package_json") or {}
    scripts = package_data.get("scripts") or {}
    managers = detected.get("package_managers") or []
    if package_data:
        manager = next((x for x in ("pnpm", "yarn", "npm") if x in managers), "npm")
        prefix = [manager, "run"]
        if "test" in scripts:
            commands["tests"] = [manager, "test"] if manager == "npm" else [manager, "test"]
        for name, key in (("lint", "lint"), ("typecheck", "typecheck"), ("build", "build"), ("format", "format")):
            if key in scripts:
                commands[name] = prefix + [key]
        if commands["typecheck"] is None and "typescript" in {**(package_data.get("dependencies") or {}), **(package_data.get("devDependencies") or {})}:
            executable = "npx" if manager == "npm" else manager
            commands["typecheck"] = [executable, "tsc", "--noEmit"] if executable == "npx" else [executable, "exec", "tsc", "--noEmit"]

    languages = set(detected["languages"])
    if "python" in languages:
        python_cmd = sys.executable or "python"
        has_tests = (workspace / "tests").exists() or any(workspace.glob("test_*.py"))
        if has_tests:
            commands["tests"] = (
                [python_cmd, "-m", "pytest", "-q"]
                if python_module_exists("pytest")
                else [python_cmd, "-m", "unittest", "discover"]
            )
        if python_module_exists("ruff"):
            commands["lint"] = [python_cmd, "-m", "ruff", "check", "."]
            commands["format"] = [python_cmd, "-m", "ruff", "format", "--check", "."]
        if python_module_exists("mypy"):
            commands["typecheck"] = [python_cmd, "-m", "mypy", "."]
        commands["build"] = (
            [python_cmd, "-m", "build", "--no-isolation"]
            if (workspace / "pyproject.toml").exists() and python_module_exists("build")
            else [python_cmd, "-m", "compileall", "-q", "."]
        )

    if "go" in languages:
        commands.update({"tests": ["go", "test", "./..."], "lint": ["go", "vet", "./..."], "build": ["go", "build", "./..."]})
    if "rust" in languages:
        commands.update({"tests": ["cargo", "test"], "lint": ["cargo", "clippy", "--", "-D", "warnings"], "build": ["cargo", "build", "--release"], "format": ["cargo", "fmt", "--", "--check"]})
    if "java" in languages or "kotlin" in languages:
        if "maven" in managers:
            commands.update({"tests": ["mvn", "test"], "build": ["mvn", "package", "-DskipTests"]})
        elif "gradle" in managers:
            wrapper = "./gradlew" if (workspace / "gradlew").exists() else "gradle"
            commands.update({"tests": [wrapper, "test"], "build": [wrapper, "build", "-x", "test"]})
    if "csharp" in languages:
        commands.update({"tests": ["dotnet", "test"], "build": ["dotnet", "build", "--configuration", "Release"]})
    if "dart" in languages:
        commands.update({"tests": ["flutter", "test"], "lint": ["flutter", "analyze"], "build": ["flutter", "build", "web"]})

    # Drop commands whose executable is not installed. Relative wrappers remain valid.
    for key, command in list(commands.items()):
        if command and not command[0].startswith("./") and not command_exists(command[0]):
            commands[key] = None
    return commands
