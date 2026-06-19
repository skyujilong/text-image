from __future__ import annotations

from langgraph.graph import END, StateGraph
from novel2media.nodes.setup_nodes import (
    fix_character_profile,
    setup_dispatcher,
    upload_tri_view,
    voice_card_draw,
    voice_params_choice,
    voice_params_manual,
)
from novel2media.state import SetupSubgraphState


def _route_after_dispatcher(state: SetupSubgraphState) -> str:
    """队列为空 → 退出子图；否则进入 upload_tri_view 上传三视图。"""
    char = state.get("setup_current_character", {})
    if not char:
        return END
    return "upload_tri_view"


def _route_after_voice_choice(state: SetupSubgraphState) -> str:
    """音色参数方式：manual→手动填写；draw（默认）→抽卡。"""
    return state.get("_voice_route", "voice_card_draw")


def _route_after_manual_review(state: SetupSubgraphState) -> str:
    """手动音色审核：pass→确认档案；revise→重调（adjust 回 manual，redraw 转 card_draw）。"""
    decision = state.get("_manual_review", "pass")
    if decision == "pass":
        return "fix_character_profile"
    retry = state.get("_manual_retry", "adjust")
    return "voice_params_manual" if retry == "adjust" else "voice_card_draw"


def _route_after_card_draw(state: SetupSubgraphState) -> str:
    """抽卡结果：选定→确认档案；未选定→重抽（TTS 空走时固定选定以避免死循环，见 step 06）。"""
    if state.get("_card_selected"):
        return "fix_character_profile"
    return "voice_card_draw"


def build_character_setup_subgraph():
    """character_setup 子图：对每个新角色上传三视图（可选）+ 设定音色参数。

    链路：setup_dispatcher → upload_tri_view → voice_params_choice
          →(voice_params_manual | voice_card_draw)→ fix_character_profile → setup_dispatcher（循环）
    队列空时 setup_dispatcher → END。

    立绘改为上传三视图（砍掉 ComfyUI 抽卡 + selector）；voice 三件套保留（step 06 补 interrupt）。
    """
    builder = StateGraph(SetupSubgraphState)

    builder.add_node("setup_dispatcher", setup_dispatcher)
    builder.add_node("upload_tri_view", upload_tri_view)
    # 音色参数阶段
    builder.add_node("voice_params_choice", voice_params_choice)
    builder.add_node("voice_params_manual", voice_params_manual)
    builder.add_node("voice_card_draw", voice_card_draw)
    builder.add_node("fix_character_profile", fix_character_profile)

    builder.set_entry_point("setup_dispatcher")

    builder.add_conditional_edges(
        "setup_dispatcher", _route_after_dispatcher, {"upload_tri_view": "upload_tri_view", END: END}
    )
    builder.add_edge("upload_tri_view", "voice_params_choice")

    # 音色参数阶段
    builder.add_conditional_edges(
        "voice_params_choice",
        _route_after_voice_choice,
        {"voice_params_manual": "voice_params_manual", "voice_card_draw": "voice_card_draw"},
    )
    builder.add_conditional_edges(
        "voice_params_manual",
        _route_after_manual_review,
        {
            "fix_character_profile": "fix_character_profile",
            "voice_params_manual": "voice_params_manual",
            "voice_card_draw": "voice_card_draw",
        },
    )
    builder.add_conditional_edges(
        "voice_card_draw",
        _route_after_card_draw,
        {"fix_character_profile": "fix_character_profile", "voice_card_draw": "voice_card_draw"},
    )
    builder.add_edge("fix_character_profile", "setup_dispatcher")  # 内部循环

    return builder.compile()


# 模块级单例（R4/R10）：init_graph / chapter / graph 三处必须引用此同一编译对象。
# 多实例会导致 LangGraph checkpoint namespace 不一致，fork/inspect 找错 checkpoint。
# 任意调用方应直接引用 character_setup_subgraph_compiled，不要再各自 build_character_setup_subgraph()。
character_setup_subgraph_compiled = build_character_setup_subgraph()
