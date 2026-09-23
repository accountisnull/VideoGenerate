"""存储查询快照；沿用主管记录，不新增业务状态来源。"""

from uuid import UUID

from app.contracts import ContentJob, ContentRequest
from app.contracts.common import Text, UtcTime

from ..models import InternalModel, RunOutcome


class CallRecord(InternalModel):
    call_id: UUID
    job_id: UUID
    stage: Text
    capability: Text
    input_json: str
    output_json: str | None
    started_at: UtcTime
    finished_at: UtcTime | None


class StoredContent(InternalModel):
    job: ContentJob
    request: ContentRequest
    outcome: RunOutcome | None
    calls: tuple[CallRecord, ...]
