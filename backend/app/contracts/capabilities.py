"""可供调用方检查的服务能力和资源声明。"""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from .common import FrameRate, JsonObject, PositiveInt, ResponseModel, Text


class SpeechInput(ResponseModel):
    max_text_chars: PositiveInt


class AudioFormat(ResponseModel):
    media_type: Text
    codec: Text
    sample_rate_hz: PositiveInt
    channels: PositiveInt


class SpeechProfile(ResponseModel):
    profile_id: Text
    input_constraints: SpeechInput
    output_constraints: AudioFormat


class VideoInput(ResponseModel):
    audio_media_types: Annotated[list[Text], Field(min_length=1)]
    audio_codecs: Annotated[list[Text], Field(min_length=1)]
    sample_rates_hz: Annotated[list[PositiveInt], Field(min_length=1)]
    channels: Annotated[list[PositiveInt], Field(min_length=1)]
    max_audio_duration_ms: PositiveInt


class VideoFormat(ResponseModel):
    media_type: Text
    codec: Text
    pixel_format: Text
    width: PositiveInt
    height: PositiveInt
    fps: FrameRate
    has_audio: StrictBool


class VideoProfile(ResponseModel):
    profile_id: Text
    input_constraints: VideoInput
    output_constraints: VideoFormat


class ContentProfile(ResponseModel):
    profile_id: Text
    input_constraints: JsonObject
    output_constraints: JsonObject


class Resource(ResponseModel):
    resource_id: Text
    display_name: Text
    kind: Literal["voice", "avatar"]
    profile_ids: Annotated[list[Text], Field(min_length=1)]


class Voice(Resource):
    kind: Literal["voice"]


class Avatar(Resource):
    kind: Literal["avatar"]


class CapabilitiesBase(ResponseModel):
    api_version: Literal["1.0"]

    @model_validator(mode="after")
    def resource_profiles_exist(self):
        profile_ids = [item.profile_id for item in self.profiles]
        resource_ids = [item.resource_id for item in self.resources]
        if len(profile_ids) != len(set(profile_ids)) or len(resource_ids) != len(set(resource_ids)):
            raise ValueError("配置档编号和资源编号必须各自唯一")
        for resource in self.resources:
            if not set(resource.profile_ids).issubset(profile_ids):
                raise ValueError("资源引用了未声明的配置档")
            if len(resource.profile_ids) != len(set(resource.profile_ids)):
                raise ValueError("资源的配置档编号不能重复")
        return self


class ContentCapabilities(CapabilitiesBase):
    service: Literal["content"]
    profiles: list[ContentProfile]
    resources: Annotated[list[Resource], Field(max_length=0)]


class SpeechCapabilities(CapabilitiesBase):
    service: Literal["speech"]
    profiles: list[SpeechProfile]
    resources: list[Voice]


class VideoCapabilities(CapabilitiesBase):
    service: Literal["video"]
    profiles: list[VideoProfile]
    resources: list[Avatar]
