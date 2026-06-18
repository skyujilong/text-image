from __future__ import annotations
from enum import Enum
from typing import TypedDict


class ChapterStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    EXPORTED = "exported"


class ChapterArtifacts(TypedDict):
    audio_path: str
    subtitles_path: str
    timeline_path: str
    # image_path 已含于 timeline.json 每条记录中，此处不重复存储


class GraphState(TypedDict):
    # 全局配置
    novel_title: str
    novel_dir: str
    worldview: str

    # 角色管理
    characters_profile: dict            # 角色完整档案（唯一真相）
    ignored_characters: list[str]       # 已忽略角色名列表

    # 章节状态与产物（历史章节数据累积存储，支持跨章导出）
    chapters_status: dict[str, str]     # chapter_id → ChapterStatus
    chapters_artifacts: dict[str, ChapterArtifacts]  # chapter_id → 产物路径

    # 当前章节中间状态（load_chapter 时全部重置）
    current_chapter_id: str
    current_chapter_text: str
    current_script: list[dict]
    current_storyboard: list[dict]
    current_audio_path: str
    current_subtitles_path: str
    current_timestamps: list[dict]      # 含全局偏移后时间戳
    current_image_map: dict[str, str]   # storyboard_id → image_path（generate_images 中间结果）
    current_timeline_path: str

    # 审核重试计数器（load_chapter 统一重置）
    script_review_attempts: int
    storyboard_review_attempts: int

    # character_setup_subgraph 内部状态（子图自驱动队列循环）
    setup_queue: list[dict]             # 待设定角色队列，dispatcher 逐个弹出
    setup_current_character: dict       # 当前待处理的单个角色信息
    setup_image_candidates: list[str]   # 当前角色的候选图片路径列表
    setup_voice_candidates: list[dict]  # 当前角色的候选音色列表（seed + 样本路径）

    # detect_new_characters 中间结果
    pending_new_characters: list[dict]  # 待人工决策的新角色列表
