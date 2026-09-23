"""同质化检测的可交接错误，不以空结果掩盖故障。"""


class SimilarityError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self),
                "retryable": self.retryable, "details": {}}
