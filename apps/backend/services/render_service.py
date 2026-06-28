"""渲染服务层：封装渲染相关业务逻辑，供 API 端点直接调用。

从图节点提取的纯函数（chapter_nodes.py）+ render_session 管理 + checkpoint state 读取，
组合为后端服务函数，使渲染流程完全脱离图流程。
"""

from __future__ import annotations

import json
from pathlib import Path

from novel2media_logging import get_logger

import services.graph_runner as runner

log = get_logger("render_service")
from novel2media import render_state
from novel2media.nodes.chapter_nodes import (
    export_to_jianying,
    render_build_timeline,
    render_generate_images,
    render_synthesize_audio,
)


async def _get_novel_dir(run_id: str) -> str:
    meta = await runner.get_run(run_id)
    if meta is None:
        raise ValueError(f"run not found: {run_id}")
    return meta.novel_dir


async def _get_shared_state(run_id: str) -> dict:
    """从主图 checkpoint 提取 SharedGraphState 字段。"""
    return await runner.get_run_state_values(run_id)


async def get_render_chapters(run_id: str) -> list[dict]:
    """返回章节列表 + 渲染状态。

    从 checkpoint state 读取 chapters_status + render_batch，合并为前端可消费的章节列表。
    """
    novel_dir = await _get_novel_dir(run_id)
    state = await _get_shared_state(run_id)
    chapters_status: dict[str, str] = state.get("chapters_status", {})
    render_batch: list[dict] = state.get("render_batch", [])

    batch_map = {item.get("chapter_id"): item for item in render_batch}

    chapters = []
    for ch_id in sorted(chapters_status.keys()):
        item = batch_map.get(ch_id, {})
        storyboard = item.get("storyboard", [])
        script = item.get("script", [])
        ch_text_path = str(Path(novel_dir) / "chapters" / f"{ch_id}.txt")
        has_script = bool(script)
        has_storyboard = bool(storyboard)
        chapters.append({
            "chapter_id": ch_id,
            "status": chapters_status.get(ch_id, "pending"),
            "has_script": has_script,
            "has_storyboard": has_storyboard,
            "storyboard_count": len(storyboard),
            "chapter_text_path": ch_text_path,
            "storyboard": storyboard,
        })
    return chapters


async def start_chapter_render(run_id: str, chapter_id: str, force_switch: bool = False) -> dict:
    """启动某章节图片渲染：写 render_state + 启动 RenderSession。

    force_switch: 如果其他章节正在渲染，是否强制切换。
    """
    import services.render_session as render_session

    novel_dir = await _get_novel_dir(run_id)
    state = await _get_shared_state(run_id)
    render_batch: list[dict] = state.get("render_batch", [])
    characters_profile: dict = state.get("characters_profile", {})
    chapters_status: dict[str, str] = dict(state.get("chapters_status", {}))

    # 直接按 chapter_id 查找稿件（render_dispatch 自动选章，这里按需指定）
    item = next((it for it in render_batch if it.get("chapter_id") == chapter_id), None)
    if item is None:
        raise ValueError(f"chapter {chapter_id} not found in render_batch")

    storyboard = item.get("storyboard", [])
    if not storyboard:
        raise ValueError(f"chapter {chapter_id} has empty storyboard")

    # 冲突检测：其他章节正在渲染时，需要 force_switch=True
    active_chapter = render_session.get_active_chapter(run_id)
    if active_chapter is not None and active_chapter != chapter_id:
        if not force_switch:
            return {
                "conflict": True,
                "active_chapter": active_chapter,
                "requested_chapter": chapter_id,
                "message": f"章节 {active_chapter} 正在渲染，设置 force_switch=true 可强制切换",
            }
        # 强制切换：旧会话会被 start_session 内部自动停止

    specs = render_generate_images(novel_dir, chapter_id, storyboard, characters_profile)

    render_session.start_session(
        run_id, novel_dir, chapter_id, specs, runner.push_event
    )

    # 更新章节状态为 rendering，确保刷新页面后渲染看板仍可见
    chapters_status[chapter_id] = "rendering"
    await runner.update_run_state_values(run_id, {"chapters_status": chapters_status})

    return {
        "chapter_id": chapter_id,
        "specs_count": len(specs),
        "session_started": True,
        "ok": True,
        "started": True,
    }


async def synthesize_audio(run_id: str, chapter_id: str, audio_config: dict | None = None) -> dict:
    """提交 TTS 音频合成：调用纯函数合成整章音频并落盘。"""
    log.info(
        "render_service 收到音频合成请求",
        run_id=run_id,
        chapter=chapter_id,
        audio_config=audio_config,
        has_voice_name=audio_config.get("voice_name") if audio_config else None,
    )
    novel_dir = await _get_novel_dir(run_id)
    state = await _get_shared_state(run_id)
    render_batch: list[dict] = state.get("render_batch", [])
    chapters_status: dict[str, str] = dict(state.get("chapters_status", {}))
    chapters_artifacts: dict = dict(state.get("chapters_artifacts", {}))

    item = next((it for it in render_batch if it.get("chapter_id") == chapter_id), None)
    if item is None:
        raise ValueError(f"chapter {chapter_id} not found in render_batch")

    script = item.get("script", [])
    if not script:
        raise ValueError(f"chapter {chapter_id} has empty script")

    # 同步合成函数放到线程池执行，避免阻塞 FastAPI 事件循环
    # TTS 合成可能需要几十秒，直接在 async 函数里调用会卡死整个服务器
    import asyncio
    result = await asyncio.to_thread(
        render_synthesize_audio, novel_dir, chapter_id, script, audio_config
    )

    # 更新章节状态为 audio_done，并保存产物路径
    chapters_status[chapter_id] = "audio_done"
    chapters_artifacts[chapter_id] = result
    await runner.update_run_state_values(
        run_id,
        {
            "chapters_status": chapters_status,
            "chapters_artifacts": chapters_artifacts,
        },
    )

    return result


async def get_audio_status(run_id: str, chapter_id: str) -> dict:
    """查询音频合成状态：检查音频文件是否存在。"""
    novel_dir = await _get_novel_dir(run_id)
    audio_path = Path(novel_dir) / chapter_id / "audio.wav"
    if audio_path.exists():
        return {
            "chapter_id": chapter_id,
            "status": "done",
            "audio_path": str(audio_path),
        }
    return {
        "chapter_id": chapter_id,
        "status": "pending",
        "audio_path": None,
    }


async def build_chapter_timeline(run_id: str, chapter_id: str) -> dict:
    """生成某章节时间轴：从 render_state 提取 image_map + 调用纯函数生成 timeline.json。"""
    novel_dir = await _get_novel_dir(run_id)
    state = await _get_shared_state(run_id)
    chapters_artifacts: dict = state.get("chapters_artifacts", {})

    # 从 render_state 提取 image_map（selected → storyboard_id）
    data = render_state.load(novel_dir, chapter_id)
    if data is None:
        raise ValueError(f"chapter {chapter_id} render_state not found, render images first")

    image_map: dict[str | int, str] = {}
    for sid, shot in data.get("shots", {}).items():
        selected = shot.get("selected")
        if selected:
            image_map[sid] = selected
            image_map[int(sid)] = selected

    # 从 chapters_artifacts 获取音频路径
    artifact = chapters_artifacts.get(chapter_id, {})
    audio_path = artifact.get("audio_path", "")
    timestamps = artifact.get("timestamps", [])

    result = render_build_timeline(
        novel_dir=novel_dir,
        chapter_id=chapter_id,
        image_map=image_map,
        audio_path=audio_path,
        timestamps=timestamps,
        chapters_artifacts=chapters_artifacts,
    )

    # 更新章节状态为 rendered，并保存时间轴路径
    chapters_status: dict[str, str] = dict(state.get("chapters_status", {}))
    chapters_status[chapter_id] = "rendered"
    chapters_artifacts = dict(state.get("chapters_artifacts", {}))
    chapters_artifacts[chapter_id] = {
        **chapters_artifacts.get(chapter_id, {}),
        "timeline_path": result.get("timeline_path"),
    }
    await runner.update_run_state_values(
        run_id,
        {
            "chapters_status": chapters_status,
            "chapters_artifacts": chapters_artifacts,
        },
    )

    return result


async def get_chapter_timeline(run_id: str, chapter_id: str) -> dict:
    """读取某章节的 timeline.json。"""
    novel_dir = await _get_novel_dir(run_id)
    timeline_path = Path(novel_dir) / chapter_id / "timeline.json"
    if not timeline_path.exists():
        return {"chapter_id": chapter_id, "timeline": None}
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    return {"chapter_id": chapter_id, "timeline": timeline}


async def export_draft(run_id: str) -> dict:
    """导出剪映草稿：调用纯函数导出 rendered 章节为 jianying_draft.json。"""
    novel_dir = await _get_novel_dir(run_id)
    state = await _get_shared_state(run_id)
    chapters_status: dict[str, str] = state.get("chapters_status", {})
    chapters_artifacts: dict = state.get("chapters_artifacts", {})

    result = export_to_jianying(novel_dir, chapters_status, chapters_artifacts)
    export_path = str(Path(novel_dir) / "export" / "jianying_draft.json")
    return {
        "export_path": export_path,
        "chapters_status": result.get("chapters_status", chapters_status),
    }
