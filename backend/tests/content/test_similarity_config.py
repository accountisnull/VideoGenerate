"""配置必须可复现，索引连接模式不能含糊。"""

import pytest

from app.content.config import SimilarityConfig
from app.content.errors import SimilarityError


def values():
    return {"SIMILARITY_MODEL_PATH": "models/bge-m3",
            "SIMILARITY_MODEL_REVISION": "local-sha256:" + "a" * 64,
            "SIMILARITY_QDRANT_PATH": "data/vector"}


def test_defaults_and_paths_resolved_against_project(tmp_path):
    config = SimilarityConfig.from_values(values(), tmp_path)
    assert config.model_path == tmp_path / "models/bge-m3"
    assert config.qdrant_path == tmp_path / "data/vector"
    assert config.profile.dimension == 1024
    assert config.embedding_timeout_seconds == 120


@pytest.mark.parametrize("changes", [
    {"SIMILARITY_MODEL_PATH": ""}, {"SIMILARITY_MODEL_REVISION": "master"},
    {"SIMILARITY_EMBEDDING_TIMEOUT_SECONDS": "nan"},
    {"SIMILARITY_QDRANT_URL": "http://127.0.0.1:6333"},
    {"SIMILARITY_QDRANT_PATH": "", "SIMILARITY_QDRANT_URL": ""},
    {"SIMILARITY_REQUIRED_CHECKS": "similarity"},
])
def test_invalid_configuration_is_explicit_error(tmp_path, changes):
    with pytest.raises(SimilarityError) as error:
        SimilarityConfig.from_values(values() | changes, tmp_path)
    assert error.value.code == "INVALID_CONFIG"


def test_server_mode_and_additional_required_checks(tmp_path):
    config = SimilarityConfig.from_values(values() | {
        "SIMILARITY_QDRANT_PATH": "", "SIMILARITY_QDRANT_URL": "http://127.0.0.1:6333",
        "SIMILARITY_REQUIRED_CHECKS": "keywords,content_audit,similarity,fact_review",
    }, tmp_path)
    assert config.qdrant_path is None
    assert "fact_review" in config.required_checks
