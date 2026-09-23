"""真实临时 Qdrant 验证索引兼容性、过滤、持久化及未知写入结果。"""

import socket
from contextlib import closing
from uuid import uuid4

import pytest
from qdrant_client import QdrantClient, models

from app.content.errors import SimilarityError
from app.content.similarity_models import (
    Approval,
    IndexProfile,
    StoredRecord,
    script_digest,
)
from app.content.storage.vector_index import QdrantIndex


@pytest.fixture
def profile():
    return IndexProfile(model_id="test", model_revision="rev1", dimension=2, index_version="v1")


@pytest.fixture
def index(tmp_path, profile):
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    index = QdrantIndex(client, "test", profile)
    index.initialize()
    yield index
    client.close()


def record(profile, **updates):
    job, ver = uuid4(), uuid4()
    data = {"script": "历史合格稿", "job_id": job, "content_version_id": ver,
                "script_sha256": script_digest("历史合格稿"), "profile": profile,
                "approval": Approval(approval_id=uuid4(), job_id=job, content_version_id=ver,
                                  script_sha256=script_digest("历史合格稿"),
                                  checks={"keywords": True, "content_audit": True, "similarity": True})}
    data.update(updates)
    return StoredRecord(**data)


def test_missing_collection_is_not_valid_empty(profile):
    with closing(QdrantClient(location=":memory:")) as client:
        index = QdrantIndex(client, "missing", profile)
        with pytest.raises(SimilarityError) as error:
            index.search([1, 0], uuid4(), 10)
        assert error.value.code == "INDEX_NOT_READY"
        index.initialize()
        assert index.search([1, 0], uuid4(), 10) == []


@pytest.mark.parametrize("updates", [
    {"model_id": "other"}, {"model_revision": "rev2"}, {"dimension": 3},
    {"index_version": "v2"},
])
def test_existing_collection_never_silently_recreated(index, profile, updates):
    index.upsert(record(profile), [1, 0])
    incompatible = QdrantIndex(index.client, "test", profile.model_copy(update=updates))
    with pytest.raises(SimilarityError) as error:
        incompatible.initialize()
    assert error.value.code == "INDEX_INCOMPATIBLE"
    assert index.client.count("test").count == 1


def test_collection_dimension_and_distance_checked_independently(profile):
    with closing(QdrantClient(location=":memory:")) as client:
        client.create_collection("bad", vectors_config=models.VectorParams(size=3, distance="Dot"),
                                 metadata={"similarity_profile": profile.model_dump()})
        with pytest.raises(SimilarityError) as error:
            QdrantIndex(client, "bad", profile).validate()
        assert error.value.code == "INDEX_INCOMPATIBLE"


def test_filter_before_top_ten_and_keep_older_job_versions(index, profile):
    current = record(profile)
    index.upsert(current, [1, 0])
    versions = []
    for _ in range(10):
        old = record(profile)
        versions.append(old.content_version_id)
        index.upsert(old, [0.8, 0.6])
    matches = index.search([1, 0], current.content_version_id, 10)
    assert len(matches) == 10
    assert {m.content_version_id for m in matches} == set(versions)
    assert matches[0].score == pytest.approx(0.8)


def test_idempotent_write_and_disk_reopen(tmp_path, profile):
    path = str(tmp_path / "db")
    item = record(profile)
    with closing(QdrantClient(path=path)) as client:
        index = QdrantIndex(client, "test", profile)
        index.initialize()
        index.upsert(item, [1, 0])
        index.upsert(item, [1, 0])
        assert client.count("test").count == 1
    with closing(QdrantClient(path=path)) as client:
        index = QdrantIndex(client, "test", profile)
        assert index.lookup(item.content_version_id) == item


def test_lost_write_response_can_be_reconciled(index, profile, monkeypatch):
    item = record(profile)
    original = index.client.upsert

    def lost_response(*args, **kwargs):
        original(*args, **kwargs)
        raise TimeoutError("response lost")

    monkeypatch.setattr(index.client, "upsert", lost_response)
    with pytest.raises(SimilarityError) as error:
        index.upsert(item, [1, 0])
    assert error.value.code == "VECTOR_WRITE_UNCONFIRMED"
    assert index.lookup(item.content_version_id) == item
    monkeypatch.setattr(index.client, "upsert", original)
    index.upsert(item, [1, 0])
    assert index.client.count("test").count == 1


def test_unreachable_database_never_passes_as_empty(profile):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        port = blocker.getsockname()[1]
        with closing(QdrantClient(url=f"http://127.0.0.1:{port}", timeout=1,
                          check_compatibility=False, trust_env=False)) as client:
            with pytest.raises(SimilarityError) as error:
                QdrantIndex(client, "test", profile).search([1, 0], uuid4(), 10)
            assert error.value.code == "VECTOR_UNAVAILABLE"


def test_corrupted_payload_and_conflict_are_errors(index, profile):
    item = record(profile)
    index.upsert(item, [1, 0])
    with pytest.raises(SimilarityError) as error:
        index.upsert(item.model_copy(update={"script": "另一稿", "script_sha256": script_digest("另一稿")}), [1, 0])
    assert error.value.code == "CONTENT_VERSION_CONFLICT"
    index.client.set_payload("test", payload={"profile": {}}, points=[str(item.content_version_id)])
    with pytest.raises(SimilarityError) as error:
        index.search([1, 0], uuid4(), 10)
    assert error.value.code == "INDEX_INCOMPATIBLE"


def test_missing_approval_checks_in_history_rejected(index, profile):
    item = record(profile)
    index.upsert(item, [1, 0])
    incomplete = item.approval.model_copy(update={"checks": {"similarity": True}})
    index.client.set_payload("test", payload={"approval": incomplete.model_dump(mode="json")},
                             points=[str(item.content_version_id)])
    with pytest.raises(SimilarityError) as error:
        index.lookup(item.content_version_id)
    assert error.value.code == "INDEX_INCOMPATIBLE"
