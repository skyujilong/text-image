from __future__ import annotations

from pathlib import Path

from langgraph.types import interrupt
from novel2media.chapters import (
    build_chapter_groups,
    chapter_pad_width,
    chapter_sort_key,
)
from novel2media.llm import invoke_llm
from novel2media.prompts._parse import parse_json_array
from novel2media.prompts.init_prompts import build_parse_initial_characters_prompt
from novel2media.prompts.narration_schemes import (
    DEFAULT_SCHEME_KEY,
    default_templates,
    get_scheme,
    list_scheme_presets,
    validate_templates,
)
from novel2media_logging import get_logger

log = get_logger("init_nodes")

# 角色档案必填字段（与 state.CharacterProfileRequired 对齐）
_REQUIRED_CHAR_FIELDS = (
    "name",
    "appearance",
    "character_trait",
    "visual_trait",
    "tri_view_prompt",
    "tri_view_prompt_cn",
)


def load_config(state: dict) -> dict:
    """初始化小说配置状态 + 校验/登记章节文件。

    配置字段从 API params 传入 state（不重新读 config.json——用户在表单中的
    修改会被覆盖；config.json 仅由 novels 端点读取供前端表单回填）。

    章节文件由用户预先按 `chapters/chapter_xxx_ssss.txt`（xxx=章序数字）整理好，
    此处扫描并按数字序存入有序 chapter_files（stem 列表），供下游
    configure_chapter_grouping 节点按用户选择的粒度分组。chapters_status 在此
    置空占位（分组后由 configure_chapter_grouping 按组 id 预填 pending），避免
    以「每文件一 key」污染后续按组处理。无章节目录/无文件时抛错暴露——否则
    后续 load_chapter glob 不到章节会静默返回 END，整个 run 什么都不干，问题难以定位。

    character_profiles（前端 textarea 原文）透传，供 parse_characters_llm 解析。
    setup_queue 不在此预填真实角色（由 review_initial_characters pass 后写入），
    但初始化 setup/control 控制字段为默认值，防旧 state/fork 残留串扰。
    """
    novel_dir = Path(state["novel_dir"])
    novel_title = (
        state.get("novel_title", "")
        or state.get("title", "")
        or state.get("novel_name", "")
        or "未命名小说"
    )

    # 校验 + 扫描章节文件（用户预先按 chapter_xxx_ssss.txt 整理）
    chapters_dir = novel_dir / "chapters"
    if not chapters_dir.is_dir():
        raise FileNotFoundError(
            f"load_config: 章节目录不存在: {chapters_dir}（需预先按 chapters/chapter_xxx_ssss.txt 整理）"
        )
    ch_files = sorted(chapters_dir.glob("*.txt"), key=lambda p: chapter_sort_key(p.stem))
    if not ch_files:
        raise FileNotFoundError(f"load_config: 章节目录无 .txt 文件: {chapters_dir}")
    chapter_files = [f.stem for f in ch_files]

    log.info("load_config 完成", title=novel_title, chapters=len(chapter_files))

    return {
        "novel_title": novel_title,
        "genre": state.get("genre", ""),
        "writing_style": state.get("writing_style", ""),
        "target_audience": state.get("target_audience", ""),
        "core_tone": state.get("core_tone", ""),
        "chapter_word_count": state.get("chapter_word_count", ""),
        "total_word_count": state.get("total_word_count", ""),
        "core_theme": state.get("core_theme", ""),
        "worldview": state.get("world_building", "") or state.get("worldview", ""),
        "core_conflicts": state.get("core_conflicts", ""),
        "overall_outline": state.get("overall_outline", ""),
        "character_profiles": state.get("character_profiles", ""),
        "characters_profile": {},
        "ignored_characters": [],
        # 解说方案默认恐怖悬疑（= 现状）；configure_chapter_grouping 按用户选择/自定义覆盖。
        # 这里预置默认，保证即便 grouping resume 未带该字段，下游也有可用模板（旧 checkpoint 兼容）。
        "narration_scheme": DEFAULT_SCHEME_KEY,
        "narration_templates": default_templates(DEFAULT_SCHEME_KEY),
        # chapters_status 置空占位；configure_chapter_grouping 按组 id 预填 pending
        "chapters_status": {},
        # 有序原始章节文件 stem 列表，供 configure_chapter_grouping 分组消费
        "chapter_files": chapter_files,
        "chapters_artifacts": {},
        # setup/control 字段初始化为默认值（防旧 state/fork 残留串扰路由）
        "setup_queue": [],
        "setup_image_candidates": [],
        "pending_new_characters": [],
        "_init_characters_review": "",
        "_init_characters_feedback": "",
        # chapter 细分审阅控制字段初始化（防旧 state/fork 残留串扰路由）
        "_script_review_decision": "",
        "_script_review_feedback": "",
        "_storyboard_review_decision": "",
        "_storyboard_review_feedback": "",
        "_characters_review_decision": "",
        "_characters_review_feedback": "",
        "_route": "",
        # 全局音频配置（单播，整本书一份；configure_audio 节点配置，跨章节持久）
        "audio_config": {},
        # 渲染批次稿件缓存（规划阶段积累、渲染阶段读取、批次结束清空）
        "render_batch": [],
    }


def configure_chapter_grouping(state: dict) -> dict:
    """interrupt：让用户选择合并粒度 N（1..5，默认1），据此把章节文件切成组。

    R1 原则：interrupt() 之后不做写盘副作用。本节点只读 state（chapter_files）
    + 计算分组并写回 state 字段。

    - 从 load_config 存入的有序 chapter_files 读原始章节文件 stem 列表。
    - interrupt payload 告知前端章节总数 + 默认/最大粒度，供 UI 预览组数。
    - resume 兼容：raw 为 dict 取 raw["group_size"]；为 int 直接用；缺失/其它当默认 1。
    - 校验 group_size 为 1..5 的整数，否则显式抛 ValueError（与其它节点风格一致）。
    - 一次性定死 pad_width（供中途新增文件成单章组复用）+ 计算分组。
    - chapters_status 按组 id 预填 pending（组 = 新原子单元，扮演原 chapter_id）。

    同一交互还让用户选「解说方案」（题材类型）并可自定义其 prompt 模板（仅本次 run）：
    - payload 额外下发 schemes（内置方案含默认模板正文）+ default_scheme，供前端选择/预填/编辑。
    - resume 额外读 narration_scheme（方案 key）+ narration_templates（用户改后的模板对；
      缺失则回退所选方案的内置模板）。模板经 validate_templates 校验（缺必需占位符即抛错）。
    """
    files: list[str] = list(state.get("chapter_files", []))
    raw = interrupt(
        {
            "type": "chapter_grouping",
            "chapter_count": len(files),
            "default_group_size": 1,
            "max_group_size": 5,
            "schemes": list_scheme_presets(),
            "default_scheme": DEFAULT_SCHEME_KEY,
        }
    )

    # 兼容 resume：对象 {group_size:N, narration_scheme, narration_templates} / 纯 int / 缺失当默认 1
    if isinstance(raw, dict):
        group_size = raw.get("group_size", 1)
    elif isinstance(raw, int) and not isinstance(raw, bool):
        group_size = raw
    else:
        group_size = 1

    # 显式校验：必须是 1..5 的整数（bool 是 int 子类，须排除）
    if not isinstance(group_size, int) or isinstance(group_size, bool) or not 1 <= group_size <= 5:
        raise ValueError(
            f"configure_chapter_grouping: 非法 group_size（应为 1..5 的整数）: {group_size!r}"
        )

    # 解说方案：resolve 未知 key 回退默认；模板缺失回退所选方案预设，提供则校验必需占位符。
    raw_dict = raw if isinstance(raw, dict) else {}
    scheme = get_scheme(raw_dict.get("narration_scheme"))
    raw_templates = raw_dict.get("narration_templates")
    if raw_templates is None:
        narration_templates = default_templates(scheme.key)
    else:
        narration_templates = validate_templates(raw_templates)

    pad_width = chapter_pad_width(files)
    groups = build_chapter_groups(files, group_size, pad_width)

    log.info(
        "configure_chapter_grouping: 分组完成",
        group_size=group_size,
        groups=len(groups),
        narration_scheme=scheme.key,
    )
    return {
        "chapter_group_size": group_size,
        "chapter_group_pad_width": pad_width,
        "chapter_groups": groups,
        "chapters_status": {gid: "pending" for gid in groups},
        "narration_scheme": scheme.key,
        "narration_templates": narration_templates,
    }


def parse_characters_llm(state: dict) -> dict:
    """LLM 解析表单预填角色字符串 → 结构化主要角色（含三视图提示词）。

    读 character_profiles（textarea 原文）+ worldview。空 textarea 直接返回空
    pending_new_characters（不调 LLM），由条件边跳过审阅直接 END。
    每个角色必含非空 name/appearance/tri_view_prompt，缺则抛错；重复 name 抛错。
    不落盘草稿（最终档案由 batch_fix_profiles 落盘）。

    revise 回环时读 _init_characters_feedback（review_initial_characters 写入）拼进 prompt，
    用完清空，避免串到下一次解析。
    """
    raw = (state.get("character_profiles") or "").strip()
    if not raw:
        log.info("parse_characters_llm: character_profiles 为空，跳过解析")
        return {"pending_new_characters": []}

    feedback = state.get("_init_characters_feedback", "") or ""
    prompt = build_parse_initial_characters_prompt(raw, state.get("worldview", ""), feedback)
    resp = invoke_llm(prompt, node="parse_characters_llm", json_mode=True)
    parsed = parse_json_array(resp)  # [{name, appearance, tri_view_prompt}]

    seen: set[str] = set()
    for c in parsed:
        for field in _REQUIRED_CHAR_FIELDS:
            if not c.get(field):
                raise ValueError(f"parse_characters_llm: 角色缺 {field} 字段: {c}")
        name = c["name"]
        if name in seen:
            raise ValueError(f"parse_characters_llm: 重复角色名: {name}")
        seen.add(name)

    # feedback 记录原文（与 prompt_chars 同条，便于核对 revise 意见是否真拼进 prompt）
    log.info("parse_characters_llm: 完成", count=len(parsed), feedback=feedback)
    return {"pending_new_characters": parsed, "_init_characters_feedback": ""}


def review_initial_characters(state: dict) -> dict:
    """interrupt：人工审阅 LLM 解析出的初始主要角色，resume 为 {"decision","feedback"}。

    R1 原则：interrupt() 之后不做写盘副作用。本节点只读 state + 写 state 字段。
    - pass：把 pending_new_characters 转入 setup_queue（交给 character_setup_subgraph
      逐个上传三视图 + 音色），清空 pending。
    - revise：回 parse_characters_llm 重解析，并把用户修改意见写入 _init_characters_feedback。
    - 非法 resume 值：显式抛错，不静默当 pass。

    resume 兼容：旧 checkpoint 可能 resume 纯字符串 "pass"/"revise"（无意见），
    此处按 decision 解释、feedback 视为空；非法值仍抛错。
    """
    raw = interrupt(
        {
            "type": "initial_characters_review",
            "characters": state.get("pending_new_characters", []),
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
        log.info("review_initial_characters: 打回重解析", feedback=feedback)
        return {"_init_characters_review": "revise", "_init_characters_feedback": feedback}

    if decision != "pass":
        raise ValueError(f"review_initial_characters: 非法 resume 值（应为 pass/revise）: {raw!r}")

    queue = list(state.get("pending_new_characters", []))
    log.info("review_initial_characters: 审核通过", count=len(queue))
    return {
        "_init_characters_review": "pass",
        "setup_queue": queue,
        "pending_new_characters": [],
        # pass 时清空反馈，防上一轮 revise 残留串到下次解析
        "_init_characters_feedback": "",
    }
