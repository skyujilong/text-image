from __future__ import annotations

from langgraph.graph import END, StateGraph
from novel2media.nodes.setup_nodes import (
    batch_fix_profiles,
    batch_upload_tri_view,
    setup_dispatcher,
)
from novel2media.state import SetupSubgraphState


def _route_after_dispatcher(state: SetupSubgraphState) -> str:
    """队列为空 → 退出子图；否则进入 batch_upload_tri_view 批量上传三视图。"""
    if not state.get("setup_queue"):
        return END
    return "batch_upload_tri_view"


def build_character_setup_subgraph():
    """character_setup 子图（批量化）：对一批新角色一次上传三视图 + 批量落盘档案。

    链路：setup_dispatcher → batch_upload_tri_view → batch_fix_profiles → END。
    队列空时 setup_dispatcher → END。

    批量化：一次 interrupt 传全部角色三视图（不再逐角色循环）。
    音色配置已移出本子图——单播模式下音色是全局一份，由 chapter 子图 render 前
    的 configure_audio 节点配置（MainGraphState.audio_config）。
    """
    builder = StateGraph(SetupSubgraphState)

    builder.add_node("setup_dispatcher", setup_dispatcher)
    builder.add_node("batch_upload_tri_view", batch_upload_tri_view)
    builder.add_node("batch_fix_profiles", batch_fix_profiles)

    builder.set_entry_point("setup_dispatcher")

    builder.add_conditional_edges(
        "setup_dispatcher",
        _route_after_dispatcher,
        {"batch_upload_tri_view": "batch_upload_tri_view", END: END},
    )
    builder.add_edge("batch_upload_tri_view", "batch_fix_profiles")
    builder.add_edge("batch_fix_profiles", END)

    return builder.compile()


# 模块级单例（R4/R10）：init_graph / chapter / graph 三处必须引用此同一编译对象。
# 多实例会导致 LangGraph checkpoint namespace 不一致，fork/inspect 找错 checkpoint。
# 任意调用方应直接引用 character_setup_subgraph_compiled，不要再各自 build_character_setup_subgraph()。
character_setup_subgraph_compiled = build_character_setup_subgraph()
