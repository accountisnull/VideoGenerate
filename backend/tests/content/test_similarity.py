"""防止阈值方向错误、审核漏项、版本重放及失败伪装成功。"""

import hashlib
from uuid import uuid4

import pytest

from app.content.errors import SimilarityError
from app.content.models import (
    Approval,
    DetectRequest,
    IndexProfile,
    Match,
    StoredRecord,
    StoreRequest,
)
from app.content.review.similarity import SimilarityService


class Embedding:
    def embed(self, script):
        return [1.0, 0.0]


class Index:
    def __init__(self, matches=()):
        self.matches = list(matches)
        self.records = {}

    def validate(self):
        pass

    def search(self, vector, exclude_version, limit):
        assert limit == 10
        return [m for m in self.matches if m.content_version_id != exclude_version][:limit]

    def lookup(self, version):
        return self.records.get(version)

    def upsert(self, record, vector):
        self.records[record.content_version_id] = record


@pytest.fixture
def profile():
    return IndexProfile(model_id="test-model", model_revision="rev1", dimension=2,
                        index_version="test-v1")


def request(script="合格的测试稿件"):
    return DetectRequest(script=script, job_id=uuid4(), content_version_id=uuid4())


def approved(req, **updates):
    data = {"approval_id": uuid4(), "job_id": req.job_id,
                "content_version_id": req.content_version_id,
                "script_sha256": hashlib.sha256(req.script.encode()).hexdigest(),
                "checks": {"keywords": True, "content_audit": True, "similarity": True}}
    data.update(updates)
    return StoreRequest(**req.model_dump(), approval=Approval(**data))


@pytest.mark.parametrize("score, passed", [(0.8499, True), (0.85, False), (0.8501, False)])
def test_threshold_and_feedback(profile, score, passed):
    hit = Match(content_version_id=uuid4(), job_id=uuid4(), score=score,
                script_excerpt="这段历史稿可以帮助改写")
    result = SimilarityService(Embedding(), Index([hit]), profile).detect(request())
    assert result.passed is passed
    assert result.max_similarity == score
    assert result.matches[0].script_excerpt == "这段历史稿可以帮助改写"
    assert result.profile == profile


def test_empty_history_has_no_measured_score(profile):
    result = SimilarityService(Embedding(), Index(), profile).detect(request())
    assert result.passed and result.max_similarity is None
    assert result.reason == "NO_HISTORY_MATCH"


def test_embedding_failure_is_not_empty_pass(profile):
    class Failed:
        def embed(self, script):
            raise SimilarityError("EMBEDDING_FAILED", "向量化失败")
    with pytest.raises(SimilarityError, match="向量化失败"):
        SimilarityService(Failed(), Index(), profile).detect(request())


@pytest.mark.parametrize("vector", [[1.0], [float('nan'), 0], [0.0, 0.0]])
def test_invalid_vector_fails(profile, vector):
    class Broken:
        def embed(self, script):
            return vector
    with pytest.raises(SimilarityError):
        SimilarityService(Broken(), Index(), profile).detect(request())


def test_store_and_reconcile_are_idempotent(profile):
    index = Index()
    service = SimilarityService(Embedding(), index, profile)
    req = request()
    assert service.reconcile(req).saved is False
    first = service.store(approved(req))
    second = service.store(approved(req))
    assert first.saved and second.saved
    assert first.point_id == second.point_id
    assert len(index.records) == 1
    assert service.reconcile(req).saved


@pytest.mark.parametrize("updates", [
    {"checks": {"similarity": True}},
    {"checks": {"keywords": True, "content_audit": False, "similarity": True}},
    {"script_sha256": "0" * 64}, {"content_version_id": uuid4()}, {"job_id": uuid4()},
])
def test_unapproved_or_mismatched_script_never_stored(profile, updates):
    index = Index()
    with pytest.raises(SimilarityError) as error:
        SimilarityService(Embedding(), index, profile).store(approved(request(), **updates))
    assert error.value.code == "APPROVAL_REQUIRED"
    assert not index.records


def test_same_version_different_script_conflicts(profile):
    service = SimilarityService(Embedding(), Index(), profile)
    req = request()
    service.store(approved(req))
    changed = req.model_copy(update={"script": "修改后的另一段正文"})
    for action in (lambda: service.store(approved(changed)), lambda: service.detect(changed),
                   lambda: service.reconcile(changed)):
        with pytest.raises(SimilarityError) as error:
            action()
        assert error.value.code == "CONTENT_VERSION_CONFLICT"


def test_current_version_excluded_but_older_version_compared(profile):
    req = request()
    old = Match(content_version_id=uuid4(), job_id=req.job_id, score=0.95, script_excerpt="旧版")
    current = Match(content_version_id=req.content_version_id, job_id=req.job_id,
                    score=1, script_excerpt=req.script)
    result = SimilarityService(Embedding(), Index([current, old]), profile).detect(req)
    assert result.matches == [old]
    assert not result.passed


def test_store_failure_is_not_success(profile):
    class BrokenIndex(Index):
        def upsert(self, record: StoredRecord, vector):
            raise SimilarityError("VECTOR_WRITE_UNCONFIRMED", "无法确认保存", retryable=True)
    with pytest.raises(SimilarityError) as error:
        SimilarityService(Embedding(), BrokenIndex(), profile).store(approved(request()))
    assert error.value.code == "VECTOR_WRITE_UNCONFIRMED"
