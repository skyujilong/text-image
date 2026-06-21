from __future__ import annotations

import json
from pathlib import Path

from langgraph.types import interrupt
from novel2media.chapters import chapter_sort_key
from novel2media.llm import invoke_llm
from novel2media.prompts._parse import parse_json_array
from novel2media.prompts.chapter_prompts import (
    build_adapt_script_prompt,
    build_detect_new_characters_prompt,
    build_generate_storyboard_prompt,
)
from novel2media_logging import get_logger

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

    # 动态发现新章节文件（按 chapter_xxx 数字序，兜底用户中途新增章节）
    known = set(chapters_status.keys())
    for ch_file in sorted(chapters_dir.glob("*.txt"), key=lambda p: chapter_sort_key(p.stem)):
        ch_id = ch_file.stem
        if ch_id not in known:
            chapters_status[ch_id] = "pending"

    # R13：优先恢复 processing（断点续跑），无则取第一个 pending
    processing = sorted(
        [ch_id for ch_id, st in chapters_status.items() if st == "processing"],
        key=chapter_sort_key,
    )
    pending = sorted(
        [ch_id for ch_id, st in chapters_status.items() if st == "pending"],
        key=chapter_sort_key,
    )
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
            "_script_review_decision": "",
            "_script_review_feedback": "",
            "_storyboard_review_decision": "",
            "_storyboard_review_feedback": "",
            "_characters_review_decision": "",
            "_characters_review_feedback": "",
            "_chapter_advance": "",
            "_final_decision": "",
            "_init_characters_review": "",
            "_export_now": False,
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
        "_script_review_decision": "",
        "_script_review_feedback": "",
        "_storyboard_review_decision": "",
        "_storyboard_review_feedback": "",
        "_characters_review_decision": "",
        "_characters_review_feedback": "",
        "_chapter_advance": "",
        "_final_decision": "",
        "_init_characters_review": "",
        "_export_now": False,
    }


# ─── 规划阶段节点（上游 LLM 生成 + 审核 + 推进决策）──────────────────────
# 以下节点为两阶段流程的规划阶段。step 02 仅放置桩实现以跑通图拓扑；
# 真实逻辑在 step 03（LLM 生成）/ step 04（interrupt 审核）填充。


def adapt_script(state: dict) -> dict:
    """LLM 改写口播漫剧脚本 → current_script（不落盘，稿件由 commit_chapter 收入 render_batch）。

    读 current_chapter_text_path 原文 + characters_profile（name-based）。LLM 输出 JSON 数组，
    解析失败抛错暴露。结果存 current_script 供 review_script 展示 + 后续入 render_batch。

    revise 回环时读 _script_review_feedback（review_script 写入）拼进 prompt，用完清空，
    避免串到下一章重写。
    """
    ch_id = state["current_chapter_id"]
    chapter_text = Path(state["current_chapter_text_path"]).read_text(encoding="utf-8")
    characters_profile = state.get("characters_profile", {})
    feedback = state.get("_script_review_feedback", "") or ""

    prompt = build_adapt_script_prompt(chapter_text, characters_profile, feedback)
    resp = invoke_llm(prompt, node="adapt_script", label="adapt_script")
    script = parse_json_array(resp)  # [{"text","action"}]

    # feedback 记录原文（与 prompt_chars 同条，便于核对 revise 意见是否真拼进 prompt）
    log.info("adapt_script: 完成", chapter=ch_id, lines=len(script), feedback=feedback)
    return {"current_script": script, "_script_review_feedback": ""}


def generate_storyboard(state: dict) -> dict:
    """LLM 生成分镜 → current_storyboard（不落盘，稿件由 commit_chapter 收入 render_batch）。

    双输入：current_chapter_text_path 原文（画面细节）+ current_script 口播脚本（节奏/画面角色名）
    + characters_profile。强制首条 scene_change=True。解析失败抛错。
    scene_prompt 字段名与 image_nodes.generate_images 的读取对齐（渲染阶段复用）。

    revise 回环时读 _storyboard_review_feedback（review_storyboard 写入）拼进 prompt，用完清空。
    """
    ch_id = state["current_chapter_id"]
    script = state.get("current_script", [])
    chapter_text = Path(state["current_chapter_text_path"]).read_text(encoding="utf-8")
    characters_profile = state.get("characters_profile", {})
    feedback = state.get("_storyboard_review_feedback", "") or ""

    prompt = build_generate_storyboard_prompt(script, chapter_text, characters_profile, feedback)
    resp = invoke_llm(prompt, node="generate_storyboard", label="generate_storyboard")
    storyboard = parse_json_array(resp)  # [{"storyboard_id","scene_change","text","speaker","scene_prompt"}]
    if storyboard:
        storyboard[0]["scene_change"] = True  # 首条必为新场景

    # feedback 记录原文（与 prompt_chars 同条，便于核对 revise 意见是否真拼进 prompt）
    log.info("generate_storyboard: 完成", chapter=ch_id, shots=len(storyboard), feedback=feedback)
    return {"current_storyboard": storyboard, "_storyboard_review_feedback": ""}


def detect_new_characters_llm(state: dict) -> dict:
    """LLM 检测本章新角色 + 外观 + 三视图提示词 → pending_new_characters（name-based，无 id）。

    读章节原文 + 现有 characters_profile 的 name 集。只输出新角色，不进 setup_queue
    （留给 review_new_characters 审 + commit_chapter 转 setup_queue + batch_upload_tri_view）。
    每个元素必须含 name/appearance/character_trait/visual_trait/tri_view_prompt/tri_view_prompt_cn
    六字段（与 init parse_characters_llm 角色模型一致），缺则抛错。

    revise 回环时读 _characters_review_feedback（review_new_characters 写入）拼进 prompt，用完清空。
    """
    chapter_text = Path(state["current_chapter_text_path"]).read_text(encoding="utf-8")
    existing_names = set(state.get("characters_profile", {}).keys())
    feedback = state.get("_characters_review_feedback", "") or ""

    prompt = build_detect_new_characters_prompt(chapter_text, existing_names, feedback)
    resp = invoke_llm(prompt, node="detect_new_characters_llm", label="detect_new_characters")
    pending = parse_json_array(resp)  # [{"name","appearance","character_trait","visual_trait","tri_view_prompt","tri_view_prompt_cn"}]
    # 字段模型与 init parse_characters_llm 一致（六字段必填，无 id）
    for c in pending:
        for field in ("name", "appearance", "character_trait", "visual_trait", "tri_view_prompt", "tri_view_prompt_cn"):
            if not c.get(field):
                raise ValueError(f"detect_new_characters_llm: 角色缺 {field} 字段: {c}")

    # feedback 记录原文（与 prompt_chars 同条，便于核对 revise 意见是否真拼进 prompt）
    log.info("detect_new_characters_llm: 完成", count=len(pending), feedback=feedback)
    return {"pending_new_characters": pending, "_characters_review_feedback": ""}


def review_chapter(state: dict) -> dict:
    """[已废弃] 旧单点合并审阅节点。

    已由 review_script / review_storyboard / review_new_characters 三处细分审阅 +
    commit_chapter 纯提交节点取代。保留此函数仅为占位避免历史 import 报错，
    不再注册进图。新代码勿用。
    """
    raise NotImplementedError("review_chapter 已拆分为 review_script/review_storyboard/review_new_characters + commit_chapter")


# ─── 细分审阅节点（通用工厂）─────────────────────────────────────────────
# 原单点 review_chapter 拆为三处细分审阅：各自只审本步产物，revise 回到对应生成
# 节点并把指导意见注入该节点 prompt（精准回环，避免一处问题导致整章重写）。
# pass 后由 commit_chapter 统一执行提交副作用（planned/render_batch/setup_queue），
# 保持 R1：interrupt 之后不做写盘副作用。


def _make_review_node(name, payload_type, artifact_key, artifact_field, decision_field, feedback_field):
    """构造一个细分审阅 interrupt 节点。

    参数：
    - name: 节点名（供 LangGraph stream 命名 / 前端分发）
    - payload_type: interrupt payload 的 type 字段（前端 InteractionDispatcher 据此分发）
    - artifact_key: 从 state 取待审产物的 key（current_script / current_storyboard / pending_new_characters）
    - artifact_field: payload 中产物字段名（script / storyboard / new_characters）
    - decision_field / feedback_field: 写回 state 的决策 / 意见字段名

    节点逻辑：interrupt 把本步产物传给前端 → resume {decision, feedback}（兼容旧字符串）→
    revise 写 decision=revise + feedback；pass 写 decision=pass + 清空 feedback；非法值抛错。
    """

    def _node(state: dict) -> dict:
        ch_id = state.get("current_chapter_id", "")
        raw = interrupt(
            {
                "type": payload_type,
                "chapter_id": ch_id,
                artifact_field: state.get(artifact_key, []),
            }
        )

        # 兼容旧字符串 resume 与新对象 resume {decision, feedback}
        if isinstance(raw, dict):
            decision = raw.get("decision")
            feedback = raw.get("feedback", "") or ""
        else:
            decision = raw
            feedback = ""

        if decision == "revise":
            # 记录 feedback 原文（feedback 进 state 的源头，便于排查"为何没拼进 prompt"）
            log.info(f"{name}: 打回重做", chapter=ch_id, feedback=feedback)
            return {decision_field: "revise", feedback_field: feedback}

        if decision != "pass":
            raise ValueError(f"{name}: 非法 resume 值（应为 pass/revise）: {raw!r}")

        log.info(f"{name}: 审核通过", chapter=ch_id)
        # pass 时清空反馈，防上一轮 revise 残留串到下一次重做
        return {decision_field: "pass", feedback_field: ""}

    _node.__name__ = name
    _node.__doc__ = f"interrupt：细分审阅 {name}，resume 为 {{decision, feedback}}。revise 回到对应生成节点并注入 feedback。"
    return _node


# 三个细分审阅节点：分别审剧本 / 分镜 / 新角色，revise 各自回环
review_script = _make_review_node(
    "review_script",
    payload_type="script_review",
    artifact_key="current_script",
    artifact_field="script",
    decision_field="_script_review_decision",
    feedback_field="_script_review_feedback",
)
review_storyboard = _make_review_node(
    "review_storyboard",
    payload_type="storyboard_review",
    artifact_key="current_storyboard",
    artifact_field="storyboard",
    decision_field="_storyboard_review_decision",
    feedback_field="_storyboard_review_feedback",
)
review_new_characters = _make_review_node(
    "review_new_characters",
    payload_type="new_characters_review",
    artifact_key="pending_new_characters",
    artifact_field="new_characters",
    decision_field="_characters_review_decision",
    feedback_field="_characters_review_feedback",
)


def commit_chapter(state: dict) -> dict:
    """章节规划纯提交节点（非 interrupt）。

    三处细分审阅均 pass 后执行原 review_chapter 的 pass 副作用：标当前章
    chapters_status=planned + 把本章 current_script/current_storyboard 收入 render_batch
    （渲染阶段逐章读取，按 chapter_id 合并覆盖）+ 新角色进 setup_queue（交给
    character_setup_subgraph 批量上传三视图）+ 清空 pending_new_characters。

    拆出为独立非 interrupt 节点的原因：R1 要求 interrupt() 之后不做写盘副作用
    （fork/restart 重放会重复执行），提交逻辑必须放在无 interrupt 的节点。
    """
    ch_id = state["current_chapter_id"]
    chapters_status = dict(state.get("chapters_status", {}))
    chapters_status[ch_id] = "planned"
    render_batch = list(state.get("render_batch", []))
    # 按 chapter_id 合并稿件（revise 重写时会覆盖该章旧稿件）
    render_batch = [item for item in render_batch if item.get("chapter_id") != ch_id]
    render_batch.append(
        {
            "chapter_id": ch_id,
            "script": state.get("current_script", []),
            "storyboard": state.get("current_storyboard", []),
        }
    )
    queue = list(state.get("pending_new_characters", []))
    log.info("commit_chapter: 章节规划提交", chapter=ch_id, new_characters=len(queue))
    return {
        "chapters_status": chapters_status,
        "render_batch": render_batch,
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


def configure_audio(state: dict) -> dict:
    """interrupt：配置全局音色参数（单播，整本书一份）。已配则跳过 interrupt 回填，不重填。

    数据存 MainGraphState.audio_config（同名字段冒泡到主图 checkpoint，跨章节/批次持久）。
    - audio_config 非空：直接返回（已配过，回填不重填），流到 render_dispatch。
    - audio_config 空：interrupt 让用户填 voice_type/speed/pitch/volume，resume 写回。
    - resume 缺 voice_type（必填）→ 抛错暴露，不静默接受。
    """
    current = state.get("audio_config") or {}
    if current:
        log.info("configure_audio: 已配置，跳过（回填）")
        return {}
    result = interrupt({"type": "audio_config", "current": current})
    if not isinstance(result, dict) or not result.get("voice_type"):
        raise ValueError(f"configure_audio: 非法 resume 值（缺 voice_type）: {result!r}")
    log.info("configure_audio: 已配置", voice_type=result.get("voice_type"))
    return {"audio_config": result}


# ─── 渲染阶段节点（逐章串行，状态细化：planned→images_done→audio_done→rendered）──
# step 02 桩实现；step 05 接真实 ComfyUI/TTS + 从 render_batch 取稿 + 状态推进。


def render_dispatch(state: dict) -> dict:
    """取下一个 planned 章节，从 render_batch 读 script/storyboard 写入 current_*。

    逐章串行：选取策略 sorted 后取第一个 `planned` 章节，从 render_batch 取该章稿件
    （替代旧版从盘读 storyboard.json）。章节状态经图→音→视频逐步推进至 rendered。

    无 planned（本批全渲染完）→ 清空 render_batch（重新积累下一批），由条件边去 export。
    planned 章节缺 render_batch 稿件属异常，显式抛错不静默跳过。
    """
    chapters_status = dict(state.get("chapters_status", {}))
    planned = sorted([ch for ch, st in chapters_status.items() if st == "planned"])
    if not planned:
        # 无 planned：本批渲染完，清空 render_batch 重新积累，条件边去 export_to_jianying
        log.info("render_dispatch: 无 planned 章节，清空 render_batch，交由条件边去 export")
        return {"current_chapter_id": "", "render_batch": []}

    ch_id = planned[0]
    batch = state.get("render_batch", [])
    item = next((it for it in batch if it.get("chapter_id") == ch_id), None)
    if item is None:
        raise ValueError(
            f"render_dispatch: planned 章节 {ch_id} 在 render_batch 中无稿件（review_chapter 未入?）"
        )
    ch_text_path = str(Path(state["novel_dir"]) / "chapters" / f"{ch_id}.txt")
    storyboard = item.get("storyboard", [])
    script = item.get("script", [])
    log.info("render_dispatch: 选取渲染章节", chapter=ch_id, shots=len(storyboard))
    return {
        "current_chapter_id": ch_id,
        "current_chapter_text_path": ch_text_path,
        "current_script": script,
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

    完成后推进章节状态：planned → images_done（图片阶段完成）。
    """
    ch_id = state["current_chapter_id"]
    log.info("render_generate_images: [占位] 场景图空走通（ComfyUI 接入待补）", chapter=ch_id)
    chapters_status = dict(state.get("chapters_status", {}))
    chapters_status[ch_id] = "images_done"
    return {"current_image_map": {}, "chapters_status": chapters_status}


def render_synthesize_audio(state: dict) -> dict:
    """TTS 音频/字幕生成。当前空走通（无音轨），接入 TTS 时填 timestamps/subtitles/audio。

    空走通返回空 timestamps，后续 render_build_timeline 会生成仅含图的空 timeline。
    完成后推进章节状态：images_done → audio_done（音频阶段完成）。
    """
    ch_id = state["current_chapter_id"]
    log.info("render_synthesize_audio: [占位] TTS 空走通（接入待补）", chapter=ch_id)
    chapters_status = dict(state.get("chapters_status", {}))
    chapters_status[ch_id] = "audio_done"
    return {
        "current_audio_path": "",
        "current_subtitles_path": "",
        "current_timestamps": [],
        "chapters_status": chapters_status,
    }


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

    # merge 写入媒体产物路径（audio/subtitles/timeline），稿件不入 artifacts（在 render_batch）
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
