"""配音、数字人和成片的传输模型；不读取或修改实际媒体。"""

from uuid import UUID

from pydantic import StrictBool

from .common import (
    AssetRef,
    FrameRate,
    JobResponse,
    NonNegativeInt,
    PositiveInt,
    RequestAssetRef,
    RequestModel,
    ResponseModel,
    Sha256,
    Text,
)


class SpeechRequest(RequestModel):
    task_id: UUID
    content_job_id: UUID
    text: Text
    voice_id: Text
    profile_id: Text


class SpeechResult(ResponseModel):
    content_job_id: UUID
    audio: AssetRef
    duration_ms: PositiveInt
    codec: Text
    sample_rate_hz: PositiveInt
    channels: PositiveInt


class SpeechJob(JobResponse[SpeechResult]):
    pass


class VideoRequest(RequestModel):
    task_id: UUID
    speech_job_id: UUID
    avatar_resource_id: Text
    profile_id: Text
    audio: RequestAssetRef


class VideoResult(ResponseModel):
    speech_job_id: UUID
    source_audio_sha256: Sha256
    video: AssetRef
    duration_ms: PositiveInt
    codec: Text
    pixel_format: Text
    width: PositiveInt
    height: PositiveInt
    fps: FrameRate
    has_audio: StrictBool
    audio_offset_ms: NonNegativeInt


class VideoJob(JobResponse[VideoResult]):
    pass


class FinalVideo(ResponseModel):
    asset: AssetRef
    duration_ms: PositiveInt
    width: PositiveInt
    height: PositiveInt
    fps: FrameRate
