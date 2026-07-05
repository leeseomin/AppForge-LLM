from __future__ import annotations

from appforge.pipelines import load_pipeline
from appforge.projects import initialize_project, load_project
from appforge.prompting import build_stage_prompt


def test_stage_packet_contains_contract_context_and_safety(tmp_path) -> None:
    layout = initialize_project(
        "Build an accessible React web app with login",
        projects_dir=tmp_path,
        name="packet",
        pipeline_name="web-app",
    )
    project = load_project(layout)
    pipeline = load_pipeline("web-app")
    packet = build_stage_prompt(
        layout,
        project=project,
        pipeline=pipeline,
        stage=pipeline.stage("intake"),
        attempt=1,
    )
    assert "product_brief" in packet
    assert "stage-result.json" in packet
    assert "External actions" in packet or "external" in packet.casefold()
    assert "Build an accessible React web app with login" in packet
