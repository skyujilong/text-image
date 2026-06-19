from __future__ import annotations

from langgraph.graph import END, StateGraph
from novel2media.nodes.chapter_nodes import (
    adapt_script,
    chapter_advance_decision,
    detect_new_characters_llm,
    export_to_jianying,
    final_decision,
    generate_storyboard,
    load_chapter,
    render_build_timeline,
    render_dispatch,
    render_generate_images,
    render_synthesize_audio,
    review_chapter,
)
from novel2media.state import ChapterSubgraphState, GraphState
from novel2media.subgraphs.setup import character_setup_subgraph_compiled


def _route_load_chapter(state: GraphState) -> str:
    """无待处理章节 → END；否则进入 adapt_script 开始规划。"""
    return END if not state.get("current_chapter_id") else "adapt_script"


def _route_review(state: GraphState) -> str:
    """review_chapter 审核路由：revise→重写剧本；pass+有新角色→角色设定；pass+无→推进决策。"""
    decision = state.get("_review_decision", "")
    if decision == "revise":
        return "adapt_script"
    if state.get("setup_queue"):
        return "character_setup_subgraph"
    return "chapter_advance_decision"


def _route_chapter_advance(state: GraphState) -> str:
    """章节推进路由：render→进入批量渲染；其它（next/空）→继续规划下一章。"""
    return "render_dispatch" if state.get("_chapter_advance") == "render" else "load_chapter"


def _has_planned(state: GraphState) -> bool:
    """是否存在 status=planned 的章节（待渲染）。"""
    return any(st == "planned" for st in state.get("chapters_status", {}).values())


def _route_render_dispatch(state: GraphState) -> str:
    """渲染调度入口：有 planned→开始渲染；无→直接导出。"""
    return "render_generate_images" if _has_planned(state) else "export_to_jianying"


def _route_render(state: GraphState) -> str:
    """渲染循环路由：仍有 planned→继续渲染下一章；无→导出。"""
    return "render_dispatch" if _has_planned(state) else "export_to_jianying"


def _route_final(state: GraphState) -> str:
    """最终决策路由：done→END；其它（continue/空）→回 load_chapter 继续规划（交错）。"""
    return END if state.get("_final_decision") == "done" else "load_chapter"


def build_chapter_subgraph(checkpointer=None):
    """两阶段 chapter 子图：规划阶段（LLM+审核+推进）+ 渲染阶段（顺序循环）。

    规划：load_chapter → adapt_script → generate_storyboard → detect_new_characters_llm
          → review_chapter →(character_setup_subgraph | chapter_advance_decision)
    推进：chapter_advance_decision →(load_chapter | render_dispatch)
    渲染：render_dispatch → render_generate_images → render_synthesize_audio
          → render_build_timeline →(render_dispatch | export_to_jianying)
    收尾：export_to_jianying → final_decision →(END | load_chapter)

    checkplayer 可选：主图作子图节点时由父图统一注入；独立测试可传 MemorySaver
    以支持 interrupt/resume 跨 invoke。
    """
    builder = StateGraph(ChapterSubgraphState)

    # 规划阶段节点
    builder.add_node("load_chapter", load_chapter)
    builder.add_node("adapt_script", adapt_script)
    builder.add_node("generate_storyboard", generate_storyboard)
    builder.add_node("detect_new_characters_llm", detect_new_characters_llm)
    builder.add_node("review_chapter", review_chapter)
    builder.add_node("character_setup_subgraph", character_setup_subgraph_compiled)
    builder.add_node("chapter_advance_decision", chapter_advance_decision)
    # 渲染阶段节点
    builder.add_node("render_dispatch", render_dispatch)
    builder.add_node("render_generate_images", render_generate_images)
    builder.add_node("render_synthesize_audio", render_synthesize_audio)
    builder.add_node("render_build_timeline", render_build_timeline)
    # 收尾节点
    builder.add_node("export_to_jianying", export_to_jianying)
    builder.add_node("final_decision", final_decision)

    builder.set_entry_point("load_chapter")

    # 规划阶段边
    builder.add_conditional_edges(
        "load_chapter", _route_load_chapter, {"adapt_script": "adapt_script", END: END}
    )
    builder.add_edge("adapt_script", "generate_storyboard")
    builder.add_edge("generate_storyboard", "detect_new_characters_llm")
    builder.add_edge("detect_new_characters_llm", "review_chapter")
    builder.add_conditional_edges(
        "review_chapter",
        _route_review,
        {
            "adapt_script": "adapt_script",
            "character_setup_subgraph": "character_setup_subgraph",
            "chapter_advance_decision": "chapter_advance_decision",
        },
    )
    builder.add_edge("character_setup_subgraph", "chapter_advance_decision")
    builder.add_conditional_edges(
        "chapter_advance_decision",
        _route_chapter_advance,
        {"load_chapter": "load_chapter", "render_dispatch": "render_dispatch"},
    )

    # 渲染阶段边（顺序循环 + checkpoint 续跑）
    builder.add_conditional_edges(
        "render_dispatch",
        _route_render_dispatch,
        {"render_generate_images": "render_generate_images", "export_to_jianying": "export_to_jianying"},
    )
    builder.add_edge("render_generate_images", "render_synthesize_audio")
    builder.add_edge("render_synthesize_audio", "render_build_timeline")
    builder.add_conditional_edges(
        "render_build_timeline",
        _route_render,
        {"render_dispatch": "render_dispatch", "export_to_jianying": "export_to_jianying"},
    )

    # 收尾边
    builder.add_edge("export_to_jianying", "final_decision")
    builder.add_conditional_edges(
        "final_decision",
        _route_final,
        {END: END, "load_chapter": "load_chapter"},
    )

    return builder.compile(checkpointer=checkpointer)
