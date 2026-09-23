"""模型外壳控制 token 上限与加载格式，不在普通测试加载真实权重。"""

import sys
from types import SimpleNamespace

import pytest

from app.content.errors import SimilarityError
from app.content.providers import embedding_worker
from app.content.similarity_models import IndexProfile


def test_worker_rejects_long_text_before_encode(monkeypatch, tmp_path):
    revision = "local-sha256:" + "a" * 64
    profile = IndexProfile(model_id="BAAI/bge-m3", model_revision=revision,
                           dimension=1024, index_version="v1")
    monkeypatch.setattr(embedding_worker, "model_fingerprint", lambda path: revision)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=lambda count: None))

    class Model:
        max_seq_length = 3

        def __init__(self, *args, **kwargs):
            assert kwargs["local_files_only"] is True
            assert kwargs["model_kwargs"]["use_safetensors"] is False

        def tokenizer(self, script, truncation):
            assert truncation is False
            return {"input_ids": [1, 2, 3, 4]}

        def encode(self, *args, **kwargs):
            pytest.fail("超长输入不能被静默截断后向量化")

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=Model))
    with pytest.raises(SimilarityError) as error:
        embedding_worker.infer({"profile": profile.model_dump(), "model_path": str(tmp_path), "script": "长文本"})
    assert error.value.code == "INPUT_TOO_LONG"
