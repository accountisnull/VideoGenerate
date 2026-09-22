"""四模块 HTTP v1 公共契约，与业务执行和运行环境配置解耦。"""

from .capabilities import ContentCapabilities, SpeechCapabilities, VideoCapabilities
from .common import AssetRef, CreateHeaders, Error, ErrorResponse, HealthResponse
from .content import ContentJob, ContentRequest, ContentResult, Revision
from .media import (
    FinalVideo,
    SpeechJob,
    SpeechRequest,
    SpeechResult,
    VideoJob,
    VideoRequest,
    VideoResult,
)
from .tasks import JobReferences, Publication, TaskRequest, TaskResponse

__all__ = [
    "AssetRef", "ContentCapabilities", "ContentJob", "ContentRequest", "ContentResult",
    "CreateHeaders", "Error", "ErrorResponse", "FinalVideo", "HealthResponse", "JobReferences",
    "Publication", "Revision", "SpeechCapabilities", "SpeechJob", "SpeechRequest", "SpeechResult",
    "TaskRequest", "TaskResponse", "VideoCapabilities", "VideoJob", "VideoRequest", "VideoResult",
]
