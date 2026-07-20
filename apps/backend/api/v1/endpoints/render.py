from __future__ import annotations

from urllib.parse import quote

import httpx
import services.graph_runner as runner
import services.render_service as render_service
import services.render_session as render_session
from fastapi import APIRouter, HTTPException
from novel2media import render_planning, render_state
from novel2media_logging import get_logger
from pydantic import BaseModel

log = get_logger("render_api")
router = APIRouter()


class RerollRequest(BaseModel):
    shot_id: int
    chapter_id: str | None = None  # 可选，优先用前端传的，不依赖 active_interaction
    prompt: str | None = None  # 为空则沿用该 shot 旧提示词
    orientation: str | None = None  # 画幅朝向 landscape/portrait/square；为空沿用旧值（非法值忽略）
    edit_model: str | None = None  # edit 底模档 4step/8step；为空沿用旧值（非法值忽略）


class SelectRequest(BaseModel):
    shot_id: int
    chapter_id: str | None = None  # 可选，优先用前端传的
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
                "edit_model": shot.get("edit_model", "4step"),
                "orientation": shot.get("orientation", "square"),
                "prompt": shot.get("prompt", ""),
                "subjects": shot.get("subjects", []),
                "status": shot.get("status", "pending"),
                "error": shot.get("error"),
                "candidates": [{"path": c, "url": _file_url(c)} for c in cands],
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


async def _resolve_render_payload(run_id: str) -> tuple[str, dict]:
    """取该 run 当前 image_render interrupt 的 (novel_dir, payload)。

    payload 是节点传给 interrupt() 的原始 dict（含 type/chapter_id/storyboard/specs），
    随 checkpoint 持久化——后端重启后仍可从 get_current_run_state 解析出来，是惰性重建
    渲染会话的依据。
    """
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    state = await runner.get_current_run_state(run_id)
    interaction = state.get("active_interaction") or {}
    payload = interaction.get("payload") or {}
    if payload.get("type") != "image_render" or not payload.get("chapter_id"):
        raise HTTPException(status_code=409, detail="run 当前不在图片渲染阶段")
    return meta.novel_dir, payload


async def _ensure_render_session(run_id: str, chapter_id: str | None = None):
    """取渲染会话；不存在则从持久层惰性重建（后端重启 / 内存会话丢失后的恢复路径）。

    会话是纯内存对象，仅在 graph_runner 解析到新 image_render interrupt 时创建。若后端在
    某 run 停在渲染阶段时重启，_sessions 为空且不会再触发 astream → worker 永不重启、
    pending shot 不出图、reroll 409、完成按钮 disabled → 死锁。此处用 checkpoint payload
    + run_meta 重建会话（start_session 内部 seed_pending 会跳过已 done、复位孤立 rendering，
    重建幂等不重跑），用户一打开渲染页或重抽即自动续跑喂 GPU。

    Args:
        run_id: run ID
        chapter_id: 可选，优先用前端传的章节 ID（推荐，因为 active_interaction 可能已清空）
                    如果不传，则尝试从 active_interaction / render_batch 自动推断

    返回 None 表示 run 当前不在渲染阶段（调用方据此决定是否 409）。
    """
    session = render_session.get_session(run_id)
    if session is not None:
        if chapter_id is None or session.chapter_id == chapter_id:
            return session
        log.info(
            "_ensure_render_session: 切换章节重建会话",
            run_id=run_id,
            old_chapter=session.chapter_id,
            new_chapter=chapter_id,
        )

    meta = await runner.get_run(run_id)
    if meta is None or not meta.novel_dir:
        log.warning("_ensure_render_session: meta 不存在或 novel_dir 为空", run_id=run_id)
        return None
    state = await runner.get_current_run_state(run_id)

    # 策略 0：优先用前端传过来的 chapter_id（最准，因为用户明确选了某章节）
    storyboard = None
    specs = None
    if chapter_id:
        render_batch: list[dict] = state.get("render_batch", [])
        for item in render_batch:
            if item.get("chapter_id") == chapter_id:
                storyboard = item.get("storyboard", [])
                characters_profile: dict = state.get("characters_profile", {})
                specs = render_planning.build_shot_specs(storyboard, characters_profile, meta.novel_dir)
                log.info(
                    "_ensure_render_session: 从参数 chapter_id 恢复渲染会话",
                    run_id=run_id,
                    chapter_id=chapter_id,
                    storyboard_len=len(storyboard) if storyboard else 0,
                )
                break
        if not storyboard:
            # render_batch 中找不到，但 render_state 文件可能已存在（后端重启等情况）
            # 尝试直接从 render_state 读取 specs 信息（reroll 只需要 render_state）
            data = render_state.load(meta.novel_dir, chapter_id)
            if data is not None and data.get("shots"):
                # 从 render_state 重建 specs：只需要 storyboard_id 和 prompt
                specs = []
                for sid, shot in data["shots"].items():
                    specs.append(
                        {
                            "storyboard_id": int(sid),
                            "prompt": shot.get("prompt", ""),
                            "workflow": shot.get("workflow", "qwen_t2i"),
                            "edit_model": shot.get("edit_model", "4step"),
                            "orientation": shot.get("orientation", "square"),
                            "ref_images": shot.get("ref_images", []),
                            "scene_id": shot.get("scene_id", ""),
                        }
                    )
                log.info(
                    "_ensure_render_session: 从 render_state 文件恢复渲染会话",
                    run_id=run_id,
                    chapter_id=chapter_id,
                    shots_count=len(specs),
                )
            else:
                log.warning(
                    "_ensure_render_session: 参数 chapter_id 不在 render_batch 中且无 render_state",
                    run_id=run_id,
                    chapter_id=chapter_id,
                )
                return None
    else:
        # 策略 1：从 active_interaction 取（原来的逻辑，渲染阶段进行中）
        interaction = state.get("active_interaction") or {}
        payload = interaction.get("payload") or {}
        chapter_id = payload.get("chapter_id")
        if payload.get("type") == "image_render" and chapter_id:
            storyboard = payload.get("storyboard", [])
            log.info(
                "_ensure_render_session: 从 active_interaction 恢复渲染会话",
                run_id=run_id,
                chapter_id=chapter_id,
            )
        else:
            # 策略 2：active_interaction 没了（可能渲染完了 resume 过），从 render_batch 找第一个
            render_batch: list[dict] = state.get("render_batch", [])
            chapters_status: dict[str, str] = state.get("chapters_status", {})
            log.info(
                "_ensure_render_session: 尝试从 render_batch 恢复",
                run_id=run_id,
                render_batch_size=len(render_batch),
                chapters_status_keys=list(chapters_status.keys()),
            )
            for item in render_batch:
                ch_id = item.get("chapter_id")
                if not ch_id:
                    continue
                status = chapters_status.get(ch_id, "")
                if status in ("rendering", "images_done", "audio_done", "rendered"):
                    chapter_id = ch_id
                    storyboard = item.get("storyboard", [])
                    log.info(
                        "_ensure_render_session: 从 chapters_status 恢复渲染会话",
                        run_id=run_id,
                        chapter_id=chapter_id,
                        status=status,
                        storyboard_len=len(storyboard) if storyboard else 0,
                    )
                    break

    if not chapter_id or (not storyboard and not specs):
        log.warning(
            "_ensure_render_session: 无法恢复会话，返回 None",
            run_id=run_id,
            chapter_id=chapter_id,
            has_storyboard=bool(storyboard),
            has_specs=bool(specs),
        )
        return None

    # specs 随 checkpoint 持久化；缺失（异常态）则从分镜重新解析，保证 seed 不空
    if specs is None:
        specs = render_planning.build_shot_specs(
            storyboard,
            state.get("characters_profile", {}) if isinstance(state, dict) else {},
            meta.novel_dir,
        )
    return render_session.start_session(run_id, meta.novel_dir, chapter_id, specs, runner.push_event)


async def _get_render_context(run_id: str) -> tuple[str, str]:
    """取该 run 当前渲染章节的 (novel_dir, chapter_id)。

    优先用活跃渲染会话（最准）；无会话时回退 run meta + 当前 interrupt payload。
    """
    session = render_session.get_session(run_id)
    if session is not None:
        return session.novel_dir, session.chapter_id
    novel_dir, payload = await _resolve_render_payload(run_id)
    return novel_dir, payload["chapter_id"]


@router.get("/runs/{run_id}/render/chapter/{chapter_id}/state")
async def get_render_state_by_chapter(run_id: str, chapter_id: str):
    """渲染工作台专用：获取指定章节的渲染状态。

    会话不存在时，直接读 render_state 文件（已渲染完但会话已销毁的场景）。
    """
    session = await _ensure_render_session(run_id, chapter_id)
    if session is not None:
        return _build_board(session.novel_dir, session.chapter_id)
    # 会话不存在：直接读文件（已渲染完但会话已销毁的场景）
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _build_board(meta.novel_dir, chapter_id)


@router.get("/runs/{run_id}/render/state")
async def get_render_state_legacy(run_id: str):
    """旧接口（兼容 interrupt 流程）：返回当前活跃会话的渲染状态。"""
    session = await _ensure_render_session(run_id)
    if session is not None:
        return _build_board(session.novel_dir, session.chapter_id)
    # 不在渲染阶段（无法重建）：回退按 payload 取上下文，取不到则 409
    novel_dir, chapter_id = await _get_render_context(run_id)
    return _build_board(novel_dir, chapter_id)


@router.post("/runs/{run_id}/render/reroll")
async def reroll_shot(run_id: str, req: RerollRequest):
    """改词重抽单张：用（可选新）提示词 + 新随机 seed 追加候选，旧候选保留。

    会话不存在时先惰性重建（后端重启后也能重抽），重建不出则 409。
    优先用 req.chapter_id 恢复会话（前端知道当前选的是哪个章节）。
    """
    session = await _ensure_render_session(run_id, req.chapter_id)
    if session is None:
        raise HTTPException(status_code=409, detail="渲染会话不存在（run 未在渲染阶段）")
    try:
        session.enqueue_reroll(req.shot_id, req.prompt, req.orientation, req.edit_model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@router.post("/runs/{run_id}/render/select")
async def select_candidate(run_id: str, req: SelectRequest):
    """把某候选设为该 shot 的选定终图。

    优先用 req.chapter_id（前端知道当前选的是哪个章节），不依赖 active_interaction。
    """
    # 先尝试用会话里的章节（如果有的话）
    session = await _ensure_render_session(run_id, req.chapter_id)
    if session is not None:
        novel_dir, chapter_id = session.novel_dir, session.chapter_id
    else:
        # 会话也创建不了的话，直接从 meta 获取 novel_dir，假设 chapter_id 是传过来的
        meta = await runner.get_run(run_id)
        if meta is None or not meta.novel_dir:
            raise HTTPException(status_code=404, detail="run not found")
        novel_dir = meta.novel_dir
        chapter_id = req.chapter_id
        if not chapter_id:
            raise HTTPException(status_code=409, detail="渲染会话不存在，请传入 chapter_id")

    try:
        render_session.select_candidate(novel_dir, chapter_id, req.shot_id, req.candidate)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


# ── 渲染工作台 API（Step 6 新增） ──────────────────────────────────────


class AudioRequest(BaseModel):
    language: str | None = None
    guidance_scale: float | None = None
    speaker_scale: float | None = None
    voice_name: str | None = None


@router.get("/runs/{run_id}/render/chapters")
async def get_render_chapters(run_id: str):
    """渲染工作台：返回章节列表 + 渲染状态。"""
    try:
        chapters = await render_service.get_render_chapters(run_id)
        return {"chapters": chapters}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/runs/{run_id}/render/chapter/{ch_id}/start")
async def start_chapter_render(run_id: str, ch_id: str, force_switch: bool = False):
    """启动某章节图片渲染：写 render_state + 启动 RenderSession。

    force_switch: 如果其他章节正在渲染，是否强制切换。
    """
    try:
        result = await render_service.start_chapter_render(run_id, ch_id, force_switch)
        if result.get("conflict"):
            # 冲突时返回 409，让前端弹窗确认
            raise HTTPException(status_code=409, detail=result.get("message"))
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/runs/{run_id}/render/chapter/{ch_id}/audio")
async def synthesize_audio(run_id: str, ch_id: str, req: AudioRequest):
    """提交 TTS 音频合成。

    TTS 服务不可达/超时/合成失败时映射为 502/504 + 明确 detail，让前端看到具体原因，
    而非裸 500（客户端已对瞬时抖动做重试+指数回退，落到这里的多为持续性故障）。
    """
    try:
        result = await render_service.synthesize_audio(run_id, ch_id, req.model_dump())
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except (httpx.RequestError, TimeoutError) as e:
        # 网络不可达 / job 等待超时
        raise HTTPException(
            status_code=504, detail=f"TTS 服务超时或网络不可达: {e}"
        ) from e
    except RuntimeError as e:
        # submit/轮询/下载重试耗尽、job failed/cancelled、状态接口持续失败
        raise HTTPException(status_code=502, detail=f"TTS 合成失败: {e}") from e


@router.get("/runs/{run_id}/render/chapter/{ch_id}/audio")
async def get_audio_status(run_id: str, ch_id: str):
    """查询音频合成状态 / 下载。"""
    try:
        result = await render_service.get_audio_status(run_id, ch_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/runs/{run_id}/render/chapter/{ch_id}/timeline")
async def build_chapter_timeline(run_id: str, ch_id: str):
    """生成某章节时间轴。"""
    try:
        result = await render_service.build_chapter_timeline(run_id, ch_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/runs/{run_id}/render/chapter/{ch_id}/timeline")
async def get_chapter_timeline(run_id: str, ch_id: str):
    """获取某章节时间轴数据。"""
    try:
        result = await render_service.get_chapter_timeline(run_id, ch_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/runs/{run_id}/render/export")
async def export_draft(run_id: str):
    """导出剪映草稿。"""
    try:
        result = await render_service.export_draft(run_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/runs/{run_id}/render/chapter/{ch_id}/preview")
async def get_render_preview(run_id: str, ch_id: str):
    """渲染预览：只读返回分镜规格信息，不触发渲染会话。

    用于用户打开渲染工作台时的初始展示，不自动启动 GPU 渲染。
    返回每个换图点的 storyboard_id、workflow、prompt、subjects 等规格信息，
    但不包含候选图（除非已有 render_state 文件）。
    """

    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")

    # 先尝试读取已有 render_state（如果之前已经渲染过）
    existing_state = render_state.load(meta.novel_dir, ch_id)
    if existing_state is not None:
        # 已有渲染状态，直接返回（带候选图）
        return _build_board(meta.novel_dir, ch_id)

    # 尚无渲染状态：从 render_batch 读取分镜规格
    state = await runner.get_run_state_values(run_id)
    render_batch: list[dict] = state.get("render_batch", [])
    characters_profile: dict = state.get("characters_profile", {})

    item = next((it for it in render_batch if it.get("chapter_id") == ch_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail=f"chapter {ch_id} not found")

    storyboard = item.get("storyboard", [])
    if not storyboard:
        raise HTTPException(status_code=400, detail=f"chapter {ch_id} has empty storyboard")

    # 构建 shot specs（只含规格信息，无候选图）
    from novel2media import render_planning

    specs = render_planning.build_shot_specs(storyboard, characters_profile, meta.novel_dir)
    shots_out = []
    for spec in specs:
        shots_out.append(
            {
                "storyboard_id": spec.get("storyboard_id"),
                "workflow": spec.get("workflow"),
                "edit_model": spec.get("edit_model", "4step"),
                "orientation": spec.get("orientation", "square"),
                "prompt": spec.get("prompt", ""),
                "subjects": spec.get("subjects", []),
                "status": "pending",
                "error": None,
                "candidates": [],
                "selected": None,
                "selected_url": None,
            }
        )
    shots_out.sort(key=lambda s: s["storyboard_id"])
    return {
        "chapter_id": ch_id,
        "shots": shots_out,
        "all_done": False,
        "pending": [s["storyboard_id"] for s in shots_out],
    }
