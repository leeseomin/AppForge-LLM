import json

import pytest

from appforge.evidence import execution_context, record_execution
from appforge.models import ToolResult
from appforge.snapshots import digest, source_manifest
from appforge.tooling.test_results import reporter_command, test_summary as summarize


def test_junit_preserves_failure_skip_counts_and_ids(tmp_path):
    report = tmp_path / "results.xml"
    report.write_text('<testsuites><testsuite><testcase classname="flow" name="ok"/><testcase classname="flow" name="broken"><failure/></testcase><testcase classname="flow" name="later"><skipped/></testcase></testsuite></testsuites>')
    result = summarize("junit", report, "")
    assert (result["collected"], result["executed"], result["passed"], result["failed"], result["skipped"]) == (3, 2, 1, 1, 1)
    assert result["failed_test_ids"] == ["flow::broken"]


@pytest.mark.parametrize("results", [[], [{"name": "suite", "assertionResults": [{"fullName": "skip", "status": "pending"}]}]])
def test_empty_and_all_skipped_json_do_not_claim_executed_tests(tmp_path, results):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"testResults": results}))
    assert summarize("json", report, "")["executed"] == 0


def test_junit_rejects_duplicate_case_ids_and_entities(tmp_path):
    report = tmp_path / "results.xml"
    report.write_text('<testsuite><testcase name="same"/><testcase name="same"/></testsuite>')
    with pytest.raises(ValueError, match="Duplicate"):
        summarize("junit", report, "")
    report.write_text('<!DOCTYPE results [<!ENTITY unsafe "x">]><testsuite/>')
    with pytest.raises(ValueError, match="DTD/entity"):
        summarize("junit", report, "")


def test_node_reporter_keeps_package_hooks_and_arguments(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"pretest": "node prepare.cjs", "test": "node --test"}}))
    command = ["npm", "test", "--", "test/smoke.cjs"]
    assert reporter_command(tmp_path, command)[0] == command


@pytest.mark.parametrize("second_xml", [
    '<testsuite><testcase name="one"/></testsuite>',
    '<testsuite><testcase name="one"/><testcase name="two"><skipped/></testcase></testsuite>',
])
def test_deleting_or_skipping_previous_tests_is_not_a_repair(tmp_path, second_xml):
    report = tmp_path.parent / (tmp_path.name + ".xml")
    report.write_text('<testsuite><testcase name="one"/><testcase name="two"><failure/></testcase></testsuite>')
    before = digest(source_manifest(tmp_path))
    first = ToolResult(False, data={"tests": summarize("junit", report, "")})
    record_execution(tmp_path, "run_tests", before, first)
    report.write_text(second_xml)
    second = ToolResult(True, data={"tests": summarize("junit", report, "")})
    with execution_context(frozen_workspace=str(tmp_path.resolve())):
        record_execution(tmp_path, "run_tests", before, second)
    assert not second.success
    assert second.data["code"] == "TEST_COVERAGE_REDUCED"


def test_focused_repair_check_is_allowed_but_cannot_replace_final_suite(tmp_path):
    before = digest(source_manifest(tmp_path))
    full = {"test_ids": ["one", "two"], "executed_test_ids": ["one", "two"]}
    subset = {"test_ids": ["one"], "executed_test_ids": ["one"]}
    record_execution(tmp_path, "run_tests", before, ToolResult(False, data={"tests": full}))
    focused = ToolResult(True, data={"tests": subset})
    record_execution(tmp_path, "run_tests", before, focused)
    assert focused.success
    with execution_context(frozen_workspace=str(tmp_path.resolve())):
        final = ToolResult(True, data={"tests": subset})
        record_execution(tmp_path, "run_tests", before, final)
    assert not final.success
