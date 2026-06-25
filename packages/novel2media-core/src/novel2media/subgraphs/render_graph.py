from __future__ import annotations

from langgraph.graph import END, StateGraph
from novel2media.nodes.chapter_nodes import (
    configure_audio,
    export_to_jianying,
    final_decision,
    render_build_timeline,
    render_dispatch,
    render_generate_images,
    render_synthesize_audio,
)
from novel2media.state import RenderGraphState


def _has_planned(state: RenderGraphState) -> bool:
    """是否存在 status=planned 的章节（待渲染）。"""
    return any(st == "planned" for st in state.get("chapters_status", {}).values())


def _route_render_dispatch(state: RenderGraphState) -> str:
    """渲染调度入口：有 planned→开始渲染；无→直接导出。"""
    return "render_generate_images" if _has_planned(state) else "export_to_jianying"


def _route_render(state: RenderGraphState) -> str:
    """渲染循环路由：仍有 planned→继续渲染下一章；无→导出。"""
    return "render_dispatch" if _has_planned(state) else "export_to_jianying"


def build_render_graph(checkpointer=None):
    """render_graph：生图 + 生音频 + 合成视频 + 导出。

    渲染阶段（含跨章循环）：
      configure_audio（已配则跳过 interrupt）→ render_dispatch
        →(有 planned→render_generate_images | 无→export_to_jianying)
      render_generate_images → render_synthesize_audio → render_build_timeline
        →(有 planned→render_dispatch | 无→export_to_jianying)
      export_to_jianying → final_decision → END

    作为独立顶层图编译，拥有完整内部 checkpoint 历史，支持精准回溯到任意节点。
    graph_runner 在 render_graph 到达 END 后检查主图游标，决定继续规划或结束。
    """
    builder = StateGraph(RenderGraphState)

    builder.add_node("configure_audio", configure_audio)
    builder.add_node("render_dispatch", render_dispatch)
    builder.add_node("render_generate_images", render_generate_images)
    builder.add_node("render_synthesize_audio", render_synthesize_audio)
    builder.add_node("render_build_timeline", render_build_timeline)
    builder.add_node("export_to_jianying", export_to_jianying)
    builder.add_node("final_decision", final_decision)

    builder.set_entry_point("configure_audio")

    builder.add_edge("configure_audio", "render_dispatch")
    builder.add_conditional_edges(
        "render_dispatch",
        _route_render_dispatch,
        {
            "render_generate_images": "render_generate_images",
            "export_to_jianying": "export_to_jianying",
        },
    )
    builder.add_edge("render_generate_images", "render_synthesize_audio")
    builder.add_edge("render_synthesize_audio", "render_build_timeline")
    builder.add_conditional_edges(
        "render_build_timeline",
        _route_render,
        {"render_dispatch": "render_dispatch", "export_to_jianying": "export_to_jianying"},
    )

    builder.add_edge("export_to_jianying", "final_decision")
    builder.add_edge("final_decision", END)

    return builder.compile(checkpointer=checkpointer)
