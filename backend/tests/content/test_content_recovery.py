"""模拟断电边界：数据库重开仍可查询，未知调用不得重新执行。"""

from uuid import uuid4

from app.content.models import (
    Draft,
    ReviewDecision,
    ReviewRecord,
    RoundRecord,
    RunContext,
    RunOutcome,
)
from app.content.storage import ContentStore
from app.contracts import ContentRequest, ContentResult


def test_reopen_preserves_draft_review_and_success(tmp_path):
    path = tmp_path / "content.db"
    store = ContentStore(path)
    store.initialize()
    request = ContentRequest(task_id=uuid4(), topic="绿萝养护")
    key = uuid4()
    job, _ = store.submit(request, key, "caller", ready=True)
    store.claim()
    draft = Draft(script="观察土壤后再浇水", title="绿萝养护", tags=("绿萝",))
    outcome = RunOutcome(
        context=RunContext(task_id=request.task_id, job_id=job.root.job_id, topic=request.topic),
        result=ContentResult(**draft.model_dump(), review_passed=True), error=None, retrievals=(),
        rounds=(RoundRecord(round_number=1, draft=draft, reviews=(
            ReviewRecord(capability="content", decision=ReviewDecision(passed=True, issues=()), failure=None),
        )),),
    )
    final = store.finish(outcome)
    reopened = ContentStore(path)
    reopened.initialize()
    assert reopened.interrupt_running() == 0
    assert reopened.submit(request, key, "caller", ready=False) == (final, False)
    assert reopened.inspect(job.root.job_id, "caller").outcome == outcome
    assert reopened.claim() is None


def test_reopen_unknown_call_retains_intent_without_replay(tmp_path):
    path = tmp_path / "content.db"
    store = ContentStore(path)
    store.initialize()
    request = ContentRequest(task_id=uuid4(), topic="绿萝养护")
    job, _ = store.submit(request, uuid4(), "caller", ready=True)
    store.claim()
    call_id = store.begin_call(job.root.job_id, "review_1", "content", '{"draft":"待审稿"}')
    reopened = ContentStore(path)
    reopened.initialize()
    assert reopened.interrupt_running() == 1
    found = reopened.inspect(job.root.job_id, "caller")
    assert found.job.root.error.code == "EXECUTION_INTERRUPTED"
    assert found.calls[0].call_id == call_id
    assert found.calls[0].output_json is None
    assert found.calls[0].stage == "review_1"
    assert reopened.claim() is None
    assert reopened.interrupt_running() == 0
