from __future__ import annotations

from urllib.parse import quote

import services.graph_runner as runner
import services.render_session as render_session
from fastapi import APIRouter, HTTPException
from novel2media import render_state
from pydantic import BaseModel

router = APIRouter()


class RerollRequest(BaseModel):
    shot_id: int
    prompt: str | None = None  # 为空则沿用该 shot 旧提示词


class SelectRequest(BaseModel):
    shot_id: int
    candidate: str  # 必须是该 shot 已有候选之一（绝对路径）


def _file_url(abs_path: str) -> str:
    """绝对路径 → 前端可访问的 /files URL（去掉开头 /，files 端点按 '/'+path resolve）。"""
    return f"/api/files/{quote(abs_path.lstrip('/'))}"


def _build_board(novel_dir: str, chapter_id: str) -> dict:
    """从 render_state 构建前端渲染看板（候选转 URL，按 storyboard_id 数值序）。"""
    data = render_state.load(novel_dir, chapter_id)
    if data is None:
        return {"chapter_id": chapter_id, "shots": [], "all_done": False, "pending": []}
    shots_out = []
    for sid, shot in data.get("shots", {}).items():
        cands = shot.get("candidates", []) or []
        selected = shot.get("selected")
        shots_out.append(
            {
                "storyboard_id": shot.get("storyboard_id", int(sid)),
                "workflow": shot.get("workflow"),
                "prompt": shot.get("prompt", ""),
                "subjects": shot.get("subjects", []),
                "status": shot.get("status", "pending"),
                "error": shot.get("error"),
                "candidates": [
                    {"path": c, "url": _file_url(c)} for c in cands
                ],
                "selected": selected,
                "selected_url": _file_url(selected) if selected else None,
            }
        )
    shots_out.sort(key=lambda s: s["storyboard_id"])
    return {
        "chapter_id": chapter_id,
        "shots": shots_out,
        "all_done": render_state.all_done(data),
        "pending": render_state.pending_shots(data),
    }


async def _get_render_context(run_id: str) -> tuple[str, str]:
    """取该 run 当前渲染章节的 (novel_dir, chapter_id)。

    优先用活跃渲染会话（最准）；无会话时回退 run meta + 当前 interrupt payload。
    """
    session = render_session.get_session(run_id)
    if session is not None:
        return session.novel_dir, session.chapter_id
    # 无会话：从 run meta 拿 novel_dir，从当前状态拿 chapter_id
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    state = await runner.get_current_run_state(run_id)
    interaction = state.get("active_interaction") or {}
    payload = interaction.get("payload") or {}
    chapter_id = payload.get("chapter_id", "")
    if not chapter_id:
        raise HTTPException(status_code=409, detail="run 当前不在图片渲染阶段")
    return meta.novel_dir, chapter_id


@router.get("/runs/{run_id}/render/state")
async def get_render_state(run_id: str):
    """渲染看板：每个换图点的提示词 + 候选图 URL + 选定终图 + 状态。"""
    novel_dir, chapter_id = await _get_render_context(run_id)
    return _build_board(novel_dir, chapter_id)


@router.post("/runs/{run_id}/render/reroll")
async def reroll_shot(run_id: str, req: RerollRequest):
    """改词重抽单张：用（可选新）提示词 + 新随机 seed 追加候选，旧候选保留。"""
    session = render_session.get_session(run_id)
    if session is None:
        raise HTTPException(status_code=409, detail="渲染会话不存在（run 未在渲染阶段）")
    try:
        session.enqueue_reroll(req.shot_id, req.prompt)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.post("/runs/{run_id}/render/select")
async def select_candidate(run_id: str, req: SelectRequest):
    """把某候选设为该 shot 的选定终图。"""
    novel_dir, chapter_id = await _get_render_context(run_id)
    try:
        render_session.select_candidate(novel_dir, chapter_id, req.shot_id, req.candidate)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}
