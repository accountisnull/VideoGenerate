"""Qdrant 索引：显式初始化、完整模型绑定、幂等写入与读回确认。"""

from collections.abc import Callable
from typing import TypeVar
from uuid import UUID

import httpx
from pydantic import ValidationError
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.content.errors import SimilarityError
from app.content.review.similarity import REQUIRED_CHECKS, validated_vector
from app.content.similarity_models import (
    IndexProfile,
    Match,
    StoredRecord,
    script_digest,
)

T = TypeVar("T")


class QdrantIndex:
    def __init__(self, client: QdrantClient, collection: str, profile: IndexProfile,
                 required_checks: frozenset[str] = REQUIRED_CHECKS):
        self.client = client
        self.collection = collection
        self.profile = profile
        self.required_checks = required_checks

    def _call(self, call: Callable[[], T], *, write: bool = False) -> T:
        try:
            return call()
        except (ResponseHandlingException, UnexpectedResponse, httpx.HTTPError,
                OSError, RuntimeError, ValueError) as error:
            code = "VECTOR_WRITE_UNCONFIRMED" if write else "VECTOR_UNAVAILABLE"
            raise SimilarityError(code, "向量库操作失败，不能确认结果", retryable=True) from error

    def initialize(self) -> None:
        if not self._call(lambda: self.client.collection_exists(self.collection)):
            self._call(lambda: self.client.create_collection(
                self.collection, vectors_config=models.VectorParams(
                    size=self.profile.dimension, distance=models.Distance.COSINE),
                metadata={"similarity_profile": self.profile.model_dump(mode="json")},
            ), write=True)
        self.validate()

    def validate(self) -> None:
        if not self._call(lambda: self.client.collection_exists(self.collection)):
            raise SimilarityError("INDEX_NOT_READY", "向量集合不存在，请显式初始化或恢复历史库")
        info = self._call(lambda: self.client.get_collection(self.collection))
        vectors = info.config.params.vectors
        metadata = info.config.metadata or {}
        if (not isinstance(vectors, models.VectorParams)
                or vectors.size != self.profile.dimension
                or vectors.distance != models.Distance.COSINE
                or metadata.get("similarity_profile") != self.profile.model_dump(mode="json")):
            raise SimilarityError("INDEX_INCOMPATIBLE", "集合的模型、维度、计分方式或索引版本不符")

    def _record(self, payload: dict | None, point_id: str | int | UUID) -> StoredRecord:
        try:
            record = StoredRecord.model_validate(payload)
        except ValidationError as error:
            raise SimilarityError("INDEX_INCOMPATIBLE", "历史记录缺少有效模型或版本元数据") from error
        if (record.profile != self.profile or str(record.content_version_id) != str(point_id)
                or record.script_sha256 != script_digest(record.script)
                or record.approval.script_sha256 != record.script_sha256
                or record.approval.content_version_id != record.content_version_id
                or record.approval.job_id != record.job_id
                or not self.required_checks <= record.approval.checks.keys()
                or not all(record.approval.checks.values())):
            raise SimilarityError("INDEX_INCOMPATIBLE", "历史记录的正文、审核或索引元数据不一致")
        return record

    def lookup(self, version: UUID) -> StoredRecord | None:
        self.validate()
        records = self._call(lambda: self.client.retrieve(
            self.collection, ids=[str(version)], with_payload=True, with_vectors=False))
        return self._record(records[0].payload, records[0].id) if records else None

    def search(self, vector: list[float], exclude_version: UUID, limit: int = 10) -> list[Match]:
        self.validate()
        validated_vector(vector, self.profile.dimension)
        if limit != 10:
            raise SimilarityError("INVALID_INPUT", "本期检索固定为 top 10")
        result = self._call(lambda: self.client.query_points(
            self.collection, query=vector, limit=limit,
            query_filter=models.Filter(must_not=[models.FieldCondition(
                key="content_version_id", match=models.MatchValue(value=str(exclude_version)))]),
            with_payload=True, with_vectors=False))
        matches = []
        for hit in result.points:
            record = self._record(hit.payload, hit.id)
            try:
                match = Match(content_version_id=record.content_version_id, job_id=record.job_id,
                              score=hit.score, script_excerpt=record.script[:1000])
            except ValidationError as error:
                raise SimilarityError("INDEX_INCOMPATIBLE", "向量库返回非法相似度") from error
            matches.append(match)
        return matches

    def upsert(self, record: StoredRecord, vector: list[float]) -> None:
        self.validate()
        validated_vector(vector, self.profile.dimension)
        existing = self.lookup(record.content_version_id)
        if existing:
            if existing.script_sha256 != record.script_sha256 or existing.job_id != record.job_id:
                raise SimilarityError("CONTENT_VERSION_CONFLICT", "同版本不能保存不同正文或作业")
            return
        self._record(record.model_dump(mode="json"), record.content_version_id)
        result = self._call(lambda: self.client.upsert(
            self.collection, points=[models.PointStruct(id=str(record.content_version_id),
                vector=vector, payload=record.model_dump(mode="json"))], wait=True), write=True)
        if result.status != models.UpdateStatus.COMPLETED:
            raise SimilarityError("VECTOR_WRITE_UNCONFIRMED", "向量保存尚未完成", retryable=True)
        try:
            confirmed = self.lookup(record.content_version_id)
        except SimilarityError as error:
            raise SimilarityError("VECTOR_WRITE_UNCONFIRMED", "保存后核对失败", retryable=True) from error
        if confirmed != record:
            raise SimilarityError("VECTOR_WRITE_UNCONFIRMED", "保存后记录不匹配", retryable=True)
