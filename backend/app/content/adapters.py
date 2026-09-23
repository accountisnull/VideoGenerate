"""Skill、CLI 与 MCP 共用的结果包装；业务实现不依赖包装。"""

from uuid import UUID

from pydantic import ValidationError

from app.content.errors import SimilarityError
from app.content.models import DetectRequest, IndexProfile, StoreRequest
from app.content.review.similarity import SimilarityService, validated_vector


class ToolAdapter:
    def __init__(self, service: SimilarityService):
        self.service = service

    def call(self, action: str, request: dict) -> dict:
        try:
            if action == "detect":
                data = self.service.detect(DetectRequest.model_validate(request)).model_dump(mode="json")
            elif action == "store":
                data = self.service.store(StoreRequest.model_validate(request)).model_dump(mode="json")
            elif action == "reconcile":
                data = self.service.reconcile(DetectRequest.model_validate(request)).model_dump(mode="json")
            elif action == "embed":
                data = {"vector": self.service.embed(request["script"]),
                        "profile": self.service.profile.model_dump(mode="json")}
            elif action == "search":
                if IndexProfile.model_validate(request["profile"]) != self.service.profile:
                    raise SimilarityError("INDEX_INCOMPATIBLE", "查询向量的模型或索引版本不符")
                vector = validated_vector(request["vector"], self.service.profile.dimension)
                matches = self.service.index.search(vector, UUID(request["content_version_id"]), 10)
                data = {"matches": [item.model_dump(mode="json") for item in matches],
                        "profile": self.service.profile.model_dump(mode="json")}
            else:
                raise SimilarityError("INVALID_INPUT", "未知操作")
            return {"success": True, "data": data, "error": None}
        except SimilarityError as error:
            return {"success": False, "data": None, "error": error.as_dict()}
        except (ValidationError, KeyError, TypeError, ValueError):
            return {"success": False, "data": None,
                    "error": SimilarityError("INVALID_INPUT", "输入字段缺失或类型不符").as_dict()}
