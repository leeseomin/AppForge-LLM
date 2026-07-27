from __future__ import annotations

import pytest

from appforge.drivers import DriverError, _extract_json_object


class TestExtractJsonObject:
    def test_pure_json(self):
        text = '{"stage": "verification", "passed": true}'
        result = _extract_json_object(text)
        assert result["stage"] == "verification"

    def test_json_with_leading_explanation(self):
        text = 'Here is the verification result:\n{"stage": "verification", "passed": true, "files": [{"path": "test.js", "content": "assert(1)"}]}'
        result = _extract_json_object(text)
        assert result["stage"] == "verification"
        assert len(result["files"]) == 1

    def test_json_with_trailing_explanation(self):
        text = '{"stage": "verification", "passed": true}\nThat completes the verification stage.'
        result = _extract_json_object(text)
        assert result["stage"] == "verification"

    def test_fenced_json_block(self):
        text = 'Here is the result:\n```json\n{"stage": "verification", "passed": true}\n```\nDone.'
        result = _extract_json_object(text)
        assert result["stage"] == "verification"

    def test_fenced_json_with_nested_braces(self):
        text = 'Result:\n```json\n{"stage": "verification", "gates": {"run_tests": {"passed": true}, "run_build": {"passed": true}}}\n```'
        result = _extract_json_object(text)
        assert result["gates"]["run_tests"]["passed"] is True

    def test_nested_json_with_explanation_containing_braces(self):
        text = (
            'The verification produced the following envelope.\n'
            'Note: the {run_tests} gate is required.\n'
            '{"stage": "verification", "gates": {"run_tests": {"passed": true, "skipped": false}, "run_build": {"passed": true}}, "files": [{"path": "test.js", "content": "function test() { assert(true); }"}]}'
        )
        result = _extract_json_object(text)
        assert result["stage"] == "verification"
        assert result["gates"]["run_tests"]["passed"] is True
        assert result["files"][0]["path"] == "test.js"

    def test_deeply_nested_json(self):
        text = (
            'Here is the result.\n'
            '{"a": {"b": {"c": {"d": {"e": {"f": "deep"}}}}}}'
        )
        result = _extract_json_object(text)
        assert result["a"]["b"]["c"]["d"]["e"]["f"] == "deep"

    def test_json_with_string_containing_braces(self):
        text = '{"stage": "verification", "code": "function test() { return {a: 1}; }", "passed": true}'
        result = _extract_json_object(text)
        assert result["stage"] == "verification"
        assert "function test()" in result["code"]

    def test_empty_response_raises(self):
        with pytest.raises(DriverError, match="empty response"):
            _extract_json_object("   ")

    def test_no_json_raises(self):
        with pytest.raises(DriverError, match="did not contain a JSON object"):
            _extract_json_object("No JSON here at all")

    def test_array_not_object_raises(self):
        with pytest.raises(DriverError, match="did not contain a JSON object"):
            _extract_json_object('[1, 2, 3]')

    def test_extra_data_after_json(self):
        text = '{"stage": "verification"} extra trailing content {not json}'
        result = _extract_json_object(text)
        assert result["stage"] == "verification"

    def test_multiline_fenced_json(self):
        text = (
            'Result:\n'
            '```json\n'
            '{\n'
            '  "stage": "verification",\n'
            '  "passed": true,\n'
            '  "files": [\n'
            '    {"path": "test.js", "content": "assert(true)"}\n'
            '  ]\n'
            '}\n'
            '```'
        )
        result = _extract_json_object(text)
        assert result["stage"] == "verification"
        assert result["files"][0]["path"] == "test.js"
