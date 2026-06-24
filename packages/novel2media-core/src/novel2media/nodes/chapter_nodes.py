from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langgraph.types import interrupt
from novel2media.chapters import chapter_sort_key
from novel2media.llm import invoke_llm
from novel2media.nodes.init_nodes import _REQUIRED_CHAR_FIELDS
from novel2media.prompts._parse import parse_json_array
from novel2media.prompts.chapter_prompts import (
    _SCENE_STYLE_TRIGGER,
    build_adapt_script_prompt,
    build_detect_new_characters_prompt,
    build_scene_change_prompt,
    build_scene_prompt_for_shots,
)
from novel2media_logging import get_logger

log = get_logger("chapter_nodes")

_PENDING_STATUSES = {"pending", "processing"}

# 分镜第二步「画面生成」分批参数：换图点过多时按批并行调 LLM，避免单次输出过长被截断。
_SCENE_PROMPT_BATCH_SIZE = 12  # 每批最多多少个换图点
_SCENE_PROMPT_MAX_WORKERS = 2  # 并发上限（控制 ARK 限流压力，不宜过大）


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

    只产口播脚本：新角色检测拆为独立节点 detect_new_characters_llm（放分镜之前）——合并到
    本节点会让单次输出过长撞 output token 上限被截断（实测长章节 finish_reason=length → JSON 断裂）。

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
    resp = invoke_llm(prompt, node="adapt_script", label="adapt_script", json_mode=True)
    script = parse_json_array(resp)  # [{"text","action","speaker"}]

    # feedback 记录原文（便于核对 revise 意见是否真拼进 prompt）
    log.info("adapt_script: 完成", chapter=ch_id, lines=len(script), feedback=feedback)
    return {"current_script": script, "_script_review_feedback": ""}


def _collect_shots(skeleton: list[dict], script: list[dict]) -> list[dict]:
    """从骨架收集换图点，为每个换图点算 coverage（覆盖到下一换图点之间的剧情）。

    每个 shot = {anchor_id, text, coverage}：
    - anchor_id：换图点的 storyboard_id（用于第二步结果对回）。
    - text：换图点本条口播文案。
    - coverage：从本换图点到下一个换图点之间所有条目的 text + 对应 script action 拼接，
      让 LLM 知道这张图要覆盖哪几句剧情，画面信息更完整。
    """
    # 先找出所有换图点下标
    change_indices = [i for i, e in enumerate(skeleton) if e.get("scene_change")]
    shots: list[dict] = []
    for pos, idx in enumerate(change_indices):
        # 本换图点覆盖到下一个换图点之前（最后一个换图点覆盖到结尾）
        next_idx = change_indices[pos + 1] if pos + 1 < len(change_indices) else len(skeleton)
        parts: list[str] = []
        for j in range(idx, next_idx):
            text = skeleton[j].get("text", "")
            action = script[j].get("action", "") if j < len(script) else ""
            seg = text if not action else f"{text}（{action}）"
            if seg:
                parts.append(seg)
        shots.append(
            {
                "anchor_id": skeleton[idx]["storyboard_id"],
                "text": skeleton[idx].get("text", ""),
                "coverage": " ".join(parts),
            }
        )
    return shots


def _batch_shots(shots: list[dict], batch_size: int) -> list[list[dict]]:
    """把换图点列表按 batch_size 切成多批，供第二步并行处理。"""
    return [shots[i : i + batch_size] for i in range(0, len(shots), batch_size)]


def generate_storyboard(state: dict) -> dict:
    """LLM 两步生成分镜 → current_storyboard（不落盘，稿件由 commit_chapter 收入 render_batch）。

    两步法（避免一次性生成全部 scene_prompt 导致输出 token 截断）：
    - 第一步「初筛」：LLM 只判定每条口播是否换图点（输出布尔数组，输出量极小，串行单次）。
    - 第二步「画面生成」：只为换图点生成 subjects + scene_prompt（非换图点下游复用前图、
      不读 scene_prompt，从源头省去无用输出）；换图点过多时按批并行兜底。

    text/speaker 由节点从 script 对位填充（不让 LLM 重复输出，杜绝改字/错位）。
    强制整章首条 scene_change=True。解析失败 / 第一步布尔数组长度不符直接抛错暴露。

    revise 回环时读 _storyboard_review_feedback（review_storyboard 写入）拼进两步 prompt，用完清空。
    """
    ch_id = state["current_chapter_id"]
    script = state.get("current_script", [])
    chapter_text = Path(state["current_chapter_text_path"]).read_text(encoding="utf-8")
    characters_profile = state.get("characters_profile", {})
    feedback = state.get("_storyboard_review_feedback", "") or ""

    if not script:
        log.info("generate_storyboard: 空脚本，跳过", chapter=ch_id)
        return {"current_storyboard": [], "_storyboard_review_feedback": ""}

    # ---- 第一步：初筛换图点（串行单次，输出换图点下标列表）----
    sc_prompt = build_scene_change_prompt(script, chapter_text, feedback)
    sc_resp = invoke_llm(sc_prompt, node="generate_storyboard", label="storyboard_scene_change", json_mode=True)
    raw_indices = parse_json_array(sc_resp)
    n_script = len(script)
    # 输出已从「等长布尔数组」改为「换图点下标列表」：模型不再需要逐条铺满 N 个 bool，
    # 从根上消除「数组长度对不上」的崩溃。这里只校验下标合法性（整数、在范围内），
    # 越界/非整数直接抛错暴露，不静默丢弃（否则会与 script 错位、污染音频/字幕对齐）。
    change_set: set[int] = set()
    for v in raw_indices:
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError(f"换图点初筛结果含非整数下标: {v!r}（应为 0~{n_script - 1} 的整数）")
        if v < 0 or v >= n_script:
            raise ValueError(f"换图点初筛结果下标越界: {v}（口播共 {n_script} 条，合法范围 0~{n_script - 1}）")
        change_set.add(v)

    # ---- 组装骨架：text/speaker 从 script 对位填充，scene_change 取初筛下标集 ----
    skeleton: list[dict] = []
    for i, item in enumerate(script):
        skeleton.append(
            {
                "storyboard_id": i,  # 0-based 全局连续整数
                "scene_change": i in change_set,
                "text": item.get("text", ""),
                "speaker": item.get("speaker", ""),
                "subjects": [],
                "scene_prompt": "",
            }
        )
    skeleton[0]["scene_change"] = True  # 整章首条必为换图点

    # ---- 第二步：只为换图点生成 subjects + scene_prompt（分批并行）----
    shots = _collect_shots(skeleton, script)
    batches = _batch_shots(shots, _SCENE_PROMPT_BATCH_SIZE)
    n = len(batches)

    def _run_batch(args: tuple[int, list[dict]]) -> list[dict]:
        idx, batch = args
        batch_info = (idx + 1, n) if n > 1 else None
        prompt = build_scene_prompt_for_shots(
            batch, chapter_text, characters_profile, feedback, batch_info=batch_info
        )
        resp = invoke_llm(
            prompt, node="generate_storyboard", label=f"storyboard_scene_prompt[{idx + 1}/{n}]", json_mode=True
        )
        return parse_json_array(resp)

    # 收集所有批次的 {anchor_id -> {subjects, scene_prompt}}；任一批抛错经 result() 重新抛出（不吞错）
    results: list[list[dict]] = []
    with ThreadPoolExecutor(max_workers=min(n, _SCENE_PROMPT_MAX_WORKERS)) as executor:
        results = list(executor.map(_run_batch, list(enumerate(batches))))

    shot_by_id: dict[int, dict] = {}
    for batch_result in results:
        for shot in batch_result:
            shot_by_id[shot["anchor_id"]] = shot

    # ---- 回填换图点画面 + 后处理（scene_prompt 头尾拼接只对换图点）----
    for entry in skeleton:
        if not entry["scene_change"]:
            continue  # 非换图点保持 subjects=[]、scene_prompt=""，下游复用前图
        sid = entry["storyboard_id"]
        shot = shot_by_id.get(sid)
        if shot is None:
            # 换图点缺失第二步结果：记录暴露，不伪造画面
            log.warning("generate_storyboard: 换图点缺少画面生成结果", chapter=ch_id, sid=sid)
            continue
        subjects = shot.get("subjects", [])
        entry["subjects"] = subjects
        if isinstance(subjects, list) and len(subjects) > 2:
            # subjects 是后续生图按名取参考图的依据；当前只记录 LLM 违规，不裁剪不伪装成功。
            log.warning("generate_storyboard: 主体角色超 2 人（违反一致性上限）", chapter=ch_id, sid=sid, subjects=subjects)
        raw_prompt = (shot.get("scene_prompt") or "").strip()
        if not raw_prompt:
            # 换图点拿到结果但画面描述为空：记录暴露，不拼成只有触发词的退化 prompt 蒙混生图
            log.warning("generate_storyboard: 换图点画面描述为空", chapter=ch_id, sid=sid)
            continue
        # 画风触发词由代码统一拼接到末尾（LLM 不写画风/画质/解剖词）；
        # 画质与人体结构交给 Qwen-Image-Edit 自身，不在正向 prompt 堆解剖词。
        entry["scene_prompt"] = f"{raw_prompt}, {_SCENE_STYLE_TRIGGER}"

    shots_count = len(shots)
    log.info(
        "generate_storyboard: 完成",
        chapter=ch_id,
        shots=len(skeleton),
        change_points=shots_count,
        batches=n,
        feedback=feedback,
    )
    return {"current_storyboard": skeleton, "_storyboard_review_feedback": ""}


def detect_new_characters_llm(state: dict) -> dict:
    """LLM 检测本章新角色 → 直接写 setup_queue（独立节点，放分镜之前）。

    单独成节点而非并入 adapt_script：合并后单次输出过长撞 output token 上限被截断
    （实测长章节 finish_reason=length → JSON 断裂）。故拆开各自保持输出小。

    放在 review_script 之后、generate_storyboard 之前：检测出的新角色直接进 setup_queue，
    由 character_setup_subgraph 上传三视图（无单独人工审阅），在分镜前备好 visual_trait，
    避免后期图生图角色对不上。

    读章节原文 + 现有 characters_profile 的 name 集（作排除名单）。每个新角色必须含
    六字段（_REQUIRED_CHAR_FIELDS，与 init parse_characters_llm 角色模型一致），缺则抛错；
    防御性剔除名字已在已知花名册中的角色。

    setup_queue 无 reducer（覆盖语义）：review_script revise 回环 → adapt_script → 本节点
    重跑时整体覆盖，不会重复累积/残留旧批新角色。
    """
    ch_id = state["current_chapter_id"]
    chapter_text = Path(state["current_chapter_text_path"]).read_text(encoding="utf-8")
    existing_names = set(state.get("characters_profile", {}).keys())

    prompt = build_detect_new_characters_prompt(chapter_text, existing_names)
    resp = invoke_llm(prompt, node="detect_new_characters_llm", label="detect_new_characters", json_mode=True)
    detected = parse_json_array(resp)  # [{"name","appearance","character_trait","visual_trait","tri_view_prompt","tri_view_prompt_cn"}]

    # 校验六字段（与 init parse_characters_llm 同一真相），剔除已知角色后写 setup_queue
    validated: list[dict] = []
    for c in detected:
        name = c.get("name")
        if name in existing_names:
            # 防御性剔除：已知角色不应再被当新角色（LLM 偶发重复），跳过不入队
            log.warning("detect_new_characters_llm: LLM 误报已知角色为新角色，已剔除", chapter=ch_id, name=name)
            continue
        for field in _REQUIRED_CHAR_FIELDS:
            if not c.get(field):
                raise ValueError(f"detect_new_characters_llm: 新角色缺 {field} 字段: {c}")
        validated.append(c)

    log.info("detect_new_characters_llm: 完成", chapter=ch_id, new_characters=len(validated))
    return {"setup_queue": validated}


def review_chapter(state: dict) -> dict:
    """[已废弃] 旧单点合并审阅节点。

    已由 review_script / review_storyboard 两处细分审阅 + commit_chapter 纯提交节点取代
    （新角色审阅已并入 adapt_script + character_setup_subgraph，不再单独 review）。
    保留此函数仅为占位避免历史 import 报错，不再注册进图。新代码勿用。
    """
    raise NotImplementedError("review_chapter 已拆分为 review_script/review_storyboard + commit_chapter")


# ─── 细分审阅节点（通用工厂）─────────────────────────────────────────────
# 原单点 review_chapter 拆为细分审阅：各自只审本步产物，revise 回到对应生成
# 节点并把指导意见注入该节点 prompt（精准回环，避免一处问题导致整章重写）。
# 新角色不再单独审阅（已并入 adapt_script 产出 + character_setup_subgraph 上传三视图触点）。
# pass 后由 commit_chapter 统一执行提交副作用（planned/render_batch），
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


# 两个细分审阅节点：分别审剧本 / 分镜，revise 各自回环
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


def commit_chapter(state: dict) -> dict:
    """章节规划纯提交节点（非 interrupt）。

    细分审阅均 pass 后执行提交副作用：标当前章 chapters_status=planned + 把本章
    current_script/current_storyboard 收入 render_batch（渲染阶段逐章读取，按 chapter_id 合并覆盖）。

    新角色不在此处理：已在 adapt_script 产出并写入 setup_queue，由 review_script pass 后的
    character_setup_subgraph（在分镜之前）批量上传三视图并落 characters_profile，故 commit
    不再碰 setup_queue / pending_new_characters。

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
    log.info("commit_chapter: 章节规划提交", chapter=ch_id)
    return {
        "chapters_status": chapters_status,
        "render_batch": render_batch,
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
    """interrupt：配置全局合成参数（dots.tts 单播，整本书一份）。已配则跳过 interrupt 回填。

    数据存 MainGraphState.audio_config（同名字段冒泡到主图 checkpoint，跨章节/批次持久）。
    收集 dots.tts 生成旋钮（language/guidance_scale/speaker_scale）与音色（voice_name），均可选，
    缺省走 services.json 默认；voice_name 缺省则用 dots 默认声音。
    - audio_config 非空：直接返回（已配过，回填不重填），流到 render_dispatch。
    - audio_config 空：interrupt 让用户填生成参数，resume 写回。
    - resume 非 dict → 抛错暴露，不静默接受。
    """
    current = state.get("audio_config") or {}
    if current:
        log.info("configure_audio: 已配置，跳过（回填）")
        return {}
    result = interrupt({"type": "audio_config", "current": current})
    if not isinstance(result, dict):
        raise ValueError(f"configure_audio: 非法 resume 值（应为 dict）: {result!r}")
    log.info("configure_audio: 已配置", params=result)
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

    薄节点 + 节点外长驻渲染队列服务模式（LangGraph 接外部服务的推荐做法）：
    - 真正的生图副作用在后端 RenderSession（节点外）跑，节点只做「写初始 render_state →
      interrupt 等审批 → resume 读最终 image_map」。
    - 含 interrupt 的节点 resume 时会从头重跑整个函数，故 interrupt 之前必须幂等：
      初始 render_state 已存在则合并保留已生成候选（重入不重跑）。

    流程：
    1. 解析换图点 shot 规格（subjects→tri_view 决定 t2i/edit、参考图），写初始 render_state
       （已存在的 shot 保留 candidates/selected/status，不覆盖——重入不重跑）。
    2. interrupt({type:'image_render', ...})：后端 graph_runner 据此启动 RenderSession 喂 GPU，
       前端 ImageRenderPanel 逐张展示 + 改词重抽。
    3. resume（{decision:'done'}）后兜底校验无空帧（有未完成 shot 则抛错暴露），
       读 selected 终图展开回填 current_image_map，章节状态 → images_done。
    """
    from novel2media import render_planning, render_state

    ch_id = state["current_chapter_id"]
    novel_dir = state["novel_dir"]
    storyboard: list[dict] = state.get("current_storyboard", [])
    characters_profile: dict = state.get("characters_profile", {})

    # 1. 解析换图点 shot 规格 + 写初始 render_state（内容指纹判定 + 全量重建剪枝）
    #
    # render_state 用 storyboard_id 当 key，但 storyboard_id 是分镜重生成时按位置重排的
    # 下标，不是稳定标识——「id 相同」不足以判定「同一镜头」。故复用旧候选的条件加内容指纹
    # （prompt + ref_images + workflow 全等）：改稿后画面内容已变则视为新镜头，丢弃旧候选重出，
    # 绝不把旧场景的图套到新场景上（避免串图）。
    # 同时用 new_shots 全量重建 shots，自动剪掉本轮 specs 里没有的陈旧 shot（如改稿后不再是
    # 换图点的旧镜头），否则 pending_shots() 会遍历到它卡住 resume，而前端按当前 storyboard
    # 算换图点根本看不到它（前后端完成判定不一致）。
    specs = render_planning.build_shot_specs(storyboard, characters_profile, novel_dir)
    data = render_state.load(novel_dir, ch_id) or {"chapter_id": ch_id, "shots": {}}
    old_shots = data.get("shots", {})
    new_shots: dict = {}
    reused = 0
    for spec in specs:
        sid = str(spec["storyboard_id"])
        existing = old_shots.get(sid)
        # 内容指纹一致 + 有候选 → 真·同一镜头，保留候选/选定/状态（重入不重跑）
        same_shot = bool(
            existing
            and existing.get("candidates")
            and existing.get("prompt") == spec["prompt"]
            and existing.get("ref_images") == spec["ref_images"]
            and existing.get("workflow") == spec["workflow"]
        )
        if same_shot:
            existing["subjects"] = spec["subjects"]  # subjects 仅展示用，可无条件刷新
            new_shots[sid] = existing
            reused += 1
        else:
            # 新镜头 / 改稿后内容已变：丢弃旧候选，重置为待生成
            new_shots[sid] = {
                "storyboard_id": spec["storyboard_id"],
                "workflow": spec["workflow"],
                "prompt": spec["prompt"],
                "ref_images": spec["ref_images"],
                "subjects": spec["subjects"],
                "candidates": [],
                "selected": None,
                "status": "pending",
                "error": None,
            }
    data["shots"] = new_shots  # 全量替换 = 剪掉陈旧 shot
    render_state.save(novel_dir, ch_id, data)
    log.info(
        "render_generate_images: 写初始 render_state",
        chapter=ch_id,
        shots=len(specs),
        reused=reused,
        pruned=len(old_shots) - reused,
    )

    # 2. interrupt：渲染交互（后端启动 RenderSession，前端逐张展示 + 抽卡）
    raw = interrupt(
        {
            "type": "image_render",
            "chapter_id": ch_id,
            "storyboard": storyboard,  # 前端按数组顺序展示（含非换图点，复用上一换图点图）
            "specs": specs,            # 换图点 shot 规格（含 prompt/subjects/workflow）
        }
    )

    # 3. resume：兜底校验无空帧 + 回填 image_map
    decision = raw.get("decision") if isinstance(raw, dict) else raw
    if decision != "done":
        raise ValueError(f"render_generate_images: 非法 resume 值（应为 done）: {raw!r}")

    final = render_state.load(novel_dir, ch_id)
    if final is None:
        raise ValueError(f"render_generate_images: resume 时 render_state 缺失 chapter={ch_id}")
    pending = render_state.pending_shots(final)
    if pending:
        # 不静默放行带空帧的渲染（与前端 disabled 双重把关）
        raise ValueError(
            f"render_generate_images: 仍有 {len(pending)} 个镜头未完成/未选定，不能完成渲染: {pending}"
        )

    # 换图点 selected → 展开到所有 storyboard_id（非换图点复用上一换图点图）
    selected_by_sid = {
        int(sid): shot["selected"] for sid, shot in final.get("shots", {}).items()
    }
    image_map = render_planning.expand_image_map(storyboard, selected_by_sid)

    chapters_status = dict(state.get("chapters_status", {}))
    chapters_status[ch_id] = "images_done"
    log.info("render_generate_images: 渲染完成", chapter=ch_id, images=len(image_map))
    return {"current_image_map": image_map, "chapters_status": chapters_status}


def render_synthesize_audio(state: dict) -> dict:
    """TTS 音频合成（dots.tts 异步 job）：整章脚本拼成整段文本提交，下载 final.wav 落盘。

    dots.tts 服务端按换行把文本切 chunk、串行合成后拼成整段音频，单章只产出一段 final.wav。
    本期不做逐句时间戳（current_timestamps 仍返回空），timeline 仍仅含图。
    完成后推进章节状态：images_done → audio_done。

    同步节点：synthesize 会阻塞轮询数十秒~数分钟，LangGraph 在 astream 异步执行图时
    会把同步节点放线程池执行（与 render_generate_images 同），不阻塞后端事件循环。
    """
    from novel2media.clients.tts import TTSClient
    from novel2media.nodes.image_nodes import _load_config  # 复用同一配置加载（小说目录优先回退项目根）

    ch_id = state["current_chapter_id"]
    novel_dir = Path(state["novel_dir"])
    script: list[dict] = state.get("current_script", [])

    # 1. 拼合成文本：逐条口播 text 用换行分隔（dots 按换行切 chunk 串行合成再拼整段）。
    #    current_script 结构为 [{"text","action","speaker"}]，音频只取 text。
    lines = [str(it.get("text", "")).strip() for it in script if str(it.get("text", "")).strip()]
    if not lines:
        # 空脚本异常暴露，不静默产出空音频
        raise ValueError(f"render_synthesize_audio: 章节 {ch_id} current_script 为空，无可合成文本")
    text = "\n".join(lines)

    cfg = _load_config(state)
    client = TTSClient(cfg.tts_url, cfg.tts_timeout, cfg.retry_max, cfg.retry_backoff)

    # 2. 合成参数：dots 默认旋钮 + chunk 间静音 + audio_config 用户覆盖（全局单播）。
    #    audio_config 含 voice_name 时引用对应音色预设，缺省则用 dots 默认声音。
    params = {
        **cfg.tts_params,
        "silence_ms": cfg.silence_ms,
        **(state.get("audio_config") or {}),
    }

    # 3. submit→poll→download，落盘 novel_dir/ch_id/audio.wav（与图像产物、timeline.json 同目录）
    wav_bytes = client.synthesize(text, params)
    out_dir = novel_dir / ch_id
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = out_dir / "audio.wav"
    audio_path.write_bytes(wav_bytes)

    chapters_status = dict(state.get("chapters_status", {}))
    chapters_status[ch_id] = "audio_done"
    log.info(
        "render_synthesize_audio: 合成完成",
        chapter=ch_id,
        audio=str(audio_path),
        chars=len(text),
    )
    return {
        "current_audio_path": str(audio_path),
        "current_subtitles_path": "",  # 本期不做字幕
        "current_timestamps": [],  # 本期不做逐句时间戳
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
