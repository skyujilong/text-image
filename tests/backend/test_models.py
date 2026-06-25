from schemas.models import ResumeRequest, RunMeta, SSEEvent, StartRunRequest


def test_start_run_request_requires_novel_dir():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        StartRunRequest()  # novel_dir 必填


def test_resume_request_fields():
    r = ResumeRequest(scope="plan", thread_id="run-1::plan", resume_value=2)
    assert r.scope == "plan"
    assert r.thread_id == "run-1::plan"
    assert r.resume_value == 2


def test_run_meta_defaults():
    m = RunMeta(run_id="abc", novel_dir="/tmp", novel_title="X")
    assert m.status == "pending"
    assert m.created_at is not None


def test_sse_event_serialization():
    e = SSEEvent(
        type="node_status", scope="plan", thread_id="run-1::plan",
        node_path="review_script", node="review_script",
        status="waiting_human", payload={"candidates": ["a.png"]}
    )
    assert e.model_dump()["type"] == "node_status"
    assert e.model_dump()["scope"] == "plan"
