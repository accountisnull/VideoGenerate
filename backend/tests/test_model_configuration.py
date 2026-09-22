import pytest

from app import runtime


@pytest.fixture
def model_env(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    for name in tuple(runtime.os.environ):
        if name.startswith("TEXT_") or name in ("DASHSCOPE_API_KEY", "REVIEW_API_KEY"):
            monkeypatch.delenv(name)
    lines = ["DASHSCOPE_API_KEY=shared-test-key", "REVIEW_API_KEY=review-test-key"]
    for step in ("WRITING", "REVIEW", "REVISION"):
        lines.extend([
            f"TEXT_{step}_BASE_URL=https://example.com/v1",
            f"TEXT_{step}_MODEL={step.lower()}-model",
            f"TEXT_{step}_GENERATION_OPTIONS='{{\"temperature\": 0.3}}'",
        ])
    lines.append("TEXT_REVIEW_API_KEY_ENV=REVIEW_API_KEY")
    path = tmp_path / ".env"
    path.write_text("\n".join(lines), encoding="utf-8-sig")
    return path


def test_separate_models_keys_and_environment_overrides(model_env, monkeypatch):
    monkeypatch.setenv("TEXT_WRITING_MODEL", "overridden-model")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "environment-test-key")
    writing = runtime.text_model_configuration("writing")
    review = runtime.text_model_configuration("review")
    revision = runtime.text_model_configuration("revision")
    assert writing["model"] == "overridden-model"
    assert writing["api_key"].get_secret_value() == "environment-test-key"
    assert "environment-test-key" not in repr(writing)
    assert review["model"] == "review-model"
    assert review["api_key"].get_secret_value() == "review-test-key"
    assert revision["model"] == "revision-model"
    assert revision["timeout_seconds"] == 60
    assert revision["generation_options"] == {"temperature": 0.3}


@pytest.mark.parametrize(("name", "value"), [
    ("TEXT_WRITING_MODEL", ""),
    ("TEXT_WRITING_BASE_URL", ""),
    ("DASHSCOPE_API_KEY", ""),
    ("TEXT_WRITING_TIMEOUT_SECONDS", "0"),
    ("TEXT_WRITING_TIMEOUT_SECONDS", "nan"),
    ("TEXT_WRITING_TIMEOUT_SECONDS", "abc"),
    ("TEXT_WRITING_GENERATION_OPTIONS", "[]"),
    ("TEXT_WRITING_GENERATION_OPTIONS", "invalid-json"),
])
def test_invalid_settings_report_variable_name(model_env, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        runtime.text_model_configuration("writing")


def test_empty_model_configuration_does_not_block_media(model_env):
    model_env.write_text("TEXT_WRITING_MODEL=\nDASHSCOPE_API_KEY=\n", encoding="utf-8")
    assert runtime.configuration()["VIDEO_DATA_DIR"] == "data"
    with pytest.raises(ValueError, match="TEXT_WRITING_BASE_URL"):
        runtime.text_model_configuration("writing")
    with pytest.raises(ValueError, match="Unknown text model step"):
        runtime.text_model_configuration("unknown")
