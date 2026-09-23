"""离线 BGE-M3 子进程适配；超时杀死进程，不留后台推理。"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from app.content.errors import SimilarityError
from app.content.models import IndexProfile
from app.content.review.similarity import validated_vector

MODEL_FILES = (
    "pytorch_model.bin", "config.json", "modules.json", "1_Pooling/config.json",
    "sentence_bert_config.json", "config_sentence_transformers.json", "tokenizer.json",
    "tokenizer_config.json", "special_tokens_map.json", "sentencepiece.bpe.model",
)


def model_fingerprint(model_path: Path) -> str:
    if any(model_path.glob("*.safetensors*")) or (model_path / "pytorch_model.bin.index.json").exists():
        raise SimilarityError("INDEX_INCOMPATIBLE", "本部署只接受已登记的单文件 pytorch_model.bin 权重")
    fingerprints = {}
    try:
        for filename in MODEL_FILES:
            with (model_path / filename).open("rb") as source:
                fingerprints[filename] = hashlib.file_digest(source, "sha256").hexdigest()
    except OSError as error:
        raise SimilarityError("EMBEDDING_FAILED", "本地模型文件缺失或不可读") from error
    return "local-sha256:" + hashlib.sha256(
        json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()


class LocalEmbedding:
    def __init__(self, model_path: Path, profile: IndexProfile, timeout_seconds: float = 120):
        self.model_path = model_path
        self.profile = profile
        self.timeout_seconds = timeout_seconds

    def embed(self, script: str) -> list[float]:
        if not self.model_path.is_dir():
            raise SimilarityError("EMBEDDING_FAILED", "本地模型目录不存在")
        payload = {"script": script, "model_path": str(self.model_path.resolve()),
                   "profile": self.profile.model_dump(mode="json")}
        try:
            process = subprocess.run(
                [sys.executable, "-X", "utf8", "-m", "app.content.providers.embedding_worker"],
                cwd=Path(__file__).resolve().parents[3],
                input=json.dumps(payload, ensure_ascii=False), capture_output=True,
                text=True, encoding="utf-8", timeout=self.timeout_seconds, check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise SimilarityError("EMBEDDING_TIMEOUT", "模型加载或推理超时", retryable=True) from error
        except OSError as error:
            raise SimilarityError("EMBEDDING_FAILED", "无法启动模型进程") from error
        if process.returncode:
            raise SimilarityError("EMBEDDING_FAILED", "模型进程异常退出")
        try:
            result = json.loads(process.stdout)
            if "error" in result:
                details = result["error"]
                raise SimilarityError(details["code"], details["message"])
            vector = result["vector"]
            if not isinstance(vector, list):
                raise TypeError
        except (ValueError, KeyError, TypeError) as error:
            raise SimilarityError("EMBEDDING_FAILED", "模型返回无效结果") from error
        return validated_vector(vector, self.profile.dimension)
