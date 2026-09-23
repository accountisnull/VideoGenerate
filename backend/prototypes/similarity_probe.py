"""一次性实验：真实中文向量能否通过 Qdrant 查重；不接入生产流程。"""

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

MODEL_ID = "BAAI/bge-m3"
INDEX_VERSION = "probe-bge-m3-dense-cls-l2-cosine-v1"
DIMENSION = 1024
THRESHOLD = 0.85
CORPUS = {
    "history": "绿萝喜欢明亮的散射光，不要放在太阳下暴晒。浇水前摸一摸盆土，表层干了再浇透，避免花盆积水导致烂根。冬天生长慢，要减少浇水。",
    "identical": "绿萝喜欢明亮的散射光，不要放在太阳下暴晒。浇水前摸一摸盆土，表层干了再浇透，避免花盆积水导致烂根。冬天生长慢，要减少浇水。",
    "paraphrase": "养绿萝要放在光线明亮但没有阳光直射的位置。等盆土表面变干后再一次浇透，盆底不能存水，否则容易烂根。到了冬季，绿萝长得慢，浇水次数也要减少。",
    "different_topic": "乘坐高铁出行，请提前核对车次和发车时间，携带有效身份证件。到站后按照指示完成安检，在对应检票口候车，检票结束前及时进站。",
}


def encode_to_file(output: Path, model_path: Path) -> None:
    """子进程限制模型加载和推理总时间，离线读取固定版本权重。"""
    import faulthandler

    faulthandler.dump_traceback_later(60)
    print("[probe] 导入推理依赖", flush=True)
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer

    fingerprints = {}
    for filename in (
        "pytorch_model.bin", "config.json", "modules.json", "1_Pooling/config.json",
        "sentence_bert_config.json", "config_sentence_transformers.json",
        "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
        "sentencepiece.bpe.model",
    ):
        with (model_path / filename).open("rb") as source:
            fingerprints[filename] = hashlib.file_digest(source, "sha256").hexdigest()
    model_revision = "local-sha256:" + hashlib.sha256(
        json.dumps(fingerprints, sort_keys=True).encode()
    ).hexdigest()
    torch.set_num_threads(2)
    print("[probe] 加载离线模型", flush=True)
    model = SentenceTransformer(
        str(model_path.resolve()), device="cpu", local_files_only=True,
        trust_remote_code=False,
    )
    texts = list(CORPUS.values())
    token_counts = [
        len(model.tokenizer(text, truncation=False)["input_ids"]) for text in texts
    ]
    if max(token_counts) > model.max_seq_length:
        raise ValueError("实验输入超过模型限制，禁止静默截断")
    print("[probe] 生成真实向量", flush=True)
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    if vectors.shape != (len(texts), DIMENSION) or not np.isfinite(vectors).all():
        raise ValueError("向量维度不符或存在非有限值")
    output.write_text(json.dumps({
        "model_revision": model_revision, "model_file_sha256": fingerprints,
        "vectors": vectors.tolist(),
        "token_counts": token_counts,
        "max_sequence_tokens": model.max_seq_length,
        "vector_norms": np.linalg.norm(vectors, axis=1).tolist(),
    }, ensure_ascii=False), encoding="utf-8")
    faulthandler.cancel_dump_traceback_later()


def decision(score: float | None) -> dict:
    return {
        "passed": score is None or score < THRESHOLD,
        "reason": "NO_HISTORY_MATCH" if score is None else (
            "SIMILARITY_BLOCKED" if score >= THRESHOLD else "BELOW_THRESHOLD"
        ),
        "max_similarity": score,
    }


def run_probe(output: Path, timeout_seconds: float, model_path: Path) -> None:
    from qdrant_client import QdrantClient, models

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="PROTOTYPE-similarity-") as scratch:
        embedding_path = Path(scratch) / "embedding.json"
        subprocess.run(
            [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
             "--embedding-output", str(embedding_path), "--model-path", str(model_path)],
            check=True, timeout=timeout_seconds,
        )
        embedding = json.loads(embedding_path.read_text(encoding="utf-8"))
        model_revision = embedding["model_revision"]
        embedding_seconds = time.perf_counter() - started
        vectors = embedding["vectors"]
        index_path = str(Path(scratch) / "PROTOTYPE-qdrant")
        collection = "similarity_probe"
        client = QdrantClient(path=index_path)
        try:
            client.create_collection(
                collection_name=collection,
                vectors_config=models.VectorParams(size=DIMENSION, distance=models.Distance.COSINE),
            )

            def search(vector: list[float], excluded_version: str) -> list:
                return client.query_points(
                    collection_name=collection, query=vector, limit=10,
                    query_filter=models.Filter(must_not=[models.FieldCondition(
                        key="content_version_id", match=models.MatchValue(value=excluded_version),
                    )]), with_payload=True,
                ).points

            empty = search(vectors[0], "new-v1")
            if empty:
                raise AssertionError("新建集合不为空")
            point_id = str(uuid5(NAMESPACE_URL, "video-generate/content/history-v1"))
            point = models.PointStruct(id=point_id, vector=vectors[0], payload={
                "content_version_id": "history-v1", "script": CORPUS["history"],
                "script_sha256": hashlib.sha256(CORPUS["history"].encode()).hexdigest(),
                "model_id": MODEL_ID, "model_revision": model_revision,
                "index_version": INDEX_VERSION,
            })
            counts = []
            for _ in range(2):
                client.upsert(collection_name=collection, points=[point], wait=True)
                counts.append(client.count(collection_name=collection, exact=True).count)
            if counts != [1, 1]:
                raise AssertionError("重复 upsert 产生额外记录")
            comparisons = []
            for name, vector in zip(list(CORPUS)[1:], vectors[1:], strict=True):
                hits = search(vector, "new-v1")
                if len(hits) != 1:
                    raise AssertionError("应命中唯一的历史记录")
                hit = hits[0]
                comparisons.append({"case": name, **decision(hit.score), "hit": hit.payload})
            self_hits = search(vectors[0], "history-v1")
            if self_hits:
                raise AssertionError("自身记录未被排除")
        finally:
            client.close()
        reopened = QdrantClient(path=index_path)
        try:
            persisted_count = reopened.count(collection_name=collection, exact=True).count
            if persisted_count != 1:
                raise AssertionError("重新打开后历史记录丢失")
        finally:
            reopened.close()

    boundaries = [decision(score) for score in (0.8499, 0.85, 0.8501)]
    if [item["passed"] for item in boundaries] != [True, False, False]:
        raise AssertionError("阈值边界不符合任务单")
    report = {
        "status": "PROTOTYPE_ONLY", "created_at": datetime.now(UTC).isoformat(),
        "model_id": MODEL_ID, "model_revision": model_revision,
        "dimension": DIMENSION, "pooling": "CLS", "normalization": "L2",
        "query_instruction": None, "distance": "Cosine", "threshold": THRESHOLD,
        "top_k": 10, "index_version": INDEX_VERSION, "device": "cpu",
        "embedding_timeout_seconds": timeout_seconds, "embedding_seconds": embedding_seconds,
        "qdrant_mode": "local_disk_temporary", "python": sys.version,
        "packages": {name: version(name) for name in (
            "sentence-transformers", "transformers", "torch", "qdrant-client", "numpy",
        )},
        "corpus": CORPUS, "embedding": embedding,
        "empty_database": decision(None), "comparisons": comparisons,
        "self_exclusion": decision(None), "upsert_counts": counts,
        "reopened_count": persisted_count, "synthetic_boundaries": boundaries,
        "not_verified": ["Qdrant 服务端及断连", "生产稿件去重效果", "生产 detect/store 接口",
                         "跨库恢复", "模型或维度变更拦截", "同版本不同正文冲突"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "report": str(output), "comparisons": comparisons,
        "upsert_counts": counts, "reopened_count": persisted_count,
        "embedding_seconds": embedding_seconds,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True, help="完整的本地 BGE-M3 模型目录")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[2] / "data/tmp/similarity-probe-bge-m3.json")
    parser.add_argument("--timeout-seconds", type=float, default=120)
    parser.add_argument("--embedding-output", type=Path, help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.embedding_output:
        encode_to_file(arguments.embedding_output, arguments.model_path)
    else:
        run_probe(arguments.output, arguments.timeout_seconds, arguments.model_path)
