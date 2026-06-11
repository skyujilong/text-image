from __future__ import annotations
from langgraph.graph import StateGraph, END
from novel2media.state import GraphState
from novel2media.nodes.setup_nodes import (
    setup_dispatcher,
    check_needs_visual,
    image_card_draw,
    fix_character_visual,
    fullbody_card_draw,
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
    return state.get("_route", "voice_params_choice")


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
    builder.add_node("image_card_draw", image_card_draw)
    builder.add_node("fix_character_visual", fix_character_visual)
    builder.add_node("fullbody_card_draw", fullbody_card_draw)
    builder.add_node("voice_params_choice", voice_params_choice)
    builder.add_node("voice_params_manual", voice_params_manual)
    builder.add_node("voice_card_draw", voice_card_draw)
    builder.add_node("fix_character_profile", fix_character_profile)

    builder.set_entry_point("setup_dispatcher")

    builder.add_conditional_edges("setup_dispatcher", _route_after_dispatcher,
                                  {"check_needs_visual": "check_needs_visual", END: END})
    builder.add_conditional_edges("check_needs_visual", _route_after_check_visual,
                                  {"image_card_draw": "image_card_draw",
                                   "voice_params_choice": "voice_params_choice"})
    # 大头照 → 确认 → 全身立绘 → 语音参数
    builder.add_edge("image_card_draw", "fix_character_visual")
    builder.add_edge("fix_character_visual", "fullbody_card_draw")
    builder.add_edge("fullbody_card_draw", "voice_params_choice")

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
