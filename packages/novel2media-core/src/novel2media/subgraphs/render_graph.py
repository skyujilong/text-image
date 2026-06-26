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
    wait_for_server_ready,
)
from novel2media.state import RenderGraphState


def _has_planned(state: RenderGraphState) -> bool:
    """是否存在 status=planned 的章节（待渲染）。"""
    return any(st == "planned" for st in state.get("chapters_status", {}).values())


def _route_render_dispatch(state: RenderGraphState) -> str:
    """渲染调度入口：有 planned→开始渲染；无→直接导出。

    render_dispatch 已选取章节并写入 current_chapter_id/current_script/current_storyboard。
    有 planned→进入音频服务器确认流程；无→直接去导出。
    """
    return "wait_audio_server" if _has_planned(state) else "export_to_jianying"


def _route_render(state: RenderGraphState) -> str:
    """渲染循环路由：仍有 planned→继续渲染下一章；无→导出。"""
    return "render_dispatch" if _has_planned(state) else "export_to_jianying"


def wait_audio_server(state: dict) -> dict:
    """音频合成前：确认 TTS 服务器已就绪（租赁服务器成本控制）。"""
    return wait_for_server_ready(state, "audio_synthesis")


def wait_image_server(state: dict) -> dict:
    """图像生成前：确认 ComfyUI 服务器已就绪（租赁服务器成本控制）。"""
    return wait_for_server_ready(state, "image_render")


def build_render_graph(checkpointer=None):
    """render_graph：生图 + 生音频 + 合成视频 + 导出。

    渲染阶段（含跨章循环 + 租赁服务器确认节点）：
      render_dispatch（选章写 current_*）
        →(有 planned→wait_audio_server | 无→export_to_jianying)
      wait_audio_server（确认 TTS 就绪）→ configure_audio（音色配置）
        → render_synthesize_audio（合成音频）→ wait_image_server（确认 GPU 就绪）
        → render_generate_images（生成图片）→ render_build_timeline
        →(有 planned→render_dispatch 循环 | 无→export_to_jianying)
      export_to_jianying → final_decision → END

    服务器确认节点用于租赁场景：在耗时操作前人工确认服务器已启动，避免自动跑浪费租期。

    作为独立顶层图编译，拥有完整内部 checkpoint 历史，支持精准回溯到任意节点。
    graph_runner 在 render_graph 到达 END 后检查主图游标，决定继续规划或结束。
    """
    builder = StateGraph(RenderGraphState)

    builder.add_node("render_dispatch", render_dispatch)
    builder.add_node("wait_audio_server", wait_audio_server)
    builder.add_node("configure_audio", configure_audio)
    builder.add_node("render_synthesize_audio", render_synthesize_audio)
    builder.add_node("wait_image_server", wait_image_server)
    builder.add_node("render_generate_images", render_generate_images)
    builder.add_node("render_build_timeline", render_build_timeline)
    builder.add_node("export_to_jianying", export_to_jianying)
    builder.add_node("final_decision", final_decision)

    builder.set_entry_point("render_dispatch")

    # render_dispatch → 条件：有 planned 走音频确认，无则直接导出
    builder.add_conditional_edges(
        "render_dispatch",
        _route_render_dispatch,
        {
            "wait_audio_server": "wait_audio_server",
            "export_to_jianying": "export_to_jianying",
        },
    )
    # 音频服务器确认 → 配置音色 → 合成音频 → 图像服务器确认 → 生成图片 → build_timeline
    builder.add_edge("wait_audio_server", "configure_audio")
    builder.add_edge("configure_audio", "render_synthesize_audio")
    builder.add_edge("render_synthesize_audio", "wait_image_server")
    builder.add_edge("wait_image_server", "render_generate_images")
    builder.add_edge("render_generate_images", "render_build_timeline")

    # build_timeline → 条件：还有 planned 继续循环，无则导出
    builder.add_conditional_edges(
        "render_build_timeline",
        _route_render,
        {"render_dispatch": "render_dispatch", "export_to_jianying": "export_to_jianying"},
    )

    builder.add_edge("export_to_jianying", "final_decision")
    builder.add_edge("final_decision", END)

    return builder.compile(checkpointer=checkpointer)
