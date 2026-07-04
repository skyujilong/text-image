"""小说参考资料 · 只读接口（左侧 Sidebar 常驻「章节/世界观/人物」阅读数据源）。

- GET /runs/{run_id}/chapters             逐章文件（stem/number/label，读盘）
- GET /runs/{run_id}/chapters/{stem}/text 某章原文正文（读盘）
- GET /runs/{run_id}/worldview            世界观设定文本（读 checkpoint state）
- GET /runs/{run_id}/characters           人物档案列表（读 state，附三视图立绘绝对路径）

章节走磁盘（run 的 novel_dir 工作副本，内容与原著一致、只读）；世界观/人物走 checkpoint
state（init/setup 阶段写入的 worldview / characters_profile）。
"""

from __future__ import annotations

from pathlib import Path

import services.graph_runner as runner
from fastapi import APIRouter, HTTPException
from novel2media.chapters import (
    ChapterFileInfo,
    chapter_number,
    group_label,
    list_chapter_files,
    read_group_text,
)

router = APIRouter()


async def _novel_dir(run_id: str) -> str:
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    return meta.novel_dir


# ── 章节原文（读盘）──────────────────────────────────────────────────────────


@router.get("/runs/{run_id}/chapters")
async def list_chapters(run_id: str) -> list[ChapterFileInfo]:
    """列本 run 小说的逐章文件，按章序返回 [{stem, number, label}]。"""
    novel_dir = await _novel_dir(run_id)
    return list_chapter_files(novel_dir)


@router.get("/runs/{run_id}/chapters/{stem}/text")
async def read_chapter_text(run_id: str, stem: str) -> dict:
    """读某章原文正文，返回 {stem, number, label, text}。"""
    novel_dir = await _novel_dir(run_id)
    # 路径安全：stem 应为单个文件名段，拒绝分隔符/上跳，再用 resolve 兜底越权。
    if not stem or "/" in stem or "\\" in stem or ".." in stem:
        raise HTTPException(status_code=400, detail="invalid chapter stem")
    chapters_dir = (Path(novel_dir) / "chapters").resolve()
    path = (chapters_dir / f"{stem}.txt").resolve()
    if chapters_dir not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="chapter not found")
    return {
        "stem": stem,
        "number": chapter_number(stem),
        "label": group_label([stem]),
        "text": read_group_text([str(path)]),
    }


# ── 世界观 / 人物（读 state）─────────────────────────────────────────────────


@router.get("/runs/{run_id}/worldview")
async def get_worldview(run_id: str) -> dict:
    """返回本 run 世界观设定文本（未设置则空串）。"""
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    state = await runner.get_run_state_values(run_id)
    return {"worldview": state.get("worldview") or ""}


@router.get("/runs/{run_id}/characters")
async def list_characters(run_id: str) -> list[dict]:
    """返回本 run 人物档案列表（init/setup 写入的 characters_profile）。

    附 `portrait_path`：三视图立绘绝对路径（tri_view 相对 novel_dir 解析且文件存在时），
    供前端用 /files 服务展示立绘；未上传/跳过则为 null。
    """
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    novel_dir = meta.novel_dir
    state = await runner.get_run_state_values(run_id)
    profiles: dict[str, dict] = state.get("characters_profile") or {}

    result: list[dict] = []
    for name, prof in profiles.items():
        tri_view = (prof.get("tri_view") or "").strip()
        portrait_path: str | None = None
        if tri_view:
            abs_path = (Path(novel_dir) / tri_view).resolve()
            if abs_path.is_file():
                portrait_path = str(abs_path)
        result.append(
            {
                "name": prof.get("name") or name,
                "role": prof.get("role") or "main",
                "character_trait": prof.get("character_trait") or "",
                "appearance": prof.get("appearance") or "",
                "outfit": prof.get("outfit") or "",
                "visual_trait": prof.get("visual_trait") or "",
                "tri_view_prompt_cn": prof.get("tri_view_prompt_cn") or "",
                "tri_view_prompt": prof.get("tri_view_prompt") or "",
                "portrait_path": portrait_path,
            }
        )
    return result
