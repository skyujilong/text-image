from api.models import StartRunRequest, RunMeta, ResumeRequest, SSEEvent


def test_start_run_request_requires_novel_dir():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        StartRunRequest()  # novel_dir 必填


def test_resume_request_fields():
    r = ResumeRequest(resume_value=2)
    assert r.resume_value == 2


def test_run_meta_defaults():
    m = RunMeta(run_id="abc", novel_dir="/tmp", novel_title="X")
    assert m.status == "pending"
    assert m.created_at is not None


def test_sse_event_serialization():
    e = SSEEvent(type="node_status", node="portrait_selector", status="waiting_human", payload={"candidates": ["a.png"]})
    assert e.model_dump()["type"] == "node_status"
