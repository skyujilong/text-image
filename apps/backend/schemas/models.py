from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class StartRunRequest(BaseModel):
    # source_dir=用户选中的源小说目录（只读）。后端建 run 时 copy 出隔离工作副本。
    # 灰度期兼容旧前端的 novel_dir key：二者取其一即可（见 validator）。
    source_dir: str = ""
    novel_dir: str = ""
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

    @model_validator(mode="after")
    def _require_source(self) -> StartRunRequest:
        """source_dir 缺省时回退旧 novel_dir key；两者皆空则报错。"""
        if not self.source_dir:
            self.source_dir = self.novel_dir
        if not self.source_dir:
            raise ValueError("source_dir is required")
        return self


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
    novel_dir: str  # 每-run 隔离工作副本（产出落此处）
    novel_title: str
    status: Literal["pending", "running", "waiting_human", "done", "error"] = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    params: dict = Field(default_factory=dict)
    # fork 血缘：parent_run_id 为源 run，fork_source_checkpoint_id 为分叉点
    parent_run_id: str | None = None
    fork_source_checkpoint_id: str | None = None
    # 用户选中的源小说目录（只读）。legacy run 无此值（None）。
    source_dir: str | None = None


class AddWorkDirRequest(BaseModel):
    path: str
    label: str = ""


class FsEntry(BaseModel):
    """目录浏览器的一个子目录条目。"""

    name: str
    path: str
    is_novel: bool  # 是否形似小说（含 chapters/*.txt）
    hidden: bool = False


class FsListing(BaseModel):
    path: str
    parent: str
    entries: list[FsEntry]


class NovelEntry(BaseModel):
    """工作目录下扫出的一本小说。"""

    name: str
    path: str
    title: str | None = None
    chapter_count: int = 0


class WorkDirNovels(BaseModel):
    work_dir: str
    novels: list[NovelEntry]


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
