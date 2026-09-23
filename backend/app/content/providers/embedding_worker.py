"""独立模型进程，stdout 仅输出机器可读 JSON。"""

import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from app.content.errors import SimilarityError
from app.content.providers.embedding import model_fingerprint
from app.content.review.similarity import validated_vector
from app.content.similarity_models import IndexProfile


def infer(payload: dict) -> list[float]:
    profile = IndexProfile.model_validate(payload["profile"])
    model_path = Path(payload["model_path"])
    script = payload["script"]
    if not isinstance(script, str) or not script.strip():
        raise SimilarityError("INVALID_INPUT", "口播稿不能为空")
    if profile.model_id != "BAAI/bge-m3" or profile.dimension != 1024:
        raise SimilarityError("INDEX_INCOMPATIBLE", "本供应方只支持 BGE-M3 dense 1024 维")
    if model_fingerprint(model_path) != profile.model_revision:
        raise SimilarityError("INDEX_INCOMPATIBLE", "模型文件已变更，不能写入当前索引")
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(2)
    model = SentenceTransformer(str(model_path), device="cpu", local_files_only=True,
                                trust_remote_code=False, model_kwargs={"use_safetensors": False})
    tokens = model.tokenizer(script, truncation=False)["input_ids"]
    if len(tokens) > model.max_seq_length:
        raise SimilarityError("INPUT_TOO_LONG", f"输入超过模型 {model.max_seq_length} token 上限")
    vector = model.encode([script], normalize_embeddings=True, show_progress_bar=False)[0].tolist()
    return validated_vector(vector, profile.dimension)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        with redirect_stdout(sys.stderr):
            vector = infer(payload)
        result = {"vector": vector}
    except SimilarityError as error:
        result = {"error": error.as_dict()}
    except Exception as error:  # noqa: BLE001 -- 独立进程边界必须返回结构化失败
        # 进程边界兜底，不将模型路径、正文或第三方异常回传给消费者。
        result = {"error": SimilarityError("EMBEDDING_FAILED", "模型加载或推理失败").as_dict(),
                  "diagnostic": type(error).__name__}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
