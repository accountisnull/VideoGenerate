"""模型进程边界：真实超时语义、输出验证及本地文件指纹。"""

import json
import subprocess
from pathlib import Path

import pytest

from app.content.errors import SimilarityError
from app.content.providers.embedding import (
    MODEL_FILES,
    LocalEmbedding,
    model_fingerprint,
)
from app.content.similarity_models import IndexProfile


@pytest.fixture
def profile():
    return IndexProfile(model_id="BAAI/bge-m3", model_revision="local-sha256:" + "a" * 64,
                        dimension=1024, index_version="v1")


@pytest.mark.parametrize("reply, code", [
    ({"vector": [1.0]}, "INDEX_INCOMPATIBLE"),
    ({"vector": [0.0] * 1024}, "EMBEDDING_FAILED"),
    ({"error": {"code": "INPUT_TOO_LONG", "message": "输入超过模型限制"}}, "INPUT_TOO_LONG"),
    ({"error": {"code": "INDEX_INCOMPATIBLE", "message": "模型文件已变更"}}, "INDEX_INCOMPATIBLE"),
])
def test_worker_errors_never_become_valid_vector(monkeypatch, tmp_path, profile, reply, code):
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, json.dumps(reply), "")
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(SimilarityError) as error:
        LocalEmbedding(tmp_path, profile, 2).embed("稿件")
    assert error.value.code == code


def test_timeout_and_crashed_worker_are_errors(monkeypatch, tmp_path, profile):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 2
        raise subprocess.TimeoutExpired(args[0], 2)
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(SimilarityError) as error:
        LocalEmbedding(tmp_path, profile, 2).embed("稿件")
    assert error.value.code == "EMBEDDING_TIMEOUT"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "oops"))
    with pytest.raises(SimilarityError) as error:
        LocalEmbedding(tmp_path, profile, 2).embed("稿件")
    assert error.value.code == "EMBEDDING_FAILED"


def test_valid_worker_vector_and_request_metadata(monkeypatch, tmp_path, profile):
    def run(*args, **kwargs):
        req = json.loads(kwargs["input"])
        assert req["script"] == " 原文不裁剪 "
        assert req["profile"]["model_revision"] == profile.model_revision
        return subprocess.CompletedProcess(args[0], 0, json.dumps({"vector": [1.0] + [0.0] * 1023}), "")
    monkeypatch.setattr(subprocess, "run", run)
    assert len(LocalEmbedding(tmp_path, profile, 2).embed(" 原文不裁剪 ")) == 1024


def test_fingerprint_detects_weight_and_tokenizer_changes(tmp_path):
    for filename in MODEL_FILES:
        file = tmp_path / filename
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("fixture", encoding="utf-8")
    original = model_fingerprint(tmp_path)
    (tmp_path / "tokenizer.json").write_text("changed", encoding="utf-8")
    assert model_fingerprint(tmp_path) != original
    (tmp_path / "pytorch_model.bin").unlink()
    with pytest.raises(SimilarityError) as error:
        model_fingerprint(tmp_path)
    assert error.value.code == "EMBEDDING_FAILED"


def test_missing_model_directory_fails_before_process(profile):
    with pytest.raises(SimilarityError):
        LocalEmbedding(Path("nonexistent-model-directory"), profile, 2).embed("稿件")


def test_untracked_alternative_weights_rejected(tmp_path):
    for filename in MODEL_FILES:
        file = tmp_path / filename
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("fixture", encoding="utf-8")
    (tmp_path / "model.safetensors").write_text("alternative", encoding="utf-8")
    with pytest.raises(SimilarityError) as error:
        model_fingerprint(tmp_path)
    assert error.value.code == "INDEX_INCOMPATIBLE"
