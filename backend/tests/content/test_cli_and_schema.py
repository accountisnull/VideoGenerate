import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.content.errors import ContentError
from app.content.generation import __main__ as cli
from app.content.generation.models import (
    Draft,
    GenerationRequest,
    GenerationResult,
    RevisionRequest,
)


def test_examples_are_valid_requests():
    root = Path(__file__).resolve().parents[3] / "docs" / "content-generation"
    generation = GenerationRequest.model_validate_json((root / "generate.example.json").read_text("utf-8"))
    revision = RevisionRequest.model_validate_json((root / "revise.example.json").read_text("utf-8"))
    assert generation.topic == revision.topic
    assert revision.previous.draft.script


def test_schema_cli_outputs_contracts_without_model_call(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["generation", "schema"])
    assert cli.main() == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["GenerationRequest"]["required"] == ["topic"]
    assert "previous" in schema["RevisionRequest"]["required"]
    result = schema["GenerationResult"]
    assert result["properties"]["char_count"]["readOnly"] is True
    assert "review_passed" not in result["properties"]


def test_cli_success_is_unreviewed_and_revision_dispatches(monkeypatch, capsys, tmp_path, payload, stub_model):
    from app.content.generation.models import SourceVersion

    request = RevisionRequest(topic="主题", previous=SourceVersion(version_id=uuid4(), draft=Draft(**payload)),
                              revision_request="简化开头")
    input_path = tmp_path / "revision.json"
    input_path.write_text(request.model_dump_json(), encoding="utf-8")
    calls = []

    class Service:
        async def revise(self, parsed):
            calls.append(parsed)
            reply = await stub_model.generate(step="revision")
            return GenerationResult(version_id=uuid4(), source_version_id=parsed.previous.version_id,
                                    draft=Draft(**payload), previous=parsed.previous, model_call=reply.call)

    monkeypatch.setattr(cli, "ContentGenerator", Service)
    monkeypatch.setattr("sys.argv", ["generation", "revise", str(input_path)])
    assert cli.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["success"] is True and len(calls) == 1
    assert output["data"]["source_version_id"] == str(request.previous.version_id)
    assert "review_passed" not in output["data"]


def test_cli_invalid_input_is_sanitized(monkeypatch, capsys, tmp_path):
    input_path = tmp_path / "invalid.json"
    input_path.write_text('{"unexpected": "private-test-key"}', encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["generation", "generate", str(input_path)])
    assert cli.main() == 1
    output = capsys.readouterr().out
    assert json.loads(output)["error"]["code"] == "INPUT_INVALID"
    assert "private-test-key" not in output


def test_cli_model_error_is_structured(monkeypatch, capsys, tmp_path):
    input_path = tmp_path / "input.json"
    input_path.write_text('{"topic":"topic"}', encoding="utf-8")

    class Service:
        async def generate(self, request):
            raise ContentError("MODEL_CONFIGURATION", "模型配置缺失")

    monkeypatch.setattr(cli, "ContentGenerator", Service)
    monkeypatch.setattr("sys.argv", ["generation", "generate", str(input_path)])
    assert cli.main() == 1
    output = json.loads(capsys.readouterr().out)
    assert output["success"] is False and output["data"] is None
    assert output["error"]["model_call"] is None


@pytest.mark.parametrize("ready", [True, False])
def test_check_config_only_reports_presence(monkeypatch, capsys, ready):
    from app import runtime

    def configuration(step):
        if not ready:
            raise ValueError("private-test-key")
        return {"api_key": "private-test-key"}

    monkeypatch.setattr(runtime, "text_model_configuration", configuration)
    monkeypatch.setattr("sys.argv", ["generation", "check-config"])
    assert cli.main() == (0 if ready else 1)
    output = capsys.readouterr().out
    assert "private-test-key" not in output
    assert len(json.loads(output)) == 3
