"""显式真实模型联调，普通 pytest 不执行；只使用独立临时测试集合。"""

import argparse
import json
import sys
import tempfile
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

# 支持从 backend 直接执行脚本，避免要求全局安装项目。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qdrant_client import QdrantClient

from app.content.providers.embedding import (
    LocalEmbedding,
    model_fingerprint,
)
from app.content.review.similarity import SimilarityService
from app.content.similarity_models import (
    Approval,
    DetectRequest,
    IndexProfile,
    StoreRequest,
    script_digest,
)
from app.content.storage.vector_index import QdrantIndex

CORPUS = {
    "history": "绿萝喜欢明亮的散射光，不要放在太阳下暴晒。浇水前摸一摸盆土，表层干了再浇透，避免花盆积水导致烂根。冬天生长慢，要减少浇水。",
    "paraphrase": "养绿萝要放在光线明亮但没有阳光直射的位置。等盆土表面变干后再一次浇透，盆底不能存水，否则容易烂根。到了冬季，绿萝长得慢，浇水次数也要减少。",
    "different_topic": "乘坐高铁出行，请提前核对车次和发车时间，携带有效身份证件。到站后按照指示完成安检，在对应检票口候车，检票结束前及时进站。",
}


def run(model_path: Path, output: Path, qdrant_url: str | None) -> None:
    profile = IndexProfile(model_id="BAAI/bge-m3", model_revision=model_fingerprint(model_path),
                           dimension=1024, index_version="bge-m3-dense-cls-l2-cosine-v1")
    embedding = LocalEmbedding(model_path, profile)
    collection = "similarity_verify_" + uuid4().hex
    results = {}
    with tempfile.TemporaryDirectory(prefix="similarity-verify-") as scratch:
        client = (QdrantClient(url=qdrant_url, timeout=10, check_compatibility=False, trust_env=False)
                  if qdrant_url else QdrantClient(path=str(Path(scratch) / "qdrant")))
        created = False
        try:
            index = QdrantIndex(client, collection, profile)
            index.initialize()
            created = True
            service = SimilarityService(embedding, index, profile)
            history = DetectRequest(script=CORPUS["history"], job_id=uuid4(), content_version_id=uuid4())
            print("[verify] 有效空库检测", flush=True)
            results["empty"] = service.detect(history).model_dump(mode="json")
            approval = Approval(approval_id=uuid4(), job_id=history.job_id,
                content_version_id=history.content_version_id, script_sha256=script_digest(history.script),
                checks={"keywords": True, "content_audit": True, "similarity": True})
            store_request = StoreRequest(**history.model_dump(), approval=approval)
            print("[verify] 最终测试稿入库与重放", flush=True)
            first = service.store(store_request)
            second = service.store(store_request)
            assert first.point_id == second.point_id
            assert client.count(collection, exact=True).count == 1
            results["store"] = second.model_dump(mode="json")
            results["reconcile"] = service.reconcile(history).model_dump(mode="json")
            print("[verify] 同版本自身排除", flush=True)
            results["self_excluded"] = service.detect(history).model_dump(mode="json")
            for case, text in CORPUS.items():
                print(f"[verify] 真实比较：{case}", flush=True)
                req = DetectRequest(script=text, job_id=uuid4(), content_version_id=uuid4())
                start = time.perf_counter()
                result = service.detect(req)
                results[case] = {**result.model_dump(mode="json"),
                                 "elapsed_seconds": time.perf_counter() - start}
            assert results["empty"]["max_similarity"] is None
            assert results["self_excluded"]["matches"] == []
            assert results["reconcile"]["saved"]
            # 同文必须拦截；改写与不同主题的分数如实记录，不调整阈值造出预期。
            assert results["history"]["passed"] is False
            results["count"] = client.count(collection, exact=True).count
            if not qdrant_url:
                client.close()
                client = QdrantClient(path=str(Path(scratch) / "qdrant"))
                reopened = SimilarityService(embedding, QdrantIndex(client, collection, profile), profile)
                results["after_restart"] = reopened.reconcile(history).model_dump(mode="json")
                assert results["after_restart"]["saved"]
        finally:
            # 仅清理由本次脚本生成的随机集合；从不接收业务集合名。
            if qdrant_url and created:
                client.delete_collection(collection)
            client.close()
    report = {"created_at": datetime.now(UTC).isoformat(), "profile": profile.model_dump(),
              "mode": "server" if qdrant_url else "local_disk", "corpus": CORPUS,
              "packages": {name: version(name) for name in
                           ("qdrant-client", "sentence-transformers", "torch", "mcp")},
              "results": results, "limitations": ["人工固定语料，非生产效果评估", "未接入主管真实作业"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(output), "scores": {
        key: results[key]["max_similarity"] for key in CORPUS}}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("../data/tmp/similarity-real-verification.json"))
    parser.add_argument("--qdrant-url", help="可选：使用独立随机测试集合验证真实 Qdrant 服务")
    args = parser.parse_args()
    run(args.model_path.resolve(), args.output, args.qdrant_url)
