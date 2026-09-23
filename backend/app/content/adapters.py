"""Skill、CLI 与 MCP 共用的结果包装；业务实现不依赖包装。"""

import asyncio
from uuid import UUID

from pydantic import ValidationError

from app.content.async_tools import bounded_thread
from app.content.errors import SimilarityError
from app.content.review.similarity import SimilarityService, validated_vector
from app.content.similarity_models import DetectRequest, IndexProfile, StoreRequest


class ToolAdapter:
    def __init__(self, service: SimilarityService):
        self.service = service
        self.lock = asyncio.Lock()

    async def acall(self, action: str, request: dict) -> dict:
        # 同一索引单写入者；取消等待正在进行的有界 I/O 退出后释放锁。
        async with self.lock:
            try:
                request = self._profile_request(request)
                if action == "detect":
                    data = (await self.service.adetect(DetectRequest.model_validate(request))).model_dump(mode="json")
                elif action == "store":
                    data = (await self.service.astore(StoreRequest.model_validate(request))).model_dump(mode="json")
                elif action == "embed":
                    data = {"vector": await self.service.aembed(request["script"]),
                            "profile": self.service.profile.model_dump(mode="json")}
                else:
                    return await bounded_thread(lambda: self.call(action, request))
                return {"success": True, "data": data, "error": None}
            except SimilarityError as error:
                return {"success": False, "data": None, "error": error.as_dict()}
            except (ValidationError, KeyError, TypeError, ValueError):
                return {"success": False, "data": None,
                        "error": SimilarityError("INVALID_INPUT", "输入字段缺失或类型不符").as_dict()}

    def call(self, action: str, request: dict) -> dict:
        try:
            request = self._profile_request(request)
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

    def _profile_request(self, request: dict) -> dict:
        request = dict(request)
        expected = request.pop("expected_profile", None)
        if expected is not None and IndexProfile.model_validate(expected) != self.service.profile:
            raise SimilarityError("INDEX_INCOMPATIBLE", "待写入或核对的索引版本与审核时不一致")
        return request
