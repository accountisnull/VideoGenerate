"""同质化检测与审核后入库规则，不依赖 MCP 传输。"""

import math
from typing import Protocol
from uuid import UUID

from app.content.async_tools import bounded_thread
from app.content.errors import SimilarityError
from app.content.similarity_models import (
    DetectionResult,
    DetectRequest,
    IndexProfile,
    Match,
    ReconcileResult,
    StoredRecord,
    StoreRequest,
    StoreResult,
    script_digest,
)

REQUIRED_CHECKS = frozenset({"keywords", "content_audit", "similarity"})


class EmbeddingProvider(Protocol):
    def embed(self, script: str) -> list[float]: ...


class VectorIndex(Protocol):
    def validate(self) -> None: ...
    def search(self, vector: list[float], exclude_version: UUID, limit: int) -> list[Match]: ...
    def lookup(self, version: UUID) -> StoredRecord | None: ...
    def upsert(self, record: StoredRecord, vector: list[float]) -> None: ...


def validated_vector(vector: list[float], dimension: int) -> list[float]:
    if len(vector) != dimension:
        raise SimilarityError("INDEX_INCOMPATIBLE", "向量维度与索引不符")
    if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x)
           for x in vector):
        raise SimilarityError("EMBEDDING_FAILED", "向量含非有限值或非法元素")
    norm = math.sqrt(sum(x * x for x in vector))
    if not math.isfinite(norm) or abs(norm - 1.0) > 0.001:
        raise SimilarityError("EMBEDDING_FAILED", "向量必须经过 L2 归一化且非零")
    return [float(x) for x in vector]


class SimilarityService:
    def __init__(self, embedding: EmbeddingProvider, index: VectorIndex,
                 profile: IndexProfile, required_checks: frozenset[str] = REQUIRED_CHECKS):
        if not REQUIRED_CHECKS <= required_checks:
            raise SimilarityError("INVALID_CONFIG", "必需检查不能移除基础三项")
        self.embedding = embedding
        self.index = index
        self.profile = profile
        self.required_checks = required_checks

    async def aembed(self, script: str) -> list[float]:
        if not isinstance(script, str) or not script.strip():
            raise SimilarityError("INVALID_INPUT", "口播稿不能为空")
        if hasattr(self.embedding, "aembed"):
            vector = await self.embedding.aembed(script)
        else:
            vector = await bounded_thread(lambda: self.embedding.embed(script))
        return validated_vector(vector, self.profile.dimension)

    async def adetect(self, request: DetectRequest) -> DetectionResult:
        await bounded_thread(lambda: self._existing(request))
        vector = await self.aembed(request.script)
        matches = await bounded_thread(
            lambda: self.index.search(vector, request.content_version_id, 10)
        )
        matches = sorted(matches, key=lambda item: item.score, reverse=True)
        score = matches[0].score if matches else None
        passed = score is None or score < 0.85
        return DetectionResult(
            passed=passed,
            reason="NO_HISTORY_MATCH" if score is None else (
                "BELOW_THRESHOLD" if passed else "SIMILARITY_BLOCKED"),
            max_similarity=score, matches=matches, profile=self.profile,
        )

    async def astore(self, request: StoreRequest) -> StoreResult:
        self._check_approval(request)
        if await bounded_thread(lambda: self._existing(request)) is None:
            vector = await self.aembed(request.script)
            record = StoredRecord(**request.model_dump(), profile=self.profile,
                                  script_sha256=script_digest(request.script))
            await bounded_thread(lambda: self.index.upsert(record, vector))
        return StoreResult(saved=True, point_id=request.content_version_id,
                           content_version_id=request.content_version_id,
                           index_version=self.profile.index_version)

    def embed(self, script: str) -> list[float]:
        if not isinstance(script, str) or not script.strip():
            raise SimilarityError("INVALID_INPUT", "口播稿不能为空")
        return validated_vector(self.embedding.embed(script), self.profile.dimension)

    def _existing(self, request: DetectRequest) -> StoredRecord | None:
        self.index.validate()
        existing = self.index.lookup(request.content_version_id)
        if existing and (existing.script_sha256 != script_digest(request.script)
                         or existing.job_id != request.job_id):
            raise SimilarityError("CONTENT_VERSION_CONFLICT", "版本已绑定另一份正文或作业")
        if existing and existing.profile != self.profile:
            raise SimilarityError("INDEX_INCOMPATIBLE", "历史记录的模型或索引版本不符")
        return existing

    def detect(self, request: DetectRequest) -> DetectionResult:
        self._existing(request)
        matches = self.index.search(self.embed(request.script), request.content_version_id, 10)
        matches = sorted(matches, key=lambda item: item.score, reverse=True)
        score = matches[0].score if matches else None
        passed = score is None or score < 0.85
        reason = "NO_HISTORY_MATCH" if score is None else (
            "BELOW_THRESHOLD" if passed else "SIMILARITY_BLOCKED")
        return DetectionResult(passed=passed, reason=reason, max_similarity=score,
                               matches=matches, profile=self.profile)

    def _check_approval(self, request: StoreRequest) -> None:
        approval = request.approval
        if (approval.job_id != request.job_id
                or approval.content_version_id != request.content_version_id
                or approval.script_sha256 != script_digest(request.script)
                or not self.required_checks <= approval.checks.keys()
                or not all(approval.checks.values())):
            raise SimilarityError("APPROVAL_REQUIRED", "只有同版本正文的全部必需检查通过后才能入库")

    def store(self, request: StoreRequest) -> StoreResult:
        self._check_approval(request)
        if self._existing(request) is None:
            record = StoredRecord(**request.model_dump(), profile=self.profile,
                                  script_sha256=script_digest(request.script))
            self.index.upsert(record, self.embed(request.script))
        return StoreResult(saved=True, point_id=request.content_version_id,
                           content_version_id=request.content_version_id,
                           index_version=self.profile.index_version)

    def reconcile(self, request: DetectRequest) -> ReconcileResult:
        return ReconcileResult(saved=self._existing(request) is not None,
                               point_id=request.content_version_id,
                               content_version_id=request.content_version_id,
                               index_version=self.profile.index_version)
