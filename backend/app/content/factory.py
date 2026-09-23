"""服务生命周期由调用方拥有，同机本地模式只允许一个索引拥有者。"""

from collections.abc import Iterator
from contextlib import contextmanager

from qdrant_client import QdrantClient

from app.content.config import SimilarityConfig
from app.content.errors import SimilarityError
from app.content.providers.embedding import LocalEmbedding
from app.content.review.similarity import SimilarityService
from app.content.storage.vector_index import QdrantIndex


@contextmanager
def open_service(config: SimilarityConfig) -> Iterator[SimilarityService]:
    try:
        if config.qdrant_path is not None:
            client = QdrantClient(path=str(config.qdrant_path))
        else:
            client = QdrantClient(url=config.qdrant_url, timeout=config.qdrant_timeout_seconds,
                                  check_compatibility=False, trust_env=False)
    except (OSError, RuntimeError, ValueError) as error:
        raise SimilarityError("VECTOR_UNAVAILABLE", "无法打开向量库；检查路径、服务或本地独占锁") from error
    try:
        yield SimilarityService(
            LocalEmbedding(config.model_path, config.profile, config.embedding_timeout_seconds),
            QdrantIndex(client, config.collection, config.profile, config.required_checks),
            config.profile, config.required_checks)
    finally:
        client.close()
