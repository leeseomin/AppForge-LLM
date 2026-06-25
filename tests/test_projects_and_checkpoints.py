from __future__ import annotations

from appforge.checkpoints import next_stage, read_checkpoint, write_checkpoint
from appforge.pipelines import load_pipeline
from appforge.projects import initialize_project, load_project


def test_project_initialization_and_checkpoint_resume(tmp_path) -> None:
    layout = initialize_project(
        "Build a small accessible web app",
        projects_dir=tmp_path,
        name="sample",
        pipeline_name="web-app",
        mode="autonomous",
    )
    metadata = load_project(layout)
    pipeline = load_pipeline(metadata["pipeline"])
    assert metadata["pipeline"] == "web-app"
    assert next_stage(layout, pipeline) == "intake"
    assert (layout.control / "request.md").is_file()

    write_checkpoint(
        layout,
        pipeline=pipeline,
        stage="intake",
        status="completed",
        attempt=1,
        artifacts={"product_brief": ".appforge/artifacts/product_brief.json"},
    )
    assert read_checkpoint(layout, "intake")["status"] == "completed"
    assert next_stage(layout, pipeline) == "specification"


def test_existing_repository_routes_to_feature(tmp_path) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    (target / "README.md").write_text("existing\n", encoding="utf-8")
    layout = initialize_project(
        "Add a sortable history table",
        projects_dir=tmp_path,
        existing_target=target,
    )
    assert load_project(layout)["pipeline"] == "feature"
