"""可交给编排层的脱敏错误，不携带上游响应正文。"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .generation.models import ModelCallRecord


class ContentError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False,
                 call: "ModelCallRecord | None" = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.call = call

    def as_dict(self):
        return {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
            "model_call": self.call.model_dump(mode="json") if self.call else None,
        }
