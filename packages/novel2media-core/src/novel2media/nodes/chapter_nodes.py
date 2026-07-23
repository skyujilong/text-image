from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TypedDict

from langgraph.types import interrupt
from novel2media.chapters import (
    chapter_pad_width,
    chapter_sort_key,
    forward_chapter_paths,
    group_id_for,
    read_group_text,
)
from novel2media.llm import invoke_llm_json_array
from novel2media.nodes.init_nodes import _REQUIRED_CHAR_FIELDS, _normalize_char_role
from novel2media.prompts.chapter_prompts import (
    _SCENE_STYLE_TRIGGER,
    build_adapt_script_prompt,
    build_candidate_scan_prompt,
    build_enrich_characters_prompt,
    build_reconcile_scenes_prompt,
    build_scene_candidate_scan_prompt,
    build_scene_change_prompt,
    build_scene_prompt_for_shots,
)
from novel2media.prompts.narration_schemes import get_scheme, resolve_perspective_tokens
from novel2media_logging import get_logger

log = get_logger("chapter_nodes")

_PENDING_STATUSES = {"pending", "processing"}


def _resolve_narration_template(state: dict, field: str) -> str:
    """按 run 的 narration_scheme 现取解说模板正文（field ∈ {adapt_script, scene_change}）。

    静态默认 + 覆盖槽：默认按所选 scheme 直接读源码模板——改 narration_schemes.py
    对**所有** run 即时生效（不再快照进 state、不用新开 run）。仅当 state["narration_templates"]
    带该字段的显式 override 时才优先用它：覆盖场景 = 用户在分组面板手改了 prompt，
    或旧 checkpoint 里 configure_chapter_grouping 快照的整段模板（向后兼容，行为不变）。
    """
    override = (state.get("narration_templates") or {}).get(field)
    if override:
        return override
    scheme = get_scheme(state.get("narration_scheme"))
    return getattr(scheme, f"{field}_template")

# 分镜第二步「画面生成」分批参数：换图点过多时按批并行调 LLM。
# max_tokens 已调至 16384，大幅放宽批大小以减少调用次数（每次调用都有大量 prompt 固定开销）
_SCENE_PROMPT_BATCH_SIZE = 40  # 每批最多多少个换图点（40 个换图点输出约 4k~6k tokens）
_SCENE_PROMPT_MAX_WORKERS = 2  # 并发上限（控制 ARK 限流压力，不宜过大）

# ---- 分镜观测（storyboard.debug.json + backend.log 诊断行）关键词表 ----
# 仅用于事后分析的启发式命中标记，命中即算「近似信号」（非精确语义判断），
# 不参与任何换图/生成/渲染逻辑。用于量化用户反馈的四个问题（重复切换/高潮/背景/氛围）。
_BG_SUPPRESS_MARKERS = (
    "背景全黑", "背景压黑", "背景压至全黑", "背景全糊", "背景深暗",
    "纯色背景", "背景虚化", "浅景深", "背景模糊",
)
_ENV_MARKERS = (
    "室内", "室外", "街", "巷", "夜", "房间", "屋", "厂", "仓库", "走廊",
    "门", "窗", "墙", "桌", "森林", "山", "车", "办公室", "教室", "医院",
    "雨", "雪", "雾", "灯",
)
_TONE_WORDS = (
    "低调", "高调", "高对比", "柔光", "柔和", "硬光", "暗调", "逆光", "冷光", "暖光",
)


class SceneScan(TypedDict):
    """单条 scene_prompt 的启发式关键词扫描结果（近似信号，仅供事后筛查）。"""

    bg_suppressed: bool  # 命中「背景压黑/全糊/虚化」等消灭背景表述
    has_env: bool  # 命中常见环境/地点词（背景是否有交代的近似信号）
    tone_word: str  # 命中的首个影调词，未命中为 ""


class ChangeStats(TypedDict):
    """整章换图节奏的聚合信号（量化「重复切换 / 高潮反差」）。"""

    lines: int  # 口播总条数
    change_points: int  # 换图点数
    change_ratio: float  # 换图点 / 口播总数
    speaker_switch_changes: int  # 换图点中相对上一句「说话人切换」的数量
    speaker_switch_ratio: float  # speaker_switch_changes / change_points
    content_driven_changes: int  # 说话人未变、靠画面变化换的换图点数
    max_consecutive_changes: int  # 相邻连续换图点的最长段长（闪切）
    consecutive_run_count: int  # 相邻连续换图点段数（长度≥2）
    dwell_histogram: dict[str, int]  # 每张图覆盖几句口播的分桶 {1,2,3-5,>5}


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

    # 解说方案模板：默认按 narration_scheme 现取源码（改 .py 即时生效）；用户手改 / 旧 checkpoint
    # 有 override 才用 override。详见 _resolve_narration_template。
    # 提示词自进化：已采纳校正规则注入块（web 层按 scheme 载入，随委派进 plan 子图）；缺省不注入。
    learned_rules_text = state.get("learned_rules_text") or {}
    sys_msg, usr_msg = build_adapt_script_prompt(
        chapter_text,
        characters_profile,
        feedback,
        template=_resolve_narration_template(state, "adapt_script"),
        worldview=state.get("worldview", ""),
        learned_rules=learned_rules_text.get("adapt_script", ""),
        # 人称视角：按所选方案+人称取 %%PERSP_*%% 取值注入（方案不支持人称时为空 no-op）。
        perspective_tokens=resolve_perspective_tokens(
            state.get("narration_scheme"), state.get("narration_perspective")
        ),
    )
    script = invoke_llm_json_array(sys_msg, usr_msg, node="adapt_script", label="adapt_script")  # [{"text","action","speaker"}]

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


def _scan_scene_prompt(scene_prompt: str) -> SceneScan:
    """对单条 scene_prompt 做启发式关键词扫描，产出背景/影调观测标记。

    纯字符串匹配、无副作用；命中与否是近似信号（非精确语义判断），仅供
    storyboard.debug.json 与日志分析筛查，不参与任何生成逻辑。
    """
    text = scene_prompt or ""
    tone = next((w for w in _TONE_WORDS if w in text), "")
    return SceneScan(
        bg_suppressed=any(m in text for m in _BG_SUPPRESS_MARKERS),
        has_env=any(m in text for m in _ENV_MARKERS),
        tone_word=tone,
    )


def _storyboard_change_stats(skeleton: list[dict]) -> ChangeStats:
    """从最终 skeleton 汇总换图节奏信号（量化「重复切换 / 高潮反差」）。纯函数、无副作用。

    speaker_switch_changes 统计「换图点相对上一句口播说话人是否切换」——量化「说话人
    切换即换图」规则驱动的换图占比；dwell_histogram 反映每张图覆盖几句口播的疏密分布
    （看铺垫段与高潮段是否有节奏反差）。整章首条为强制换图，不纳入说话人切换比对。
    """
    lines = len(skeleton)
    change_indices = [i for i, e in enumerate(skeleton) if e.get("scene_change")]
    change_points = len(change_indices)
    if change_points == 0:
        return ChangeStats(
            lines=lines, change_points=0, change_ratio=0.0,
            speaker_switch_changes=0, speaker_switch_ratio=0.0,
            content_driven_changes=0, max_consecutive_changes=0,
            consecutive_run_count=0, dwell_histogram={},
        )

    speaker_switch_changes = 0
    content_driven_changes = 0
    for idx in change_indices:
        if idx == 0:
            continue  # 整章首条为强制换图，无「上一句」可比
        if skeleton[idx].get("speaker") != skeleton[idx - 1].get("speaker"):
            speaker_switch_changes += 1
        else:
            content_driven_changes += 1

    # 相邻连续换图点（下标连续）= 闪切段：算最长段长与段数（长度≥2 的段）
    max_run = run = 1
    consecutive_run_count = 0
    for prev, cur in zip(change_indices, change_indices[1:]):
        if cur == prev + 1:
            run += 1
        else:
            if run >= 2:
                consecutive_run_count += 1
            max_run = max(max_run, run)
            run = 1
    if run >= 2:
        consecutive_run_count += 1
    max_run = max(max_run, run)

    # 每个换图点覆盖几句口播（到下一换图点/章末）的疏密分布
    histogram: dict[str, int] = {"1": 0, "2": 0, "3-5": 0, ">5": 0}
    for pos, idx in enumerate(change_indices):
        next_idx = change_indices[pos + 1] if pos + 1 < change_points else lines
        dwell = next_idx - idx
        bucket = "1" if dwell == 1 else "2" if dwell == 2 else "3-5" if dwell <= 5 else ">5"
        histogram[bucket] += 1

    return ChangeStats(
        lines=lines,
        change_points=change_points,
        change_ratio=round(change_points / lines, 3) if lines else 0.0,
        speaker_switch_changes=speaker_switch_changes,
        speaker_switch_ratio=round(speaker_switch_changes / change_points, 3),
        content_driven_changes=content_driven_changes,
        max_consecutive_changes=max_run,
        consecutive_run_count=consecutive_run_count,
        dwell_histogram=histogram,
    )


def _build_storyboard_debug(skeleton: list[dict], stats: ChangeStats) -> dict:
    """构造 storyboard.debug.json 内容：顶部 summary + 逐条明细（含观测标记）。纯函数。

    逐条把「说话人是否切换、这张图覆盖几句、背景/影调关键词命中」标出来，便于打开产物
    直接读 scene_prompt 做背景缺失/氛围一致的深度分析。
    """
    n = len(skeleton)
    shots: list[dict] = []
    for i, entry in enumerate(skeleton):
        prev_speaker = skeleton[i - 1].get("speaker", "") if i > 0 else ""
        speaker = entry.get("speaker", "")
        dwell_lines = 0
        if entry.get("scene_change"):
            next_idx = next(
                (j for j in range(i + 1, n) if skeleton[j].get("scene_change")), n
            )
            dwell_lines = next_idx - i
        shots.append(
            {
                "storyboard_id": entry.get("storyboard_id", i),
                "scene_change": entry.get("scene_change", False),
                "speaker": speaker,
                "prev_speaker": prev_speaker,
                "speaker_switched": bool(i > 0 and speaker != prev_speaker),
                "dwell_lines": dwell_lines,
                "subjects": entry.get("subjects", []),
                "scene_prompt": entry.get("scene_prompt", ""),
                **_scan_scene_prompt(entry.get("scene_prompt", "")),
            }
        )
    return {"summary": stats, "shots": shots}


def _write_storyboard_debug(novel_dir: str, chapter_id: str, payload: dict) -> None:
    """把分镜观测产物写到 <novel_dir>/<chapter_id>/storyboard.debug.json（沿用 build_timeline 落盘范式）。

    纯观测副产物，不参与渲染、可随时删。写失败由调用方兜（只告警不抛），本函数保持直白。
    """
    out_dir = Path(novel_dir) / chapter_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "storyboard.debug.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _segment_change_indices(script: list[dict]) -> set[int]:
    """段落驱动换图：按 adapt 打的 seg 段号算「换图点」下标集（纯函数、不调 LLM）。

    换图点 = seg 相对上一条发生变化的那一条（每个配图段的第一条）。seg 由 adapt_script
    的段落方案模板逐条输出：同一原文自然段共享一个 seg，长段就地切细、开篇钩子逐条各占一段。
    缺失/非法 seg（非 int 或 bool）不新起段、沿用上一段，避免因个别漏字段而错切；全缺时返回
    空集（首条换图由下游 skeleton[0]["scene_change"]=True 兜底），并打 warning 暴露。
    """
    changes: set[int] = set()
    prev: int | None = None
    missing = 0
    for i, item in enumerate(script):
        seg = item.get("seg")
        if not isinstance(seg, int) or isinstance(seg, bool):
            missing += 1  # 缺/非法 seg → 沿用上一段，不新起段
            continue
        if seg != prev:
            changes.add(i)
            prev = seg
    if missing:
        log.warning(
            "segment_change: 部分口播缺 seg 段号，按沿用前段处理",
            missing=missing,
            total=len(script),
        )
    return changes


def generate_storyboard(state: dict) -> dict:
    """LLM 两步生成分镜 → current_storyboard（不落盘，稿件由 commit_chapter 收入 render_batch）。

    两步法（避免一次性生成全部 scene_prompt 导致输出 token 截断）：
    - 第一步「确定换图点」：按 narration_scheme 分两种模式——段落驱动方案（segment_driven_change，
      如 plain_paragraph）纯代码按 adapt 打的 seg 段号切、不调 LLM；其余方案由 LLM 初筛
      （只判定哪些口播是换图点，输出下标列表，输出量极小，串行单次）。
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
    scenes_profile = state.get("scenes_profile", {}) or {}
    feedback = state.get("_storyboard_review_feedback", "") or ""
    worldview = state.get("worldview", "")

    if not script:
        log.info("generate_storyboard: 空脚本，跳过", chapter=ch_id)
        return {"current_storyboard": [], "_storyboard_review_feedback": ""}

    # ---- 第一步：确定换图点 ----
    # 按 narration_scheme 分两种模式：
    #   ① 段落驱动（segment_driven_change=True，如 plain_paragraph）：换图点由 adapt 打的 seg
    #      段号纯代码判定（_segment_change_indices），不调 LLM、零漂移——同一原文自然段共用一张图。
    #   ② LLM 初筛（现状，其余方案）：LLM 只判定哪些口播是换图点（输出下标列表，输出量极小，串行单次）。
    # 模板/密度默认按 narration_scheme 现取源码（改 .py 即时生效）；用户手改 / 旧 checkpoint 有 override 才用。
    scheme = get_scheme(state.get("narration_scheme"))
    n_script = len(script)
    change_set: set[int] = set()
    change_triggers: dict[int, str] = {}

    if scheme.segment_driven_change:
        # ① 段落驱动：纯代码按 seg 段号切换图点，不调 scene_change LLM。
        change_set = _segment_change_indices(script)
        change_mode = "segment"
    else:
        # ② LLM 初筛换图点（串行单次，输出换图点下标列表）。
        # 提示词自进化：换图点阶段的已采纳校正规则注入块；缺省不注入。
        learned_rules_text = state.get("learned_rules_text") or {}
        sc_sys, sc_usr = build_scene_change_prompt(
            script, chapter_text, feedback,
            template=_resolve_narration_template(state, "scene_change"),
            learned_rules=learned_rules_text.get("scene_change", ""),
        )
        raw_indices = invoke_llm_json_array(sc_sys, sc_usr, node="generate_storyboard", label="storyboard_scene_change")
        # 输出已从「等长布尔数组」改为「换图点下标列表」：模型不再需要逐条铺满 N 个 bool，
        # 从根上消除「数组长度对不上」的崩溃。这里只校验下标合法性（整数、在范围内），
        # 越界/非整数直接抛错暴露，不静默丢弃（否则会与 script 错位、污染音频/字幕对齐）。
        # 兼容两种输出格式：① 纯下标整数（多数解说方案）；② {"i": 下标, "trigger": 触发类别}
        # （horror 方案要求每个换图点标注命中的触发类别，用"说得出理由才换"压掉虚假切换，
        # trigger 分布也进观测日志便于诊断）。两种都归一到下标集合，dict 顺带收集触发标注。
        for v in raw_indices:
            if isinstance(v, dict):
                idx = v.get("i")
                trigger = v.get("trigger")
            else:
                idx, trigger = v, None
            if not isinstance(idx, int) or isinstance(idx, bool):
                raise ValueError(f"换图点初筛结果含非整数下标: {idx!r}（原始元素 {v!r}，应为 0~{n_script - 1} 的整数）")
            if idx < 0 or idx >= n_script:
                raise ValueError(f"换图点初筛结果下标越界: {idx}（口播共 {n_script} 条，合法范围 0~{n_script - 1}）")
            change_set.add(idx)
            if isinstance(trigger, str) and trigger.strip():
                change_triggers[idx] = trigger.strip()
        change_mode = "llm"

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

    # 触发类别分布（horror 方案带 trigger 标注时才非空）：看换图点各由哪类触发驱动，
    # 「场景切换/新人物」占比高说明换图有据，「机位/措辞」类应已被判定纪律压掉。
    trigger_dist: dict[str, int] = {}
    for _trig in change_triggers.values():
        trigger_dist[_trig] = trigger_dist.get(_trig, 0) + 1
    # 观测（不改行为）：初筛的换图点 vs 强制首条后的最终换图点，看强制项影响。
    # mode=segment 时 change_set 是代码按 seg 段号算出的换图点（llm_change_points 即此值）。
    log.info(
        "generate_storyboard.scene_change",
        chapter=ch_id,
        mode=change_mode,
        lines=n_script,
        llm_change_points=len(change_set),
        forced_first=0 not in change_set,
        final_change_points=sum(1 for e in skeleton if e["scene_change"]),
        triggers_labeled=len(change_triggers),
        trigger_dist=trigger_dist,
    )

    # ---- 第二步：只为换图点生成 subjects + scene_prompt（分批并行）----
    shots = _collect_shots(skeleton, script)
    batches = _batch_shots(shots, _SCENE_PROMPT_BATCH_SIZE)
    n = len(batches)

    def _run_batch(args: tuple[int, list[dict]]) -> list[dict]:
        idx, batch = args
        batch_info = (idx + 1, n) if n > 1 else None
        sp_sys, sp_usr = build_scene_prompt_for_shots(
            batch, chapter_text, characters_profile, feedback, batch_info=batch_info,
            worldview=worldview, scenes_profile=scenes_profile,
        )
        return invoke_llm_json_array(
            sp_sys, sp_usr, node="generate_storyboard", label=f"storyboard_scene_prompt[{idx + 1}/{n}]"
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
        # 场景锚点：该换图点归属的地点标准名（渲染 worker 据此补空景背景板；空/未知则不补、走文本背景）
        scene_id = shot.get("scene_id")
        if isinstance(scene_id, str) and scene_id.strip():
            entry["scene_id"] = scene_id.strip()
        # 画幅朝向：LLM 决定横/纵/方；非法或缺省一律回落方形（渲染层据此映射固定尺寸）
        orientation = shot.get("orientation")
        entry["orientation"] = orientation if orientation in ("landscape", "portrait", "square") else "square"
        raw_prompt = (shot.get("scene_prompt") or "").strip()
        if not raw_prompt:
            # 换图点拿到结果但画面描述为空：记录暴露，不拼成只有触发词的退化 prompt 蒙混生图
            log.warning("generate_storyboard: 换图点画面描述为空", chapter=ch_id, sid=sid)
            continue
        # 画风触发词由代码统一拼接到末尾（LLM 不写画风/画质/解剖词）；
        # 画质与人体结构交给 Qwen-Image-Edit 自身，不在正向 prompt 堆解剖词。
        entry["scene_prompt"] = f"{raw_prompt}, {_SCENE_STYLE_TRIGGER}"

    # ---- 观测（纯只读副作用，不改稿）：换图节奏聚合信号 + 可读分镜稿产物 ----
    stats = _storyboard_change_stats(skeleton)
    log.info("generate_storyboard.diagnostics", chapter=ch_id, **stats)
    novel_dir = state.get("novel_dir")
    if novel_dir:
        try:
            _write_storyboard_debug(novel_dir, ch_id, _build_storyboard_debug(skeleton, stats))
        except OSError as exc:
            # 观测产物写失败绝不拖垮生成链——只告警暴露，稿件照常返回
            log.warning(
                "generate_storyboard: 分镜观测产物写入失败（不影响生成）",
                chapter=ch_id, error=str(exc),
            )
    else:
        log.warning("generate_storyboard: 缺少 novel_dir，跳过分镜观测产物", chapter=ch_id)

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


def _collect_known_names(characters_profile: dict) -> set[str]:
    """已登记名字集 = 标准名 ∪ 全部别名（别名感知排除）。"""
    known: set[str] = set(characters_profile.keys())
    for cp in characters_profile.values():
        if isinstance(cp, dict):
            known.update(a for a in (cp.get("aliases") or []) if a)
    return known


def _apply_alias_updates(
    characters_profile: dict,
    alias_updates: list[tuple[str, str]],
    ch_id: str,
) -> dict | None:
    """把 (canonical, alias) 归并补丁应用到既有档案的 aliases。

    返回打过补丁的新 characters_profile（有实际变更时）或 None（无变更）。防御性守则：
    - canonical 先经「别名/标准名 → 标准名」反查归一到真正的档案 key；查不到 → 跳过（不新建）。
    - alias 为空 / 等于标准名 / 已是另一角色的标准名（避免劫持）→ 跳过。
    """
    owner_of: dict[str, str] = {}
    for cname, cp in characters_profile.items():
        owner_of[cname] = cname
        if isinstance(cp, dict):
            for a in cp.get("aliases") or []:
                if a:
                    owner_of.setdefault(a, cname)

    patched = {k: (dict(v) if isinstance(v, dict) else v) for k, v in characters_profile.items()}
    changed = False
    for canonical, alias in alias_updates:
        owner = owner_of.get(canonical)
        if owner is None or not isinstance(patched.get(owner), dict):
            log.warning("detect_new_characters_llm: alias_of 的 canonical 非已知角色，已跳过",
                        chapter=ch_id, canonical=canonical, alias=alias)
            continue
        if not alias or alias == owner:
            continue
        if alias in patched and alias != owner:
            log.warning("detect_new_characters_llm: alias 与另一角色标准名冲突，已跳过",
                        chapter=ch_id, alias=alias, owner=owner)
            continue
        cur = list(patched[owner].get("aliases") or [])
        if alias not in cur:
            cur.append(alias)
            patched[owner]["aliases"] = cur
            changed = True
    return patched if changed else None


def detect_new_characters_llm(state: dict) -> dict:
    """LLM 两阶段检测本组新角色 → 写 setup_queue + 别名归并（独立节点，放分镜之前）。

    单独成节点而非并入 adapt_script：合并后单次输出过长撞 output token 上限被截断
    （实测长章节 finish_reason=length → JSON 断裂）。故拆开各自保持输出小。

    放在 review_script 之后、generate_storyboard 之前：检测出的新角色直接进 setup_queue，
    由 character_setup_subgraph 上传三视图（无单独人工审阅），在分镜前备好 visual_trait，
    避免后期图生图角色对不上。

    两阶段（新角色触发式后瞻，token 花在刀刃上）：
    - Stage 1：仅本组原文的轻量候选扫描（输出极小）。排除集 = 标准名 ∪ 全部别名。无候选 → 直接
      返回空队列、跳过后瞻（中后期常见，零额外 token）。
    - Stage 2（仅有候选时）：读本组 + 后瞻 K 章（cfg.lookahead_chapters），一次产出：
      · resolution="new" 的完整档案（六必填字段 + role 归一 + aliases）→ setup_queue；
      · resolution="alias_of" 的归并记录 → 补进既有角色 aliases（解「早期占位名后续揭真名」前后
        形象对不上/重复建档），直接落盘 characters_profile.json（即使本组无 new 角色也持久化）。

    setup_queue 无 reducer（覆盖语义）：review_script revise 回环重跑时整体覆盖，不残留旧批。
    """
    ch_id = state["current_chapter_id"]
    worldview = state.get("worldview", "")
    characters_profile = state.get("characters_profile", {}) or {}
    known_names = _collect_known_names(characters_profile)

    # 整组拼接原文喂 LLM（兜底：member 缺失时退回单文件，兼容旧 checkpoint）
    member_paths = state.get("current_chapter_member_paths") or [state["current_chapter_text_path"]]
    chapter_text = read_group_text(member_paths)

    # ── Stage 1：轻量候选扫描（仅本组）──────────────────────────────
    cand_sys, cand_usr = build_candidate_scan_prompt(chapter_text, known_names, worldview=worldview)
    candidates = invoke_llm_json_array(cand_sys, cand_usr, node="detect_new_characters_llm", label="candidate_scan")
    candidates = [c for c in candidates if c.get("name") and c["name"] not in known_names]
    if not candidates:
        log.info("detect_new_characters_llm: 本组无新角色候选，跳过后瞻", chapter=ch_id)
        return {"setup_queue": []}

    # ── Stage 2：触发式后瞻增强 + 身份归并 ─────────────────────────
    from novel2media.nodes.image_nodes import _load_config
    from novel2media.nodes.setup_nodes import write_characters_profile

    k = getattr(_load_config(state), "lookahead_chapters", 3)
    current_members = [Path(p).stem for p in member_paths]
    # 全书有序 stem 优先由 shared 的 chapter_groups 展平（chapter_files/order 不进 plan 子图）
    chapter_groups = state.get("chapter_groups") or {}
    ordered_stems = [stem for members in chapter_groups.values() for stem in members] or None
    fwd_paths = forward_chapter_paths(state["novel_dir"], current_members, k, ordered_stems=ordered_stems)
    window_text = read_group_text(member_paths + fwd_paths) if fwd_paths else chapter_text
    log.info("detect_new_characters_llm: 触发后瞻增强", chapter=ch_id,
             candidates=len(candidates), lookahead_chapters=len(fwd_paths))

    enrich_sys, enrich_usr = build_enrich_characters_prompt(window_text, candidates, characters_profile, worldview=worldview)
    results = invoke_llm_json_array(enrich_sys, enrich_usr, node="detect_new_characters_llm", label="enrich_characters")

    # 分流：new → 校验六字段 + 归一 role + 规范 aliases 后入队；alias_of → 归并补丁
    new_chars: list[dict] = []
    alias_updates: list[tuple[str, str]] = []
    for r in results:
        if str(r.get("resolution") or "").strip() == "alias_of":
            canonical, alias = r.get("canonical"), r.get("alias")
            if canonical and alias:
                alias_updates.append((canonical, alias))
            else:
                log.warning("detect_new_characters_llm: alias_of 记录缺 canonical/alias，已跳过", chapter=ch_id, record=r)
            continue
        # 其余按 new 处理（resolution 缺省/其它值也当 new：宁多建档，不静默丢角色）
        name = r.get("name")
        if not name or name in known_names:
            log.warning("detect_new_characters_llm: new 角色名缺失或已登记，已跳过", chapter=ch_id, name=name)
            continue
        for field in _REQUIRED_CHAR_FIELDS:
            if not r.get(field):
                raise ValueError(f"detect_new_characters_llm: 新角色缺 {field} 字段: {r}")
        char = _normalize_char_role(r, "detect_new_characters_llm")
        char.pop("resolution", None)  # resolution 是分流标签，不进档案
        char["aliases"] = [a for a in (char.get("aliases") or []) if a and a != name]
        new_chars.append(char)

    updates: dict = {"setup_queue": new_chars}
    if alias_updates:
        patched = _apply_alias_updates(characters_profile, alias_updates, ch_id)
        if patched is not None:
            write_characters_profile(state["novel_dir"], patched)
            updates["characters_profile"] = patched

    log.info("detect_new_characters_llm: 完成", chapter=ch_id,
             new_characters=len(new_chars), alias_updates=len(alias_updates))
    return updates


# ─── 场景（地点）检测：收集 → 收敛（镜像 detect_new_characters_llm 两阶段 + 触发式后瞻）────────


def _collect_known_scenes(scenes_profile: dict) -> set[str]:
    """已登记地点名集 = 标准名 ∪ 全部别名（别名感知排除）。"""
    known: set[str] = set(scenes_profile.keys())
    for sp in scenes_profile.values():
        if isinstance(sp, dict):
            known.update(a for a in (sp.get("aliases") or []) if a)
    return known


def _apply_scene_alias_updates(
    scenes_profile: dict,
    alias_updates: list[tuple[str, str]],
    ch_id: str,
) -> dict | None:
    """把 (canonical, alias) 归并补丁应用到既有场景的 aliases（镜像 _apply_alias_updates）。

    返回打过补丁的新 scenes_profile（有实际变更时）或 None（无变更）。防御性：canonical 先经
    「别名/标准名 → 标准名」反查归一；查不到 / alias 为空 / 等于标准名 / 已是另一地点标准名 → 跳过。
    """
    owner_of: dict[str, str] = {}
    for sname, sp in scenes_profile.items():
        owner_of[sname] = sname
        if isinstance(sp, dict):
            for a in sp.get("aliases") or []:
                if a:
                    owner_of.setdefault(a, sname)

    patched = {k: (dict(v) if isinstance(v, dict) else v) for k, v in scenes_profile.items()}
    changed = False
    for canonical, alias in alias_updates:
        owner = owner_of.get(canonical)
        if owner is None or not isinstance(patched.get(owner), dict):
            log.warning("detect_new_scenes_llm: alias_of 的 canonical 非已知地点，已跳过",
                        chapter=ch_id, canonical=canonical, alias=alias)
            continue
        if not alias or alias == owner:
            continue
        if alias in patched and alias != owner:
            log.warning("detect_new_scenes_llm: alias 与另一地点标准名冲突，已跳过",
                        chapter=ch_id, alias=alias, owner=owner)
            continue
        cur = list(patched[owner].get("aliases") or [])
        if alias not in cur:
            cur.append(alias)
            patched[owner]["aliases"] = cur
            changed = True
    return patched if changed else None


def detect_new_scenes_llm(state: dict) -> dict:
    """LLM 两阶段检测本组新地点 → 收敛写 scenes_profile（独立节点，放分镜之前）。

    镜像 detect_new_characters_llm（两阶段 + 触发式后瞻），产出「收敛过的地点清单」供 storyboard
    挑 scene_id、供渲染 worker 生成空景背景板锚点。收敛四手段全在此：同义归一（aliases）+
    粗粒度大场所命名 + 频次过滤（build_asset）+ 多章覆盖提炼（后瞻窗口）。

    - Stage 1：仅本组原文的轻量地点候选扫描。排除集 = 标准名 ∪ 全部别名。无候选 → 直接返回、
      跳过后瞻（零额外 token）。
    - Stage 2（仅有候选时）：读本组 + 后瞻 K 章（cfg.lookahead_chapters_for_scenes）一次收敛：
      · resolution="new" → 新场景建档（description/aliases/build_asset，ref_image 待渲染 worker 生成）；
      · resolution="alias_of" → 归并进既有场景 aliases。
      有实际变更时落盘 scenes_profile.json（即使本组无 new 也持久化别名归并）。

    不改 setup_queue（场景不走人工上传子图）；场景参考图由渲染 worker 自动生成空景板。
    """
    ch_id = state["current_chapter_id"]
    worldview = state.get("worldview", "")
    scenes_profile = state.get("scenes_profile", {}) or {}
    known_scenes = _collect_known_scenes(scenes_profile)

    member_paths = state.get("current_chapter_member_paths") or [state["current_chapter_text_path"]]
    chapter_text = read_group_text(member_paths)

    # ── Stage 1：轻量地点候选扫描（仅本组）──────────────────────────
    cand_sys, cand_usr = build_scene_candidate_scan_prompt(chapter_text, known_scenes, worldview=worldview)
    candidates = invoke_llm_json_array(cand_sys, cand_usr, node="detect_new_scenes_llm", label="scene_candidate_scan")
    candidates = [c for c in candidates if c.get("name") and c["name"] not in known_scenes]
    if not candidates:
        log.info("detect_new_scenes_llm: 本组无新地点候选，跳过后瞻", chapter=ch_id)
        return {}

    # ── Stage 2：触发式后瞻收敛（同义归一 + 建档 + 频次判定）────────
    from novel2media.nodes.image_nodes import _load_config
    from novel2media.nodes.setup_nodes import write_scenes_profile

    k = getattr(_load_config(state), "lookahead_chapters_for_scenes", 3)
    current_members = [Path(p).stem for p in member_paths]
    chapter_groups = state.get("chapter_groups") or {}
    ordered_stems = [stem for members in chapter_groups.values() for stem in members] or None
    fwd_paths = forward_chapter_paths(state["novel_dir"], current_members, k, ordered_stems=ordered_stems)
    window_text = read_group_text(member_paths + fwd_paths) if fwd_paths else chapter_text
    log.info("detect_new_scenes_llm: 触发后瞻收敛", chapter=ch_id,
             candidates=len(candidates), lookahead_chapters=len(fwd_paths))

    recon_sys, recon_usr = build_reconcile_scenes_prompt(window_text, candidates, scenes_profile, worldview=worldview)
    results = invoke_llm_json_array(recon_sys, recon_usr, node="detect_new_scenes_llm", label="reconcile_scenes")

    profile = {k2: (dict(v) if isinstance(v, dict) else v) for k2, v in scenes_profile.items()}
    alias_updates: list[tuple[str, str]] = []
    new_scenes = 0
    for r in results:
        if str(r.get("resolution") or "").strip() == "alias_of":
            canonical, alias = r.get("canonical"), r.get("alias")
            if canonical and alias:
                alias_updates.append((canonical, alias))
            else:
                log.warning("detect_new_scenes_llm: alias_of 记录缺 canonical/alias，已跳过", chapter=ch_id, record=r)
            continue
        # 其余按 new 处理（resolution 缺省/其它值也当 new：宁多建档，不静默丢地点）
        name = r.get("name")
        if not name or name in known_scenes or name in profile:
            log.warning("detect_new_scenes_llm: new 地点名缺失或已登记，已跳过", chapter=ch_id, name=name)
            continue
        description = (r.get("description") or "").strip()
        profile[name] = {
            "name": name,
            "description": description or name,  # 缺描述兜底用地点名（空景板 prompt 仍可生图）
            "aliases": [a for a in (r.get("aliases") or []) if a and a != name],
            "build_asset": bool(r.get("build_asset", True)),
            "ref_image": "",  # 空景板待渲染 worker 生成
        }
        new_scenes += 1

    if alias_updates:
        patched = _apply_scene_alias_updates(profile, alias_updates, ch_id)
        if patched is not None:
            profile = patched

    log.info("detect_new_scenes_llm: 完成", chapter=ch_id,
             new_scenes=new_scenes, alias_updates=len(alias_updates))
    if profile != scenes_profile:
        write_scenes_profile(state["novel_dir"], profile)
        return {"scenes_profile": profile}
    return {}


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
    """interrupt：本章规划完成后继续规划下一章，resume 为 "next" / "render"。

    - render（前端唯一发送值）：END plan 子图 → render_batch 刷回主图（渲染工作台可开渲）
      → 主图有 pending 章则重委派继续规划下一章，无则整体 END。即「刷批次 + 继续规划」。
    - next（旧值，UI 已不再发送，保留兼容）：留在本子图 load_chapter 循环，批次不刷回主图。
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
            existing["scene_id"] = spec.get("scene_id", "")  # 场景归属可随重规划刷新（不参与内容指纹，不触发重出）
            # orientation/edit_model 用 setdefault 只补默认、不覆盖：保住用户手动 reroll 时改过的朝向/底模档
            existing.setdefault("orientation", spec.get("orientation", "square"))
            existing.setdefault("edit_model", spec.get("edit_model", "4step"))
            new_shots[sid] = existing
            reused += 1
        else:
            new_shots[sid] = {
                "storyboard_id": spec["storyboard_id"],
                "workflow": spec["workflow"],
                "edit_model": spec.get("edit_model", "4step"),  # 自动批量默认 4step
                "orientation": spec.get("orientation", "square"),
                "prompt": spec["prompt"],
                "ref_images": spec["ref_images"],
                "subjects": spec["subjects"],
                "scene_id": spec.get("scene_id", ""),  # 渲染 worker 据此补空景背景板
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
    """TTS 音频合成纯函数：整章脚本拼成整段文本提交，取回 final.wav + 句级时间轴。

    从图节点提取为纯函数（不再依赖图 state），由后端 API 直接调用。
    dots.tts 服务端按换行把文本切 chunk、串行合成后拼成整段音频，单章只产出一段 final.wav；
    并（服务端开启句级对齐时）产出 sentences.json 句级时间轴。

    产物落盘：
    - <ch>/audio.wav：整段音频。
    - <ch>/sentences.json：dots 原始句级时间轴存档（可得时）。
    - <ch>/subtitles.srt：编辑器可用字幕（可得时）。

    返回 {audio_path, subtitles_path, sentences_path, timestamps}——timestamps 为逐口播行
    时间戳，供 build_timeline 把图片按时间落位。句级对齐不可得时 subtitles_path/sentences_path
    为空、timestamps 为 []（音频仍成功，不阻断），并告警暴露。
    """
    from novel2media.audio.subtitles import (
        LineItem,
        build_srt,
        map_cues_to_lines,
        parse_dots_sentences,
    )
    from novel2media.clients.tts import TTSClient
    from novel2media.nodes.image_nodes import _load_config

    novel_dir_path = Path(novel_dir)

    # 只喂非空行，但保留其在 script 全量数组中的下标作为 storyboard_id（与分镜/图一一对应）
    line_items = [
        LineItem(
            storyboard_id=i,
            text=str(it.get("text", "")).strip(),
            speaker=str(it.get("speaker", "")),
        )
        for i, it in enumerate(script)
        if str(it.get("text", "")).strip()
    ]
    if not line_items:
        raise ValueError(f"render_synthesize_audio: 章节 {chapter_id} script 为空，无可合成文本")
    text = "\n".join(li.text for li in line_items)

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
    result = client.synthesize_full(text, params)

    out_dir = novel_dir_path / chapter_id
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = out_dir / "audio.wav"
    audio_path.write_bytes(result.wav)

    subtitles_path = ""
    sentences_path = ""
    timestamps: list[dict] = []
    if result.sentences is not None:
        sentences_file = out_dir / "sentences.json"
        sentences_file.write_text(
            json.dumps(result.sentences, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        sentences_path = str(sentences_file)

        cues = parse_dots_sentences(result.sentences)
        timestamps = map_cues_to_lines(cues, line_items)

        srt_file = out_dir / "subtitles.srt"
        srt_file.write_text(build_srt(cues), encoding="utf-8")
        subtitles_path = str(srt_file)
    else:
        log.warning(
            "render_synthesize_audio: 未取回句级 sentences.json（服务端句级对齐未开或失败），"
            "本章无字幕/时间戳，图片将无法按时间落位",
            chapter=chapter_id,
        )

    log.info(
        "render_synthesize_audio: 合成完成",
        chapter=chapter_id,
        audio=str(audio_path),
        chars=len(text),
        has_subtitles=bool(subtitles_path),
        timestamps=len(timestamps),
    )
    return {
        "audio_path": str(audio_path),
        "subtitles_path": subtitles_path,
        "sentences_path": sentences_path,
        "timestamps": timestamps,
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

    # 保留合成阶段写入的 subtitles_path / sentences_path，不要用空串覆盖
    existing = dict(chapters_artifacts.get(chapter_id, {}))
    existing.update(
        {
            "audio_path": audio_path or existing.get("audio_path", ""),
            "timeline_path": str(timeline_path),
        }
    )
    existing.setdefault("subtitles_path", "")
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
