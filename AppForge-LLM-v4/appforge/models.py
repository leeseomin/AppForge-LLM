from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class GateSpec:
    tool: str
    required: bool = True
    inputs: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GateSpec":
        return cls(
            tool=str(data["tool"]),
            required=bool(data.get("required", True)),
            inputs=dict(data.get("inputs") or {}),
        )


@dataclass(frozen=True)
class StageSpec:
    name: str
    description: str
    skill: str
    produces: tuple[str, ...]
    tools: tuple[str, ...] = ()
    checkpoint: bool = True
    approval: bool = False
    review_focus: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()
    gates: tuple[GateSpec, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StageSpec":
        return cls(
            name=str(data["name"]),
            description=str(data.get("description", "")),
            skill=str(data["skill"]),
            produces=tuple(str(x) for x in data.get("produces", [])),
            tools=tuple(str(x) for x in data.get("tools", [])),
            checkpoint=bool(data.get("checkpoint", True)),
            approval=bool(data.get("approval", False)),
            review_focus=tuple(str(x) for x in data.get("review_focus", [])),
            success_criteria=tuple(str(x) for x in data.get("success_criteria", [])),
            gates=tuple(GateSpec.from_dict(x) for x in data.get("gates", [])),
        )


@dataclass(frozen=True)
class PipelineSpec:
    name: str
    version: str
    category: str
    description: str
    keywords: tuple[str, ...]
    default_mode: Literal["autonomous", "guided"]
    max_stage_attempts: int
    stages: tuple[StageSpec, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PipelineSpec":
        orchestration = data.get("orchestration") or {}
        return cls(
            name=str(data["name"]),
            version=str(data.get("version", "1.0")),
            category=str(data.get("category", "custom")),
            description=str(data.get("description", "")),
            keywords=tuple(str(x).lower() for x in (data.get("match") or {}).get("keywords", [])),
            default_mode=str(orchestration.get("default_mode", "autonomous")),  # type: ignore[arg-type]
            max_stage_attempts=int(orchestration.get("max_stage_attempts", 3)),
            stages=tuple(StageSpec.from_dict(x) for x in data["stages"]),
        )

    def stage(self, name: str) -> StageSpec:
        for stage in self.stages:
            if stage.name == name:
                return stage
        raise KeyError(f"Unknown stage {name!r} in pipeline {self.name!r}")


@dataclass(frozen=True)
class ProjectLayout:
    root: Path
    control: Path
    artifacts: Path
    checkpoints: Path
    prompts: Path
    logs: Path
    reports: Path
    memory: Path

    @classmethod
    def from_root(cls, root: Path) -> "ProjectLayout":
        root = root.resolve()
        control = root / ".appforge"
        return cls(
            root=root,
            control=control,
            artifacts=control / "artifacts",
            checkpoints=control / "checkpoints",
            prompts=control / "prompts",
            logs=control / "logs",
            reports=control / "reports",
            memory=control / "memory",
        )


@dataclass
class ToolResult:
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    error: str | None = None
    duration_seconds: float = 0.0
    command: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DriverResult:
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    command: list[str]
    final_message_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
