"""集成模块的生产任务与发布结果快照。"""

from typing import Literal
from uuid import UUID

from pydantic import model_validator

from .common import Error, RequestModel, ResponseModel, Text, Topic, UtcTime
from .media import FinalVideo


class TaskRequest(RequestModel):
    topic: Topic
    avatar_id: Text
    voice_id: Text
    publish_account_id: Text


class JobReferences(ResponseModel):
    content: UUID | None
    speech: UUID | None
    video: UUID | None


class Publication(ResponseModel):
    state: Literal["not_started", "submitted", "processing", "published", "failed", "unknown"]
    platform: Literal["douyin"]
    account_id: Text
    submission_id: Text | None
    platform_video_id: Text | None
    published_at: UtcTime | None

    @model_validator(mode="after")
    def published_time(self):
        if (self.state == "published") != (self.published_at is not None):
            raise ValueError("仅确认已发布时填写 published_at，且确认后必须填写")
        return self


class TaskResponse(ResponseModel):
    task_id: UUID
    state: Literal["queued", "running", "waiting_external", "succeeded", "failed"]
    stage: Literal["content", "speech", "video", "integration", "publication", "completed"]
    created_at: UtcTime
    updated_at: UtcTime
    jobs: JobReferences
    final_video: FinalVideo | None
    publication: Publication
    error: Error | None

    @model_validator(mode="after")
    def consistent_snapshot(self):
        if self.updated_at < self.created_at:
            raise ValueError("更新时间不能早于创建时间")
        success = self.state == "succeeded"
        if success != (self.stage == "completed"):
            raise ValueError("仅成功任务的阶段可以为 completed")
        if success != (self.publication.state == "published"):
            raise ValueError("生产任务成功与发布确认必须一致")
        if success and self.final_video is None:
            raise ValueError("成功任务必须保留成片")
        if (self.state in ("failed", "waiting_external")) != (self.error is not None):
            raise ValueError("仅失败或等待外部结果时必须填写 error")
        if self.state == "queued" and self.stage != "content":
            raise ValueError("初始任务阶段必须为 content")
        if self.publication.state == "failed" and self.state != "failed":
            raise ValueError("发布失败必须标记生产任务失败")
        if self.publication.state == "unknown" and self.state != "waiting_external":
            raise ValueError("发布结果不明时必须等待外部核实")
        return self
