from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field


class StartRunRequest(BaseModel):
    novel_dir: str
    novel_title: str = ""
    worldview: str = ""
    start_chapter: int = 1
    end_chapter: int | None = None


class ResumeRequest(BaseModel):
    resume_value: Any


class RestartFromRequest(BaseModel):
    node_path: str


class RunMeta(BaseModel):
    run_id: str
    novel_dir: str
    novel_title: str
    status: Literal["pending", "running", "waiting_human", "done", "error"] = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    params: dict = Field(default_factory=dict)


class SSEEvent(BaseModel):
    type: Literal["node_status", "run_complete", "run_error"]
    node: str | None = None
    status_key: str | None = None
    status: str | None = None
    payload: dict[str, Any] | None = None
    message: str | None = None
