from __future__ import annotations

from pathlib import Path

from langgraph.types import interrupt
from novel2media.chapters import chapter_sort_key
from novel2media.llm import invoke_llm
from novel2media.prompts._parse import parse_json_array
from novel2media.prompts.init_prompts import build_parse_initial_characters_prompt
from novel2media_logging import get_logger

log = get_logger("init_nodes")

# 角色档案必填字段（与 state.CharacterProfileRequired 对齐）
_REQUIRED_CHAR_FIELDS = ("name", "appearance", "tri_view_prompt")


def load_config(state: dict) -> dict:
    """初始化小说配置状态 + 校验/登记章节文件。

    配置字段从 API params 传入 state（不重新读 config.json——用户在表单中的
    修改会被覆盖；config.json 仅由 novels 端点读取供前端表单回填）。

    章节文件由用户预先按 `chapters/chapter_xxx_ssss.txt`（xxx=章序数字）整理好，
    此处扫描并按数字序预填 chapters_status 全 pending。无章节目录/无文件时
    抛错暴露——否则后续 load_chapter glob 不到章节会静默返回 END，整个 run
    什么都不干，问题难以定位。

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
    chapters_status = {f.stem: "pending" for f in ch_files}

    log.info("load_config 完成", title=novel_title, chapters=len(chapters_status))

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
        "chapters_status": chapters_status,
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
    resp = invoke_llm(prompt, node="parse_characters_llm")
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

    log.info("parse_characters_llm: 完成", count=len(parsed), feedback=bool(feedback))
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
        log.info("review_initial_characters: 打回重解析", feedback=bool(feedback))
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
