from __future__ import annotations
from langgraph.graph import StateGraph, END
from novel2media.state import GraphState
from novel2media.nodes.setup_nodes import (
    setup_dispatcher,
    check_needs_visual,
    generate_portrait_candidates,
    portrait_selector,
    fix_character_visual,
    generate_fullbody_candidates,
    fullbody_selector,
    voice_params_choice,
    voice_params_manual,
    voice_card_draw,
    fix_character_profile,
)


def _route_after_dispatcher(state: GraphState) -> str:
    char = state.get("setup_current_character", {})
    if not char:
        return END
    return "check_needs_visual"


def _route_after_check_visual(state: GraphState) -> str:
    route = state.get("_route", "voice_params_choice")
    if route != "image_card_draw":
        return "voice_params_choice"
    # resume 场景：候选图已存在，直接跳到 selector
    return "portrait_selector" if state.get("setup_image_candidates") else "generate_portrait_candidates"


def _route_after_fix_character_visual(state: GraphState) -> str:
    # resume 场景：全身候选图已存在，跳过生成
    return "fullbody_selector" if state.get("setup_image_candidates") else "generate_fullbody_candidates"


def _route_after_voice_choice(state: GraphState) -> str:
    return state.get("_voice_route", "voice_card_draw")


def _route_after_manual_review(state: GraphState) -> str:
    decision = state.get("_manual_review", "pass")
    if decision == "pass":
        return "fix_character_profile"
    retry = state.get("_manual_retry", "adjust")
    return "voice_params_manual" if retry == "adjust" else "voice_card_draw"


def _route_after_card_draw(state: GraphState) -> str:
    if state.get("_card_selected"):
        return "fix_character_profile"
    return "voice_card_draw"  # 全部拒绝 → 重抽


def build_character_setup_subgraph():
    builder = StateGraph(GraphState)

    builder.add_node("setup_dispatcher", setup_dispatcher)
    builder.add_node("check_needs_visual", check_needs_visual)
    # 大头照阶段
    builder.add_node("generate_portrait_candidates", generate_portrait_candidates)
    builder.add_node("portrait_selector", portrait_selector)
    builder.add_node("fix_character_visual", fix_character_visual)
    # 全身立绘阶段
    builder.add_node("generate_fullbody_candidates", generate_fullbody_candidates)
    builder.add_node("fullbody_selector", fullbody_selector)
    # 语音参数阶段
    builder.add_node("voice_params_choice", voice_params_choice)
    builder.add_node("voice_params_manual", voice_params_manual)
    builder.add_node("voice_card_draw", voice_card_draw)
    builder.add_node("fix_character_profile", fix_character_profile)

    builder.set_entry_point("setup_dispatcher")

    builder.add_conditional_edges("setup_dispatcher", _route_after_dispatcher,
                                  {"check_needs_visual": "check_needs_visual", END: END})
    builder.add_conditional_edges("check_needs_visual", _route_after_check_visual,
                                  {"generate_portrait_candidates": "generate_portrait_candidates",
                                   "portrait_selector": "portrait_selector",
                                   "voice_params_choice": "voice_params_choice"})

    # 大头照：生成 → 选择 → 确认
    builder.add_edge("generate_portrait_candidates", "portrait_selector")
    builder.add_edge("portrait_selector", "fix_character_visual")
    builder.add_conditional_edges("fix_character_visual", _route_after_fix_character_visual,
                                  {"generate_fullbody_candidates": "generate_fullbody_candidates",
                                   "fullbody_selector": "fullbody_selector"})

    # 全身立绘：生成 → 选择 → 语音参数
    builder.add_edge("generate_fullbody_candidates", "fullbody_selector")
    builder.add_edge("fullbody_selector", "voice_params_choice")

    # 语音参数阶段
    builder.add_conditional_edges("voice_params_choice", _route_after_voice_choice,
                                  {"voice_params_manual": "voice_params_manual",
                                   "voice_card_draw": "voice_card_draw"})
    builder.add_conditional_edges("voice_params_manual", _route_after_manual_review,
                                  {"fix_character_profile": "fix_character_profile",
                                   "voice_params_manual": "voice_params_manual",
                                   "voice_card_draw": "voice_card_draw"})
    builder.add_conditional_edges("voice_card_draw", _route_after_card_draw,
                                  {"fix_character_profile": "fix_character_profile",
                                   "voice_card_draw": "voice_card_draw"})
    builder.add_edge("fix_character_profile", "setup_dispatcher")  # 内部循环

    return builder.compile()
