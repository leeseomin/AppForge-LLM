from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .checkpoints import next_stage, read_checkpoint, write_checkpoint
from .constants import DEFAULT_SAFETY
from .drivers import DEFAULT_LLM_BRIDGE_URL, DriverError, create_driver
from .gates import review_stage, run_declared_gates, validate_stage_artifacts, validate_stage_result
from .models import ProjectLayout
from .pipelines import all_pipelines, auto_select_pipeline, load_pipeline
from .projects import ProjectError, find_project, initialize_project, load_project
from .prompting import build_stage_prompt
from .runner import PipelineRunner
from .tooling.detection import detect_stack, quality_commands
from .tooling.registry import ToolRegistry
from .util import atomic_write_text, utc_now

app = typer.Typer(
    name="appforge",
    help="Pipeline-driven software production for coding agents.",
    no_args_is_help=True,
    add_completion=False,
)
tool_app = typer.Typer(help="Inspect and execute auditable AppForge tools.", no_args_is_help=True)
auth_app = typer.Typer(help="Connect and manage external LLM providers.", no_args_is_help=True)
app.add_typer(tool_app, name="tool")
app.add_typer(auth_app, name="auth")
console = Console()


def _layout(project: Path) -> ProjectLayout:
    try:
        return find_project(project)
    except ProjectError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _json_input(value: str | None, file: Path | None) -> dict[str, Any]:
    if value and file:
        raise typer.BadParameter("Use either --input or --input-file, not both")
    try:
        if file:
            data = json.loads(file.read_text(encoding="utf-8"))
        elif value:
            data = json.loads(value)
        else:
            data = {}
    except (OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(f"Invalid JSON input: {exc}") from exc
    if not isinstance(data, dict):
        raise typer.BadParameter("Tool input must be a JSON object")
    return data


@app.command()
def version() -> None:
    """Print the OpenAppForge version."""
    console.print(__version__)


@app.command("pipelines")
def pipelines_command() -> None:
    """List production pipelines."""
    table = Table(title="OpenAppForge pipelines")
    table.add_column("Name", style="bold")
    table.add_column("Category")
    table.add_column("Stages", overflow="fold")
    table.add_column("Description", overflow="fold")
    for pipeline in all_pipelines():
        table.add_row(
            pipeline.name,
            pipeline.category,
            " → ".join(stage.name for stage in pipeline.stages),
            pipeline.description,
        )
    console.print(table)


@app.command()
def route(prompt: str = typer.Argument(..., help="Software request to classify"), existing_repo: bool = typer.Option(False, "--existing-repo")) -> None:
    """Preview automatic pipeline selection."""
    selected, scores = auto_select_pipeline(prompt, existing_repo=existing_repo)
    console.print(Panel.fit(f"Selected pipeline: [bold]{selected}[/bold]"))
    table = Table("Pipeline", "Score")
    for name, score in sorted(scores.items(), key=lambda item: (-item[1], item[0])):
        table.add_row(name, str(score))
    console.print(table)


@app.command("new")
def new_project(
    prompt: str = typer.Argument(..., help="Natural-language product request"),
    name: str | None = typer.Option(None, "--name", "-n"),
    pipeline: str = typer.Option("auto", "--pipeline", "-p"),
    mode: str | None = typer.Option(None, "--mode", help="autonomous or guided"),
    projects_dir: Path = typer.Option(Path("projects"), "--projects-dir"),
    target: Path | None = typer.Option(None, "--target", help="Initialize AppForge inside an existing repository"),
) -> None:
    """Create or adopt an AppForge project workspace."""
    try:
        layout = initialize_project(
            prompt,
            projects_dir=projects_dir,
            name=name,
            pipeline_name=pipeline,
            mode=mode,
            existing_target=target,
        )
    except (FileExistsError, FileNotFoundError, ProjectError, ValueError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(2) from exc
    project = load_project(layout)
    console.print(
        Panel.fit(
            f"[bold green]Project initialized[/bold green]\n"
            f"Path: {layout.root}\nPipeline: {project['pipeline']}\nMode: {project['mode']}\n"
            f"Next: appforge run {layout.root} --driver llm-bridge",
            title="OpenAppForge",
        )
    )


@app.command()
def forge(
    prompt: str = typer.Argument(..., help="Natural-language command describing the application or change"),
    name: str | None = typer.Option(None, "--name", "-n"),
    pipeline: str = typer.Option("auto", "--pipeline", "-p"),
    mode: str | None = typer.Option(None, "--mode", help="autonomous or guided"),
    projects_dir: Path = typer.Option(Path("projects"), "--projects-dir"),
    target: Path | None = typer.Option(None, "--target", help="Adopt an existing repository instead of creating a project"),
    driver: str = typer.Option("llm-bridge", "--driver", help="auto or llm-bridge"),
    model: str | None = typer.Option(None, "--model"),
    llm_bridge_url: str = typer.Option(DEFAULT_LLM_BRIDGE_URL, "--llm-bridge-url"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    auto_approve: bool = typer.Option(True, "--auto-approve/--pause-for-approval"),
    allow_network: bool = typer.Option(False, "--allow-network", help="Allow dependency downloads and remote audits"),
    allow_destructive: bool = typer.Option(False, "--allow-destructive", help="Allow destructive AppForge tool operations"),
    allow_dependency_install: bool = typer.Option(
        DEFAULT_SAFETY["allow_dependency_install"],
        "--allow-dependency-install/--no-dependency-install",
        help="Allow package managers to install dependencies into the workspace",
    ),
    unsafe_agent: bool = typer.Option(False, "--unsafe-agent", help="Bypass the coding agent's permission sandbox; isolated workspaces only"),
    max_stage_attempts: int | None = typer.Option(None, "--max-stage-attempts", min=1, max=10),
    stage_timeout: int = typer.Option(3600, "--stage-timeout", min=60),
    max_turns: int | None = typer.Option(None, "--max-turns", min=1),
) -> None:
    """Create a project from one input command and run it to release-ready handoff."""
    try:
        selected_driver = create_driver(
            driver,
            unsafe=unsafe_agent,
            model=model,
            max_turns=max_turns,
            bridge_url=llm_bridge_url,
            llm_provider=llm_provider,
        )
        layout = initialize_project(
            prompt,
            projects_dir=projects_dir,
            name=name,
            pipeline_name=pipeline,
            mode=mode,
            existing_target=target,
        )
        runner = PipelineRunner(
            layout,
            selected_driver,
            auto_approve=auto_approve,
            allow_network=allow_network,
            allow_destructive=allow_destructive,
            allow_dependency_install=allow_dependency_install,
            max_stage_attempts=max_stage_attempts,
            stage_timeout=stage_timeout,
        )
        summary = runner.run()
    except (DriverError, FileExistsError, FileNotFoundError, ProjectError, ValueError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(2) from exc
    style = "green" if summary.success else "yellow" if summary.status == "awaiting_human" else "red"
    console.print(
        Panel.fit(
            f"Project: {layout.root}\n"
            f"Pipeline: {load_project(layout)['pipeline']}\n"
            f"Status: [{style}]{summary.status}[/{style}]\n"
            f"Completed this run: {', '.join(summary.completed_stages) or '-'}\n"
            f"{summary.message}",
            title="OpenAppForge · one-command forge",
        )
    )
    if not summary.success:
        raise typer.Exit(3 if summary.status == "awaiting_human" else 1)


@app.command()
def status(project: Path = typer.Argument(Path("."), help="Project path")) -> None:
    """Show stage and checkpoint status."""
    layout = _layout(project)
    metadata = load_project(layout)
    pipeline = load_pipeline(str(metadata["pipeline"]))
    table = Table(title=f"{metadata['name']} · {pipeline.name}")
    table.add_column("Stage")
    table.add_column("Status")
    table.add_column("Attempt")
    table.add_column("Artifacts", overflow="fold")
    for stage in pipeline.stages:
        cp = read_checkpoint(layout, stage.name)
        table.add_row(
            stage.name,
            str(cp.get("status")) if cp else "pending",
            str(cp.get("attempt", "-")) if cp else "-",
            ", ".join((cp.get("artifacts") or {}).keys()) if cp else "",
        )
    console.print(table)
    nxt = next_stage(layout, pipeline)
    console.print(f"Next stage: [bold]{nxt or 'complete'}[/bold]")


@app.command()
def prompt(
    project: Path = typer.Argument(Path("."), help="Project path"),
    stage: str | None = typer.Option(None, "--stage"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """Render the next stage packet for manual use in any coding assistant."""
    layout = _layout(project)
    metadata = load_project(layout)
    pipeline = load_pipeline(str(metadata["pipeline"]))
    stage_name = stage or next_stage(layout, pipeline)
    if stage_name is None:
        console.print("Pipeline is complete.")
        return
    spec = pipeline.stage(stage_name)
    text = build_stage_prompt(layout, project=metadata, pipeline=pipeline, stage=spec, attempt=1)
    if output:
        output = output.expanduser().resolve()
        atomic_write_text(output, text)
        console.print(f"Wrote {output}")
    else:
        console.print(text)


@app.command("complete")
def complete_stage(
    project: Path = typer.Argument(Path("."), help="Project path"),
    stage: str | None = typer.Option(None, "--stage"),
    auto_approve: bool = typer.Option(True, "--auto-approve/--await-approval"),
    allow_network: bool = typer.Option(False, "--allow-network"),
    allow_destructive: bool = typer.Option(False, "--allow-destructive"),
    allow_dependency_install: bool = typer.Option(
        DEFAULT_SAFETY["allow_dependency_install"],
        "--allow-dependency-install/--no-dependency-install",
        help="Allow package managers to install dependencies into the workspace",
    ),
) -> None:
    """Validate and checkpoint work completed manually by the current coding assistant."""
    layout = _layout(project)
    metadata = load_project(layout)
    pipeline = load_pipeline(str(metadata["pipeline"]))
    stage_name = stage or next_stage(layout, pipeline)
    if stage_name is None:
        console.print("Pipeline is already complete.")
        return
    spec = pipeline.stage(stage_name)
    result_ok, stage_result, result_error = validate_stage_result(layout, spec)
    artifacts_ok, artifact_records, artifact_paths = validate_stage_artifacts(layout, spec)
    gates_ok, gate_records = run_declared_gates(
        layout,
        stage=spec,
        allow_network=allow_network,
        allow_destructive=allow_destructive,
        allow_dependency_install=allow_dependency_install,
    )
    records = artifact_records + gate_records
    if not result_ok:
        records.insert(0, {"kind": "completion_record", "name": "stage-result", "required": True, "passed": False, "error": result_error})
    review = review_stage(spec, records, stage_result)
    passed = result_ok and artifacts_ok and gates_ok and review["passed"]
    status_value = "completed"
    if passed and spec.approval and metadata.get("mode") == "guided" and not auto_approve:
        status_value = "awaiting_human"
    write_checkpoint(
        layout,
        pipeline=pipeline,
        stage=stage_name,
        status=status_value if passed else "failed",
        attempt=int((read_checkpoint(layout, stage_name) or {}).get("attempt", 1)),
        artifacts=artifact_paths,
        gates=records,
        review=review,
        metadata={"stage_result": stage_result, "manual_completion": True},
    )
    console.print_json(data={"stage": stage_name, "passed": passed, "status": status_value if passed else "failed", "review": review, "gates": records})
    if not passed:
        raise typer.Exit(1)


@app.command()
def run(
    project: Path = typer.Argument(Path("."), help="Project path"),
    driver: str = typer.Option("llm-bridge", "--driver", help="auto or llm-bridge"),
    model: str | None = typer.Option(None, "--model"),
    llm_bridge_url: str = typer.Option(DEFAULT_LLM_BRIDGE_URL, "--llm-bridge-url"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    only_stage: str | None = typer.Option(None, "--stage"),
    auto_approve: bool = typer.Option(True, "--auto-approve/--pause-for-approval"),
    allow_network: bool = typer.Option(False, "--allow-network", help="Allow dependency downloads and remote audits"),
    allow_destructive: bool = typer.Option(False, "--allow-destructive", help="Allow destructive AppForge tool operations"),
    allow_dependency_install: bool = typer.Option(
        DEFAULT_SAFETY["allow_dependency_install"],
        "--allow-dependency-install/--no-dependency-install",
        help="Allow package managers to install dependencies into the workspace",
    ),
    unsafe_agent: bool = typer.Option(False, "--unsafe-agent", help="Bypass the coding agent's permission sandbox; use only in an isolated workspace"),
    max_stage_attempts: int | None = typer.Option(None, "--max-stage-attempts", min=1, max=10),
    stage_timeout: int = typer.Option(3600, "--stage-timeout", min=60),
    max_turns: int | None = typer.Option(None, "--max-turns", min=1),
) -> None:
    """Run the pipeline using the external LLM bridge."""
    layout = _layout(project)
    try:
        selected_driver = create_driver(
            driver,
            unsafe=unsafe_agent,
            model=model,
            max_turns=max_turns,
            bridge_url=llm_bridge_url,
            llm_provider=llm_provider,
        )
        runner = PipelineRunner(
            layout,
            selected_driver,
            auto_approve=auto_approve,
            allow_network=allow_network,
            allow_destructive=allow_destructive,
            allow_dependency_install=allow_dependency_install,
            max_stage_attempts=max_stage_attempts,
            stage_timeout=stage_timeout,
        )
        summary = runner.run(only_stage=only_stage)
    except (DriverError, FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(2) from exc
    style = "green" if summary.success else "yellow" if summary.status == "awaiting_human" else "red"
    console.print(
        Panel.fit(
            f"Status: [{style}]{summary.status}[/{style}]\n"
            f"Completed this run: {', '.join(summary.completed_stages) or '-'}\n"
            f"{summary.message}\nProject: {layout.root}",
            title="OpenAppForge run",
        )
    )
    if not summary.success:
        raise typer.Exit(3 if summary.status == "awaiting_human" else 1)


@app.command()
def approve(
    project: Path = typer.Argument(Path("."), help="Project path"),
    stage: str | None = typer.Option(None, "--stage"),
) -> None:
    """Approve a guided checkpoint and mark it completed."""
    layout = _layout(project)
    metadata = load_project(layout)
    pipeline = load_pipeline(str(metadata["pipeline"]))
    stage_name = stage or next_stage(layout, pipeline)
    if stage_name is None:
        console.print("Pipeline is already complete.")
        return
    cp = read_checkpoint(layout, stage_name)
    if not cp or cp.get("status") != "awaiting_human":
        raise typer.BadParameter(f"Stage {stage_name!r} is not awaiting approval")
    write_checkpoint(
        layout,
        pipeline=pipeline,
        stage=stage_name,
        status="completed",
        attempt=int(cp.get("attempt", 1)),
        artifacts=cp.get("artifacts") or {},
        gates=cp.get("gates") or [],
        review=cp.get("review") or {},
        driver=cp.get("driver") or {},
        metadata={**(cp.get("metadata") or {}), "human_approved_at": utc_now()},
    )
    console.print(f"Approved [bold]{stage_name}[/bold].")


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address"),
    port: int = typer.Option(8787, "--port", min=1, max=65535),
    open_browser: bool = typer.Option(
        True,
        "--open-browser/--no-open-browser",
        help="Open the AI app builder UI automatically",
    ),
    log_level: str = typer.Option("info", "--log-level"),
) -> None:
    """Launch the single-flow AppForge-LLM v7 web interface."""
    from .web import serve

    console.print(
        Panel.fit(
            f"Open [bold]http://{host}:{port}[/bold] in a browser.\n"
            "Describe an app once, follow the agent pipeline, then preview and download the ZIP.",
            title="AppForge-LLM v7 · web",
        )
    )
    serve(
        host=host,
        port=port,
        open_browser=open_browser,
        log_level=log_level,
    )


@app.command()
def doctor() -> None:
    """Inspect local LLM bridge and tool dependencies."""
    table = Table(title="LLM driver")
    table.add_column("Driver")
    table.add_column("Status")
    table.add_column("Endpoint")
    table.add_row("llm-bridge", "configured externally", DEFAULT_LLM_BRIDGE_URL)
    console.print(table)

    summary = Table(title="Tool support envelope")
    summary.add_column("Capability")
    summary.add_column("Available")
    summary.add_column("Unavailable")
    grouped = ToolRegistry().by_capability()
    for capability, entries in grouped.items():
        available = [item["name"] for item in entries if item["status"] == "available"]
        unavailable = [item["name"] for item in entries if item["status"] != "available"]
        summary.add_row(capability, ", ".join(available) or "-", ", ".join(unavailable) or "-")
    console.print(summary)


@app.command()
def preflight(project: Path = typer.Argument(Path("."), help="Project path")) -> None:
    """Inspect the adopted stack and quality commands before running a stage."""
    layout = _layout(project)
    metadata = load_project(layout)
    stack = detect_stack(layout.root)
    commands = quality_commands(layout.root)
    console.print(Panel.fit(json.dumps({"project": metadata, "stack": stack, "quality_commands": commands}, ensure_ascii=False, indent=2), title="Preflight"))


@auth_app.command("login")
def auth_login(
    provider: str | None = typer.Option(None, "--provider", "-p", help="Provider id to connect (skips interactive selection)"),
    oauth: bool = typer.Option(False, "--oauth", help="Use OAuth login (OpenAI ChatGPT, xAI Grok, GitHub Copilot)"),
    llm_bridge_url: str = typer.Option(DEFAULT_LLM_BRIDGE_URL, "--llm-bridge-url"),
) -> None:
    """Connect an external LLM provider: pick → API key → test → activate model."""
    from . import llm_auth

    if oauth:
        raise typer.Exit(llm_auth.cmd_login_oauth(llm_bridge_url, provider_id=provider))
    raise typer.Exit(llm_auth.cmd_login(llm_bridge_url, provider_id=provider))


@auth_app.command("list")
def auth_list(
    llm_bridge_url: str = typer.Option(DEFAULT_LLM_BRIDGE_URL, "--llm-bridge-url"),
) -> None:
    """List stored credentials and the active model."""
    from . import llm_auth

    raise typer.Exit(llm_auth.cmd_list(llm_bridge_url))


@auth_app.command("logout")
def auth_logout(
    provider: str | None = typer.Argument(None, help="Provider id to remove (skips selection)"),
    llm_bridge_url: str = typer.Option(DEFAULT_LLM_BRIDGE_URL, "--llm-bridge-url"),
) -> None:
    """Remove a stored provider credential."""
    from . import llm_auth

    raise typer.Exit(llm_auth.cmd_logout(llm_bridge_url, provider_id=provider))


@auth_app.command("use")
def auth_use(
    provider: str | None = typer.Argument(None, help="Provider id to activate"),
    model: str | None = typer.Option(None, "--model", "-m", help="Model id to activate"),
    llm_bridge_url: str = typer.Option(DEFAULT_LLM_BRIDGE_URL, "--llm-bridge-url"),
) -> None:
    """Switch the active provider/model used by the pipeline."""
    from . import llm_auth

    raise typer.Exit(llm_auth.cmd_use(llm_bridge_url, provider_id=provider, model_id=model))


@app.command()
def models(
    provider: str | None = typer.Argument(None, help="Provider id to list models for"),
    refresh: bool = typer.Option(False, "--refresh", help="Refresh the models.dev catalog cache"),
    llm_bridge_url: str = typer.Option(DEFAULT_LLM_BRIDGE_URL, "--llm-bridge-url"),
) -> None:
    """List available providers and models from the models.dev catalog."""
    from . import llm_auth

    raise typer.Exit(llm_auth.cmd_models(llm_bridge_url, provider_id=provider, refresh=refresh))


@tool_app.command("list")
def tool_list() -> None:
    """List discovered tools and live status."""
    table = Table("Tool", "Capability", "Status", "Network", "Destructive", "Description")
    for tool in ToolRegistry().all():
        info = tool.info()
        table.add_row(
            tool.name,
            tool.capability,
            info["status"],
            "yes" if tool.network_required else "no",
            "yes" if tool.destructive else "no",
            tool.description,
        )
    console.print(table)


@tool_app.command("info")
def tool_info(name: str = typer.Argument(...)) -> None:
    """Show a tool contract."""
    try:
        info = ToolRegistry().get(name).info()
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print_json(data=info)


@tool_app.command("run")
def tool_run(
    name: str = typer.Argument(...),
    project: Path = typer.Option(Path("."), "--project", "-p"),
    input_value: str | None = typer.Option(None, "--input", help="JSON object"),
    input_file: Path | None = typer.Option(None, "--input-file"),
) -> None:
    """Execute one tool against an AppForge workspace."""
    layout = _layout(project)
    inputs = _json_input(input_value, input_file)
    try:
        result = ToolRegistry().get(name).run(layout.root, inputs)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print_json(data=result.to_dict())
    if not result.success:
        raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
