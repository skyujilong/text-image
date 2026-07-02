from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class StartRunRequest(BaseModel):
    novel_dir: str
    novel_title: str = ""
    genre: str = ""
    writing_style: str = ""
    target_audience: str = ""
    core_tone: str = ""
    chapter_word_count: str = ""
    total_word_count: str = ""
    core_theme: str = ""
    world_building: str = ""
    core_conflicts: str = ""
    overall_outline: str = ""
    character_profiles: str = ""
    start_chapter: int = 1
    end_chapter: int | None = None


class ResumeRequest(BaseModel):
    scope: str  # "main" | "plan" | "render"
    thread_id: str
    resume_value: Any


class RestartFromRequest(BaseModel):
    scope: str         # "main" | "plan" | "render"
    checkpoint_id: str # 要回退到的 checkpoint ID
    node: str          # 图内节点名（仅用于日志和校验，实际回退由 checkpoint_id 决定）


class ForkRequest(BaseModel):
    scope: str  # "main" | "plan" | "render"
    # 缺省从 run 最新 checkpoint 分叉；指定则从该历史 checkpoint 分叉
    checkpoint_id: str | None = None


class UpdateRunRequest(BaseModel):
    novel_title: str | None = None


class RunMeta(BaseModel):
    run_id: str
    novel_dir: str
    novel_title: str
    status: Literal["pending", "running", "waiting_human", "done", "error"] = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    params: dict = Field(default_factory=dict)
    # fork 血缘：parent_run_id 为源 run，fork_source_checkpoint_id 为分叉点
    parent_run_id: str | None = None
    fork_source_checkpoint_id: str | None = None


class NarrationPreset(BaseModel):
    """用户自定义解说方案预设（跨 run 持久化，data/narration_presets.json）。"""

    id: str
    name: str
    base_scheme: str = "general"  # 从哪个内置方案改的（resume 时作 narration_scheme）
    adapt_script_template: str
    scene_change_template: str
    created_at: str


class CreateNarrationPresetRequest(BaseModel):
    name: str
    base_scheme: str = "general"
    adapt_script_template: str
    scene_change_template: str


class SSEEvent(BaseModel):
    type: Literal["node_status", "interrupt", "run_complete", "run_error"]
    scope: str | None = None        # "main" | "plan" | "render"
    thread_id: str | None = None    # 该图 thread（回溯/resume 用）
    node_path: str | None = None    # 图内节点路径
    node: str | None = None
    status: str | None = None
    payload: dict[str, Any] | None = None
    message: str | None = None
