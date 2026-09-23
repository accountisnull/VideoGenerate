"""通过现有 runtime 入口读取相似度配置，默认不创建或清空索引。"""

import math
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from app import runtime
from app.content.errors import SimilarityError
from app.content.review.similarity import REQUIRED_CHECKS
from app.content.similarity_models import IndexProfile


@dataclass(frozen=True)
class SimilarityConfig:
    model_path: Path
    profile: IndexProfile
    embedding_timeout_seconds: float
    qdrant_path: Path | None
    qdrant_url: str | None
    collection: str
    qdrant_timeout_seconds: int
    required_checks: frozenset[str]

    @classmethod
    def load(cls) -> "SimilarityConfig":
        return cls.from_values(runtime.environment_values(), runtime.ROOT)

    @classmethod
    def from_values(cls, values: dict, root: Path) -> "SimilarityConfig":
        def value(name: str, default: str = "") -> str:
            return (values.get("SIMILARITY_" + name) or default).strip()

        def path(raw: str) -> Path:
            result = Path(raw)
            return (result if result.is_absolute() else root / result).resolve()

        try:
            model = value("MODEL_PATH")
            revision = value("MODEL_REVISION")
            local = value("QDRANT_PATH")
            url = value("QDRANT_URL")
            timeout = float(value("EMBEDDING_TIMEOUT_SECONDS", "120"))
            db_timeout = int(value("QDRANT_TIMEOUT_SECONDS", "10"))
            collection = value("COLLECTION", "content_similarity_bge_m3_v1")
            checks = frozenset(x.strip() for x in value(
                "REQUIRED_CHECKS", "keywords,content_audit,similarity").split(",") if x.strip())
            if (not model or not re.fullmatch(r"local-sha256:[0-9a-f]{64}", revision)
                    or bool(local) == bool(url) or not math.isfinite(timeout) or timeout <= 0
                    or db_timeout <= 0 or not REQUIRED_CHECKS <= checks
                    or not re.fullmatch(r"[a-zA-Z0-9_-]+", collection)):
                raise ValueError
            if url and (urlparse(url).scheme not in {"http", "https"} or not urlparse(url).hostname
                        or urlparse(url).username or urlparse(url).password):
                raise ValueError
            return cls(path(model), IndexProfile(
                model_id="BAAI/bge-m3", model_revision=revision, dimension=1024,
                index_version=value("INDEX_VERSION", "bge-m3-dense-cls-l2-cosine-v1")),
                timeout, path(local) if local else None, url or None, collection, db_timeout, checks)
        except (ValueError, TypeError, AttributeError) as error:
            raise SimilarityError("INVALID_CONFIG", "同质化配置无效：检查模型路径、指纹、超时和唯一索引连接模式") from error
