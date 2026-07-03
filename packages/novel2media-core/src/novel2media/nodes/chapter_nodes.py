from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langgraph.types import interrupt
from novel2media.chapters import (
    chapter_pad_width,
    chapter_sort_key,
    group_id_for,
    read_group_text,
)
from novel2media.llm import invoke_llm_json_array
from novel2media.nodes.init_nodes import _REQUIRED_CHAR_FIELDS
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

# 分镜第二步「画面生成」分批参数：换图点过多时按批并行调 LLM。
# max_tokens 已调至 16384，大幅放宽批大小以减少调用次数（每次调用都有大量 prompt 固定开销）
_SCENE_PROMPT_BATCH_SIZE = 40  # 每批最多多少个换图点（40 个换图点输出约 4k~6k tokens）
_SCENE_PROMPT_MAX_WORKERS = 2  # 并发上限（控制 ARK 限流压力，不宜过大）


def _discover_new_single_chapter_groups(
    chapters_dir: Path,
    chapter_groups: dict[str, list[str]],
    chapters_status: dict[str, str],
    pad_width: int,
) -> None:
    """就地把中途新增的章节文件各自作为单章组追加进 chapter_groups / chapters_status。

    分组在 init 一次性定死；此处兜底用户在运行中新放入 chapters/ 的 .txt 文件：
    每个尚未归入任一组的 stem 复用 init 定死的 pad_width 组成单章组 `ch<n>` 并置 pending。
    若新文件章号跨位宽进位（id 位数 > pad_width）导致与既有排序不一致 → log.warning 暴露，
    不静默乱序。
    """
    grouped = {stem for members in chapter_groups.values() for stem in members}
    new_stems = sorted(
        (p.stem for p in chapters_dir.glob("*.txt") if p.stem not in grouped),
        key=chapter_sort_key,
    )
    for stem in new_stems:
        gid = group_id_for([stem], pad_width)
        # 位宽进位检测：gid 形如 `ch<零填充章号>`，去掉 `ch` 前缀后位数应 == pad_width。
        # 若章号位数超过 init 定死的 pad_width，字典序会与章号序脱节 → 暴露不静默。
        if len(gid) - 2 > pad_width:
            log.warning(
                "load_chapter: 新增章节章号跨位宽进位，单元 id 排序可能与章号序不一致",
                stem=stem,
                group_id=gid,
                pad_width=pad_width,
            )
        # id 碰撞（章号重复等）：对齐 build_chapter_groups 的暴露意图，warning 并 skip
        # 该新文件（不覆盖既有组），继续处理其余新文件。
        if gid in chapter_groups:
            log.warning(
                "load_chapter: 新增章节单元 id 与既有组冲突，跳过不覆盖",
                group_id=gid,
                stem=stem,
                existing_members=chapter_groups[gid],
            )
            continue
        chapter_groups[gid] = [stem]
        chapters_status[gid] = "pending"
        log.info("load_chapter: 发现新增章节，追加为单章组", group_id=gid, stem=stem)


def load_chapter(state: dict) -> dict:
    """加载下一单元（组）并重置章节级中间态。

    单元选取优先级（R13）：`chapters_status` 的 key 是组 id。先取 `processing`
    （恢复断点/续跑），无则取第一个 `pending` 置 `processing`。无 pending/processing
    单元时返回空 current_chapter_id，由条件边路由到 END。

    中途新增文件成单章组：init 分组一次定死后，运行中新放入 chapters/ 的 .txt 文件
    各自成单章组（复用 init 定死的 pad_width）追加进 chapter_groups 并置 pending。

    控制字段重置（R3）：fork/resume 残留的 _review_decision/_chapter_advance 等
    路由字段会串扰下一单元或新分支路由，此处统一置默认值。
    """
    novel_dir = Path(state["novel_dir"])
    chapters_dir = novel_dir / "chapters"
    chapters_status: dict[str, str] = dict(state.get("chapters_status", {}))
    chapter_groups: dict[str, list[str]] = dict(state.get("chapter_groups", {}))
    # 位宽优先取 init 定死的 state 值（活的 plan_graph 流程 configure_chapter_grouping 必设）。
    # 缺失/为 0 时（废弃 chapter.py 子图或旧 checkpoint 未带该字段）自给自足：从实际章节文件
    # （chapters_dir 下 .txt stem + 已入组成员）推导，保证 load_chapter 不依赖外部分组配置。
    pad_width = state.get("chapter_group_pad_width")
    if not pad_width:
        grouped_stems = [stem for members in chapter_groups.values() for stem in members]
        disk_stems = [p.stem for p in chapters_dir.glob("*.txt")]
        pad_width = chapter_pad_width(disk_stems + grouped_stems)

    # 中途新增文件成单章组（兜底用户运行中新增章节），随本节点 return 合并回 state
    _discover_new_single_chapter_groups(chapters_dir, chapter_groups, chapters_status, pad_width)

    # R13：优先恢复 processing（断点续跑），无则取第一个 pending（对组 id 生效）
    processing = sorted(
        [gid for gid, st in chapters_status.items() if st == "processing"],
        key=chapter_sort_key,
    )
    pending = sorted(
        [gid for gid, st in chapters_status.items() if st == "pending"],
        key=chapter_sort_key,
    )
    if processing:
        ch_id = processing[0]
        log.info("load_chapter: 恢复 processing 单元（断点续跑）", chapter=ch_id)
    elif pending:
        ch_id = pending[0]
        chapters_status[ch_id] = "processing"
        log.info("load_chapter: 开始处理单元", chapter=ch_id)
    else:
        log.info("load_chapter: 无 pending 单元，流程结束")
        return {
            "chapters_status": chapters_status,
            "chapter_groups": chapter_groups,
            "current_chapter_id": "",
            "current_chapter_text_path": "",
            "current_chapter_member_paths": [],
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

    # 解析选中单元的成员章节原文路径。章节原文是不可变源文件，仅存路径；不再把整组
    # 文本放进 state（避免每条 checkpoint 复制一份）。需要原文时按路径读取。
    members = chapter_groups.get(ch_id)
    if not members:
        # 选中单元无成员属异常（不应发生），显式抛错暴露
        raise ValueError(f"load_chapter: 单元 {ch_id} 在 chapter_groups 中无成员章节")
    member_paths = [str(chapters_dir / f"{stem}.txt") for stem in members]

    return {
        "chapters_status": chapters_status,
        "chapter_groups": chapter_groups,
        "current_chapter_id": ch_id,
        # current_chapter_text_path 保留组首成员，向后兼容/展示；整组读取走 member_paths
        "current_chapter_text_path": member_paths[0],
        "current_chapter_member_paths": member_paths,
        "current_script": [],
        "current_storyboard": [],
        "current_audio_path": "",
        "current_subtitles_path": "",
        "current_timestamps": [],
        "current_image_map": {},
        "current_timeline_path": "",
        "script_review_attempts": 0,
        "storyboard_review_attempts": 0,
        # R3：清空章节级控制字段，防止上一单元/上一分支残留驱动本单元路由
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
    # 整组拼接原文喂 LLM（兜底：member 缺失时退回单文件，兼容旧 checkpoint）
    chapter_text = read_group_text(
        state.get("current_chapter_member_paths") or [state["current_chapter_text_path"]]
    )
    characters_profile = state.get("characters_profile", {})
    feedback = state.get("_script_review_feedback", "") or ""

    # 解说方案模板：run 内选定/自定义（configure_chapter_grouping 写入，随委派进 plan 子图）；
    # 缺失（旧 checkpoint）时传 None，builder 回退恐怖悬疑默认预设。
    narration_templates = state.get("narration_templates") or {}
    # 提示词自进化：已采纳校正规则注入块（web 层按 scheme 载入，随委派进 plan 子图）；缺省不注入。
    learned_rules_text = state.get("learned_rules_text") or {}
    prompt = build_adapt_script_prompt(
        chapter_text,
        characters_profile,
        feedback,
        template=narration_templates.get("adapt_script"),
        worldview=state.get("worldview", ""),
        learned_rules=learned_rules_text.get("adapt_script", ""),
    )
    script = invoke_llm_json_array(prompt, node="adapt_script", label="adapt_script")  # [{"text","action","speaker"}]

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
    # 整组拼接原文喂 LLM（兜底：member 缺失时退回单文件，兼容旧 checkpoint）
    chapter_text = read_group_text(
        state.get("current_chapter_member_paths") or [state["current_chapter_text_path"]]
    )
    characters_profile = state.get("characters_profile", {})
    feedback = state.get("_storyboard_review_feedback", "") or ""
    worldview = state.get("worldview", "")

    if not script:
        log.info("generate_storyboard: 空脚本，跳过", chapter=ch_id)
        return {"current_storyboard": [], "_storyboard_review_feedback": ""}

    # ---- 第一步：初筛换图点（串行单次，输出换图点下标列表）----
    # 换图点节奏密度也走 run 内解说方案模板；缺失时 builder 回退默认预设。
    narration_templates = state.get("narration_templates") or {}
    # 提示词自进化：换图点阶段的已采纳校正规则注入块；缺省不注入。
    learned_rules_text = state.get("learned_rules_text") or {}
    sc_prompt = build_scene_change_prompt(
        script, chapter_text, feedback,
        template=narration_templates.get("scene_change"),
        learned_rules=learned_rules_text.get("scene_change", ""),
    )
    raw_indices = invoke_llm_json_array(sc_prompt, node="generate_storyboard", label="storyboard_scene_change")
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
            batch, chapter_text, characters_profile, feedback, batch_info=batch_info, worldview=worldview
        )
        return invoke_llm_json_array(
            prompt, node="generate_storyboard", label=f"storyboard_scene_prompt[{idx + 1}/{n}]"
        )

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
        if isinstance(subjects, list) and len(subjects) > 2:
            # 硬约束兜底：下游图生图最多 2 个参考角色，列 3 个渲染必报错。prompt 侧已要求
            # 「≥3 人 subjects=[]」，这里对漏网结果截断到前 2 个保证渲染不崩，并记录违规暴露
            # （理想情况应由 LLM 拆镜/群像处理，而非在此裁剪）。
            log.warning(
                "generate_storyboard: 主体角色超 2 人，已截断至前 2（图生图参考图上限）",
                chapter=ch_id, sid=sid, subjects=subjects,
            )
            subjects = subjects[:2]
        entry["subjects"] = subjects
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
    # 整组拼接原文喂 LLM（兜底：member 缺失时退回单文件，兼容旧 checkpoint）
    chapter_text = read_group_text(
        state.get("current_chapter_member_paths") or [state["current_chapter_text_path"]]
    )
    existing_names = set(state.get("characters_profile", {}).keys())

    prompt = build_detect_new_characters_prompt(chapter_text, existing_names, worldview=state.get("worldview", ""))
    detected = invoke_llm_json_array(prompt, node="detect_new_characters_llm", label="detect_new_characters")  # [{"name","appearance","character_trait","visual_trait","tri_view_prompt","tri_view_prompt_cn"}]

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



# ─── 渲染阶段纯函数（从图节点提取，供后端 API 直接调用）──────────────────


def render_dispatch(render_batch: list[dict], chapters_status: dict[str, str], novel_dir: str) -> dict:
    """取下一个 planned 章节，从 render_batch 读 script/storyboard 返回章节信息。

    纯函数（从图节点提取）：不再依赖图 state，由后端 API 直接调用。
    逐章串行：选取策略 sorted 后取第一个 `planned` 章节。

    无 planned → 返回空 chapter_id（调用方据此判断本批渲染完）。
    planned 章节缺 render_batch 稿件属异常，显式抛错不静默跳过。
    """
    planned = sorted([ch for ch, st in chapters_status.items() if st == "planned"])
    if not planned:
        log.info("render_dispatch: 无 planned 章节")
        return {"current_chapter_id": ""}

    ch_id = planned[0]
    item = next((it for it in render_batch if it.get("chapter_id") == ch_id), None)
    if item is None:
        raise ValueError(
            f"render_dispatch: planned 章节 {ch_id} 在 render_batch 中无稿件"
        )
    # ch_id 现在是组 id，chapters/{ch_id}.txt 不存在（不再假设单文件）。渲染阶段只用
    # script/storyboard，用不到原文，故置空（避免下游误按单文件路径读取）。
    storyboard = item.get("storyboard", [])
    script = item.get("script", [])
    log.info("render_dispatch: 选取渲染单元", chapter=ch_id, shots=len(storyboard))
    return {
        "current_chapter_id": ch_id,
        "current_chapter_text_path": "",
        "current_script": script,
        "current_storyboard": storyboard,
    }


def render_generate_images(
    novel_dir: str,
    chapter_id: str,
    storyboard: list[dict],
    characters_profile: dict,
) -> list[dict]:
    """场景图生成纯函数：写初始 render_state，返回 shot specs 供 API 启动 RenderSession。

    从图节点提取为纯函数（不再含 interrupt）：
    1. 解析换图点 shot 规格（subjects→tri_view 决定 t2i/edit、参考图），写初始 render_state
       （已存在的 shot 保留 candidates/selected/status，不覆盖——重入不重跑）。
    2. 返回 specs 供后端 API 启动 RenderSession 喂 GPU，前端逐张展示 + 抽卡。

    内容指纹判定 + 全量重建剪枝逻辑保留（改稿后画面内容已变则视为新镜头，丢弃旧候选重出）。
    """
    from novel2media import render_planning, render_state

    specs = render_planning.build_shot_specs(storyboard, characters_profile, novel_dir)
    data = render_state.load(novel_dir, chapter_id) or {"chapter_id": chapter_id, "shots": {}}
    old_shots = data.get("shots", {})
    new_shots: dict = {}
    reused = 0
    for spec in specs:
        sid = str(spec["storyboard_id"])
        existing = old_shots.get(sid)
        same_shot = bool(
            existing
            and existing.get("candidates")
            and existing.get("prompt") == spec["prompt"]
            and existing.get("ref_images") == spec["ref_images"]
            and existing.get("workflow") == spec["workflow"]
        )
        if same_shot:
            existing["subjects"] = spec["subjects"]
            new_shots[sid] = existing
            reused += 1
        else:
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
    data["shots"] = new_shots
    render_state.save(novel_dir, chapter_id, data)
    log.info(
        "render_generate_images: 写初始 render_state",
        chapter=chapter_id,
        shots=len(specs),
        reused=reused,
        pruned=len(old_shots) - reused,
    )
    return specs


def render_synthesize_audio(
    novel_dir: str,
    chapter_id: str,
    script: list[dict],
    audio_config: dict | None = None,
) -> dict:
    """TTS 音频合成纯函数：整章脚本拼成整段文本提交，下载 final.wav 落盘。

    从图节点提取为纯函数（不再依赖图 state），由后端 API 直接调用。
    dots.tts 服务端按换行把文本切 chunk、串行合成后拼成整段音频，单章只产出一段 final.wav。

    返回 {audio_path, subtitles_path, timestamps} 供调用方更新 chapters_artifacts。
    """
    from novel2media.clients.tts import TTSClient
    from novel2media.nodes.image_nodes import _load_config

    novel_dir_path = Path(novel_dir)

    lines = [str(it.get("text", "")).strip() for it in script if str(it.get("text", "")).strip()]
    if not lines:
        raise ValueError(f"render_synthesize_audio: 章节 {chapter_id} script 为空，无可合成文本")
    text = "\n".join(lines)

    cfg = _load_config({"novel_dir": novel_dir})
    client = TTSClient(cfg.tts_url, cfg.tts_timeout, cfg.retry_max, cfg.retry_backoff)

    params = {
        **cfg.tts_params,
        "silence_ms": cfg.silence_ms,
        **(audio_config or {}),
    }

    log.info(
        "render_synthesize_audio: 提交 TTS 合成",
        chapter=chapter_id,
        voice_name=params.get("voice_name"),
        language=params.get("language"),
        guidance_scale=params.get("guidance_scale"),
        speaker_scale=params.get("speaker_scale"),
        text_len=len(text),
        tts_url=cfg.tts_url,
    )
    wav_bytes = client.synthesize(text, params)
    out_dir = novel_dir_path / chapter_id
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = out_dir / "audio.wav"
    audio_path.write_bytes(wav_bytes)

    log.info(
        "render_synthesize_audio: 合成完成",
        chapter=chapter_id,
        audio=str(audio_path),
        chars=len(text),
    )
    return {
        "audio_path": str(audio_path),
        "subtitles_path": "",
        "timestamps": [],
    }


def render_build_timeline(
    novel_dir: str,
    chapter_id: str,
    image_map: dict,
    audio_path: str,
    timestamps: list[dict] | None = None,
    chapters_artifacts: dict | None = None,
) -> dict:
    """生成 <ch>/timeline.json 纯函数，返回 timeline_path + 更新后的 chapters_artifacts。

    从图节点提取为纯函数（不再依赖图 state），由后端 API 直接调用。
    """
    result = build_timeline(
        novel_dir=novel_dir,
        chapter_id=chapter_id,
        image_map=image_map,
        audio_path=audio_path,
        timestamps=timestamps or [],
        chapters_artifacts=chapters_artifacts or {},
    )
    log.info("render_build_timeline: 完成", chapter=chapter_id)
    return result



def build_timeline(
    novel_dir: str,
    chapter_id: str,
    image_map: dict,
    audio_path: str = "",
    timestamps: list[dict] | None = None,
    chapters_artifacts: dict | None = None,
) -> dict:
    """生成 timeline.json + 更新 chapters_artifacts（纯函数，不再依赖图 state）。"""
    novel_dir_path = Path(novel_dir)
    timestamps = timestamps or []
    chapters_artifacts = chapters_artifacts or {}

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

    out_dir = novel_dir_path / chapter_id
    out_dir.mkdir(parents=True, exist_ok=True)
    timeline_path = out_dir / "timeline.json"
    timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2))

    existing = dict(chapters_artifacts.get(chapter_id, {}))
    existing.update(
        {
            "audio_path": audio_path,
            "subtitles_path": "",
            "timeline_path": str(timeline_path),
        }
    )
    artifacts = dict(chapters_artifacts)
    artifacts[chapter_id] = existing
    log.info("build_timeline: 完成", chapter=chapter_id, entries=len(timeline))
    return {
        "timeline_path": str(timeline_path),
        "chapters_artifacts": artifacts,
    }


def export_to_jianying(
    novel_dir: str,
    chapters_status: dict[str, str],
    chapters_artifacts: dict,
) -> dict:
    """导出 status=rendered 章节（增量），置 exported。

    从图节点提取为纯函数（不再依赖图 state），由后端 API 直接调用。
    返回更新后的 chapters_status。
    """
    novel_dir_path = Path(novel_dir)
    chapters_status = dict(chapters_status)

    rendered_chapters = [ch for ch, st in chapters_status.items() if st == "rendered"]
    if not rendered_chapters:
        log.info("export_to_jianying: 无 rendered 章节")
        return {"chapters_status": chapters_status}

    export_data = []
    for ch_id in sorted(rendered_chapters):
        artifact = chapters_artifacts.get(ch_id, {})
        export_data.append({"chapter_id": ch_id, **artifact})
        chapters_status[ch_id] = "exported"

    out_path = novel_dir_path / "export" / "jianying_draft.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(export_data, ensure_ascii=False, indent=2))

    status_path = novel_dir_path / "chapters_status.json"
    status_path.write_text(json.dumps(chapters_status, ensure_ascii=False, indent=2))

    log.info("export_to_jianying: 导出完成", chapters=rendered_chapters)
    return {"chapters_status": chapters_status}
