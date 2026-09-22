"""内容模块的提交请求与成功结果。"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator
from pydantic.json_schema import SkipJsonSchema

from .common import JobResponse, RequestModel, ResponseModel, Text, Topic


class Revision(RequestModel):
    source_job_id: UUID
    instructions: Annotated[Text, Field(max_length=4000)]


class ContentRequest(RequestModel):
    task_id: UUID
    topic: Topic
    revision: Revision | SkipJsonSchema[None] = Field(
        default=None,
        exclude_if=lambda value: value is None,
        json_schema_extra=lambda schema: schema.pop("default", None),
    )

    @field_validator("revision", mode="before")
    @classmethod
    def omitted_or_object(cls, value):
        if value is None:
            raise ValueError("不改稿时请省略 revision，不能传 null")
        return value


class ContentResult(ResponseModel):
    script: Text
    title: Text
    tags: list[Text]
    review_passed: Literal[True]

    @field_validator("tags")
    @classmethod
    def normalized_tags(cls, values):
        if any("#" in value for value in values):
            raise ValueError("标签不能包含 #")
        return list(dict.fromkeys(values))

    @field_validator("review_passed", mode="before")
    @classmethod
    def passed_only(cls, value):
        if value is not True:
            raise ValueError("成功的内容结果必须通过检查")
        return value


class ContentJob(JobResponse[ContentResult]):
    pass
