from __future__ import annotations

import json
from pathlib import Path

from langgraph.types import interrupt

from novel2media.llm import get_llm
from novel2media.logger import get_logger
from novel2media.prompts._parse import parse_json_array
from novel2media.prompts.chapter_prompts import (
    build_adapt_script_prompt,
    build_detect_new_characters_prompt,
    build_generate_storyboard_prompt,
)

log = get_logger("chapter_nodes")

_PENDING_STATUSES = {"pending", "processing"}


def load_chapter(state: dict) -> dict:
    """加载下一章并重置章节级中间态。

    章节选取优先级（R13）：先取 `processing`（恢复断点/续跑），无则取第一个
    `pending` 置 `processing`。无 pending/processing 章节时返回空 current_chapter_id，
    由条件边路由到 END。

    控制字段重置（R3）：fork/resume 残留的 _review_decision/_chapter_advance 等
    路由字段会串扰下一章或新分支路由，此处统一置默认值。
    """
    novel_dir = Path(state["novel_dir"])
    chapters_dir = novel_dir / "chapters"
    chapters_status: dict[str, str] = dict(state.get("chapters_status", {}))

    # 动态发现新章节文件
    known = set(chapters_status.keys())
    for ch_file in sorted(chapters_dir.glob("*.txt")):
        ch_id = ch_file.stem
        if ch_id not in known:
            chapters_status[ch_id] = "pending"

    # R13：优先恢复 processing（断点续跑），无则取第一个 pending
    processing = sorted([ch_id for ch_id, st in chapters_status.items() if st == "processing"])
    pending = sorted([ch_id for ch_id, st in chapters_status.items() if st == "pending"])
    if processing:
        ch_id = processing[0]
        log.info("load_chapter: 恢复 processing 章节（断点续跑）", chapter=ch_id)
    elif pending:
        ch_id = pending[0]
        chapters_status[ch_id] = "processing"
        log.info("load_chapter: 开始处理章节", chapter=ch_id)
    else:
        log.info("load_chapter: 无 pending 章节，流程结束")
        return {
            "chapters_status": chapters_status,
            "current_chapter_id": "",
            "current_chapter_text_path": "",
            "current_script": [],
            "current_storyboard": [],
            "current_audio_path": "",
            "current_subtitles_path": "",
            "current_timestamps": [],
            "current_image_map": {},
            "current_timeline_path": "",
            "script_review_attempts": 0,
            "storyboard_review_attempts": 0,
            # R3：清空控制字段，避免残留串扰
            "_review_decision": "",
            "_chapter_advance": "",
            "_final_decision": "",
            "_export_now": False,
            "_card_selected": False,
            "_manual_review": "",
            "_manual_retry": "",
            "_voice_route": "",
        }

    # 章节原文是不可变源文件，仅存路径；不再把整章文本放进 state（避免每条
    # checkpoint 复制一份）。需要原文时按路径读取。
    ch_text_path = str(chapters_dir / f"{ch_id}.txt")

    return {
        "chapters_status": chapters_status,
        "current_chapter_id": ch_id,
        "current_chapter_text_path": ch_text_path,
        "current_script": [],
        "current_storyboard": [],
        "current_audio_path": "",
        "current_subtitles_path": "",
        "current_timestamps": [],
        "current_image_map": {},
        "current_timeline_path": "",
        "script_review_attempts": 0,
        "storyboard_review_attempts": 0,
        # R3：清空章节级控制字段，防止上一章/上一分支残留驱动本章路由
        "_review_decision": "",
        "_chapter_advance": "",
        "_final_decision": "",
        "_export_now": False,
        "_card_selected": False,
        "_manual_review": "",
        "_manual_retry": "",
        "_voice_route": "",
    }


# ─── 规划阶段节点（上游 LLM 生成 + 审核 + 推进决策）──────────────────────
# 以下节点为两阶段流程的规划阶段。step 02 仅放置桩实现以跑通图拓扑；
# 真实逻辑在 step 03（LLM 生成）/ step 04（interrupt 审核）填充。


def adapt_script(state: dict) -> dict:
    """LLM 改写剧本 → current_script + 落盘 <ch>/script.json + 更新 artifacts.script_path。

    读 current_chapter_text_path 原文 + characters_profile（name-based）。LLM 输出 JSON 数组，
    解析失败抛错暴露。结果同时存 state（供 review 展示）与盘（供渲染阶段读回）。
    """
    ch_id = state["current_chapter_id"]
    chapter_text = Path(state["current_chapter_text_path"]).read_text(encoding="utf-8")
    characters_profile = state.get("characters_profile", {})

    prompt = build_adapt_script_prompt(chapter_text, characters_profile)
    resp = get_llm().invoke(prompt)
    script = parse_json_array(resp)  # [{"speaker","text","action"}]

    script_path = _write_chapter_artifact(state, ch_id, "script.json", script)
    log.info("adapt_script: 完成", chapter=ch_id, lines=len(script))
    return {
        "current_script": script,
        "chapters_artifacts": _with_artifact_path(state, ch_id, "script_path", script_path),
    }


def generate_storyboard(state: dict) -> dict:
    """LLM 生成分镜 → current_storyboard + 落盘 <ch>/storyboard.json + 更新 artifacts.storyboard_path。

    读 current_script + characters_profile。强制首条 scene_change=True。解析失败抛错。
    scene_prompt 字段名与 image_nodes.generate_images 的读取对齐（渲染阶段复用）。
    """
    ch_id = state["current_chapter_id"]
    script = state.get("current_script", [])
    characters_profile = state.get("characters_profile", {})

    prompt = build_generate_storyboard_prompt(script, characters_profile)
    resp = get_llm().invoke(prompt)
    storyboard = parse_json_array(resp)  # [{"storyboard_id","scene_change","text","speaker","scene_prompt"}]
    if storyboard:
        storyboard[0]["scene_change"] = True  # 首条必为新场景

    storyboard_path = _write_chapter_artifact(state, ch_id, "storyboard.json", storyboard)
    log.info("generate_storyboard: 完成", chapter=ch_id, shots=len(storyboard))
    return {
        "current_storyboard": storyboard,
        "chapters_artifacts": _with_artifact_path(state, ch_id, "storyboard_path", storyboard_path),
    }


def detect_new_characters_llm(state: dict) -> dict:
    """LLM 检测本章新角色 + 外观 → pending_new_characters（name-based，无 id）。

    读章节原文 + 现有 characters_profile 的 name 集。只输出新角色，不进 setup_queue
    （留给 review_chapter 审 + upload_tri_view）。每个元素必须含 name 字段，否则抛错。
    """
    chapter_text = Path(state["current_chapter_text_path"]).read_text(encoding="utf-8")
    existing_names = set(state.get("characters_profile", {}).keys())

    prompt = build_detect_new_characters_prompt(chapter_text, existing_names)
    resp = get_llm().invoke(prompt)
    pending = parse_json_array(resp)  # [{"name","appearance"}]
    for c in pending:
        if "name" not in c:
            raise ValueError(f"detect_new_characters_llm: LLM 输出缺少 name 字段: {c}")

    log.info("detect_new_characters_llm: 完成", count=len(pending))
    return {"pending_new_characters": pending}


def _write_chapter_artifact(state: dict, ch_id: str, filename: str, data: object) -> str:
    """把章节产物写入 novel_dir/<ch_id>/<filename>，返回路径字符串。"""
    out_dir = Path(state["novel_dir"]) / ch_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _with_artifact_path(state: dict, ch_id: str, key: str, path: str) -> dict:
    """更新 chapters_artifacts[ch_id][key]=path，返回新的 chapters_artifacts dict。"""
    artifacts = dict(state.get("chapters_artifacts", {}))
    art = dict(artifacts.get(ch_id, {}))
    art[key] = path
    artifacts[ch_id] = art
    return artifacts


def review_chapter(state: dict) -> dict:
    """interrupt：纯审核（剧本+分镜+新角色候选），resume 为 "pass" / "revise"。

    R1 原则：interrupt() 之后不做任何写盘副作用（fork/restart 重放会重复执行）。
    本节点只读 state + 写 state 字段：落盘已由 adapt_script/generate_storyboard 完成。

    - revise：仅写 `_review_decision=revise`，路由回到 adapt_script 重写剧本。
    - pass：标当前章 chapters_status=planned + 把 pending_new_characters 进 setup_queue
      （交给 character_setup_subgraph 逐个上传三视图/配音色）+ 清空 pending_new_characters。
    - resume 值非 pass/revise：显式抛错，不静默当 pass（避免用户决策被吞）。
    """
    ch_id = state["current_chapter_id"]
    decision = interrupt(
        {
            "type": "chapter_review",
            "chapter_id": ch_id,
            "script": state.get("current_script", []),
            "storyboard": state.get("current_storyboard", []),
            "new_characters": state.get("pending_new_characters", []),
        }
    )

    if decision == "revise":
        log.info("review_chapter: 打回重写", chapter=ch_id)
        return {"_review_decision": "revise"}

    if decision != "pass":
        raise ValueError(f"review_chapter: 非法 resume 值（应为 pass/revise）: {decision!r}")

    # pass：标 planned + 新角色进 setup_queue
    chapters_status = dict(state.get("chapters_status", {}))
    chapters_status[ch_id] = "planned"
    queue = list(state.get("pending_new_characters", []))
    log.info("review_chapter: 审核通过", chapter=ch_id, new_characters=len(queue))
    return {
        "_review_decision": "pass",
        "chapters_status": chapters_status,
        "setup_queue": queue,
        "pending_new_characters": [],
    }


def chapter_advance_decision(state: dict) -> dict:
    """interrupt：本章规划完成后选择推进方向，resume 为 "next" / "render"。

    - next：继续规划下一章（load_chapter）。
    - render：当前批次规划完成，进入批量渲染（render_dispatch）。
    - resume 值非 next/render：显式抛错。
    """
    chapters_status = state.get("chapters_status", {})
    planned_count = sum(1 for st in chapters_status.values() if st == "planned")
    choice = interrupt(
        {
            "type": "chapter_advance",
            "chapter_id": state.get("current_chapter_id"),
            "planned_count": planned_count,
        }
    )

    if choice not in ("next", "render"):
        raise ValueError(f"chapter_advance_decision: 非法 resume 值（应为 next/render）: {choice!r}")
    log.info("chapter_advance_decision: 推进决策", chapter=state.get("current_chapter_id"), choice=choice)
    return {"_chapter_advance": choice}


def final_decision(state: dict) -> dict:
    """interrupt：渲染批次导出后选择是否完结，resume 为 "done" / "continue"。

    - done：全部完结 → END。
    - continue：继续规划下一批（load_chapter，支持规划 N 章→渲染→再规划的交错）。
    - resume 值非 done/continue：显式抛错。
    """
    chapters_status = state.get("chapters_status", {})
    exported_count = sum(1 for st in chapters_status.values() if st == "exported")
    remaining_pending = sum(1 for st in chapters_status.values() if st == "pending")
    choice = interrupt(
        {
            "type": "final_decision",
            "exported_count": exported_count,
            "remaining_pending": remaining_pending,
        }
    )

    if choice not in ("done", "continue"):
        raise ValueError(f"final_decision: 非法 resume 值（应为 done/continue）: {choice!r}")
    log.info("final_decision: 最终决策", choice=choice, exported=exported_count, pending=remaining_pending)
    return {"_final_decision": choice}


# ─── 渲染阶段节点（独立子节点，顺序循环）──────────────────────────────────
# step 02 桩实现；step 05 接真实 ComfyUI/TTS + 从盘读 storyboard + 标 rendered。


def render_dispatch(state: dict) -> dict:
    """取下一个 planned 章节，从盘读 storyboard.json 写入 current_*；无 planned → 由条件边去 export。

    选取策略：sorted 后取第一个 `planned` 章节。章节状态保持 `planned` 直到
    render_build_timeline 标 `rendered`（避免与 load_chapter 的 processing 语义冲突，
    也保证 _has_planned 在该章渲染完成后变 False 推进循环）。

    planned 章节缺 storyboard_path（规划阶段未落盘）属异常，显式抛错不静默跳过。
    """
    chapters_status = dict(state.get("chapters_status", {}))
    artifacts = state.get("chapters_artifacts", {})
    planned = sorted([ch for ch, st in chapters_status.items() if st == "planned"])
    if not planned:
        # 无 planned：条件边 _route_render_dispatch 会路由到 export_to_jianying
        log.info("render_dispatch: 无 planned 章节，交由条件边去 export")
        return {"current_chapter_id": ""}

    ch_id = planned[0]
    sb_path = artifacts.get(ch_id, {}).get("storyboard_path")
    if not sb_path:
        raise ValueError(
            f"render_dispatch: planned 章节 {ch_id} 缺 storyboard_path（规划阶段 storyboard 未落盘?）"
        )
    storyboard = json.loads(Path(sb_path).read_text(encoding="utf-8"))
    ch_text_path = str(Path(state["novel_dir"]) / "chapters" / f"{ch_id}.txt")
    log.info("render_dispatch: 选取渲染章节", chapter=ch_id, shots=len(storyboard))
    return {
        "current_chapter_id": ch_id,
        "current_chapter_text_path": ch_text_path,
        "current_storyboard": storyboard,
        "current_image_map": {},
        "current_audio_path": "",
        "current_subtitles_path": "",
        "current_timestamps": [],
    }


def render_generate_images(state: dict) -> dict:
    """场景图生成（ComfyUI，用角色三视图 tri_view 作 reference，不切图）。

    当前为空走通占位：ComfyUI 场景图工作流模板/接入待补（与 image_nodes.generate_images
    的 portrait/fullbody 旧路径解耦）。接入时应读 current_storyboard + characters_profile
    [name].tri_view 作 reference；无 tri_view 的小角色用 appearance 文字兜底。返回空 image_map。
    """
    log.info("render_generate_images: [占位] 场景图空走通（ComfyUI 接入待补）", chapter=state.get("current_chapter_id"))
    return {"current_image_map": {}}


def render_synthesize_audio(state: dict) -> dict:
    """TTS 音频/字幕生成。当前空走通（无音轨），接入 TTS 时填 timestamps/subtitles/audio。

    空走通返回空 timestamps，后续 render_build_timeline 会生成仅含图的空 timeline。
    """
    log.info("render_synthesize_audio: [占位] TTS 空走通（接入待补）", chapter=state.get("current_chapter_id"))
    return {"current_audio_path": "", "current_subtitles_path": "", "current_timestamps": []}


def render_build_timeline(state: dict) -> dict:
    """生成 <ch>/timeline.json + 标 rendered（当前章 chapters_status → rendered）。

    复用 build_timeline 落盘 timeline 与 artifacts，额外把当前章置 `rendered`。
    R8 关键：必须标 rendered，否则 _has_planned 恒真 → 渲染循环死循环 +
    export_to_jianying 找不到可导章节。
    """
    result = build_timeline(state)
    ch_id = state["current_chapter_id"]
    chapters_status = dict(state.get("chapters_status", {}))
    chapters_status[ch_id] = "rendered"
    log.info("render_build_timeline: 标记 rendered", chapter=ch_id)
    return {**result, "chapters_status": chapters_status}



def build_timeline(state: dict) -> dict:
    novel_dir = Path(state["novel_dir"])
    ch_id = state["current_chapter_id"]
    timestamps: list[dict] = state.get("current_timestamps", [])
    image_map: dict[str, str] = state.get("current_image_map", {})

    timeline = []
    for ts in timestamps:
        sid = ts["storyboard_id"]
        timeline.append(
            {
                "storyboard_id": sid,
                "text": ts["text"],
                "speaker": ts["speaker"],
                "start_time": ts["start_time"],
                "end_time": ts["end_time"],
                "image_path": image_map.get(sid, ""),
            }
        )

    out_dir = novel_dir / ch_id
    out_dir.mkdir(parents=True, exist_ok=True)
    timeline_path = out_dir / "timeline.json"
    timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2))

    # merge 保留规划阶段落盘的 script_path/storyboard_path（避免被覆盖丢失）
    artifacts = dict(state.get("chapters_artifacts", {}))
    existing = dict(artifacts.get(ch_id, {}))
    existing.update(
        {
            "audio_path": state.get("current_audio_path", ""),
            "subtitles_path": state.get("current_subtitles_path", ""),
            "timeline_path": str(timeline_path),
        }
    )
    artifacts[ch_id] = existing
    log.info("build_timeline: 完成", chapter=ch_id, entries=len(timeline))
    return {
        "current_timeline_path": str(timeline_path),
        "chapters_artifacts": artifacts,
    }


def export_to_jianying(state: dict) -> dict:
    """导出 status=rendered 章节（增量，R9），置 exported。

    R9：新流程无 done 状态，渲染完成章为 `rendered`；过滤条件必须从 done 改 rendered，
    否则永远找不到可导章节。
    """
    novel_dir = Path(state["novel_dir"])
    chapters_status = dict(state.get("chapters_status", {}))
    chapters_artifacts = state.get("chapters_artifacts", {})

    rendered_chapters = [ch for ch, st in chapters_status.items() if st == "rendered"]
    if not rendered_chapters:
        log.info("export_to_jianying: 无 rendered 章节")
        return {}

    export_data = []
    for ch_id in sorted(rendered_chapters):
        artifact = chapters_artifacts.get(ch_id, {})
        export_data.append({"chapter_id": ch_id, **artifact})
        chapters_status[ch_id] = "exported"

    out_path = novel_dir / "export" / "jianying_draft.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(export_data, ensure_ascii=False, indent=2))

    # 派生 chapters_status.json 只读视图
    status_path = novel_dir / "chapters_status.json"
    status_path.write_text(json.dumps(chapters_status, ensure_ascii=False, indent=2))

    log.info("export_to_jianying: 导出完成", chapters=rendered_chapters)
    return {"chapters_status": chapters_status}
