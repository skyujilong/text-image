from __future__ import annotations

from langgraph.graph import END, StateGraph
from novel2media.nodes.chapter_nodes import (
    adapt_script,
    chapter_advance_decision,
    commit_chapter,
    configure_audio,
    detect_new_characters_llm,
    export_to_jianying,
    final_decision,
    generate_storyboard,
    load_chapter,
    render_build_timeline,
    render_dispatch,
    render_generate_images,
    render_synthesize_audio,
    review_script,
    review_storyboard,
)
from novel2media.state import ChapterSubgraphState, GraphState
from novel2media.subgraphs.setup import character_setup_subgraph_compiled


def _route_load_chapter(state: GraphState) -> str:
    """无待处理章节 → END；否则进入 adapt_script 开始规划。"""
    return END if not state.get("current_chapter_id") else "adapt_script"


def _route_review_script(state: GraphState) -> str:
    """剧本审阅路由：revise→重写剧本；pass→检测本章新角色（分镜之前）。"""
    return "adapt_script" if state.get("_script_review_decision") == "revise" else "detect_new_characters_llm"


def _route_after_detect(state: GraphState) -> str:
    """新角色检测后路由：有新角色→先做角色设定（备好特征再分镜）；无→直接分镜。

    新角色由 detect_new_characters_llm 写入 setup_queue。在分镜之前进 character_setup_subgraph
    补三视图 + 落 characters_profile，确保 generate_storyboard 能拿到新角色 visual_trait，
    避免后期图生图错乱。
    """
    if state.get("setup_queue"):
        return "character_setup_subgraph"
    return "generate_storyboard"


def _route_review_storyboard(state: GraphState) -> str:
    """分镜审阅路由：revise→重生成分镜；pass→提交本章规划。"""
    return "generate_storyboard" if state.get("_storyboard_review_decision") == "revise" else "commit_chapter"


def _route_chapter_advance(state: GraphState) -> str:
    """章节推进路由：render→配置音色（已配自动跳过）后进入批量渲染；其它（next/空）→继续规划下一章。"""
    return "configure_audio" if state.get("_chapter_advance") == "render" else "load_chapter"


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
    """两阶段 chapter 子图：规划阶段（LLM+细分审阅+推进）+ 渲染阶段（顺序循环）。

    规划：load_chapter → adapt_script（只出脚本）→ review_script
          →(revise→adapt_script | pass→detect_new_characters_llm)
          detect_new_characters_llm（写新角色 setup_queue）
          →(有新角色→character_setup_subgraph | 无→generate_storyboard)
          character_setup_subgraph（分镜前补三视图）→ generate_storyboard
          → review_storyboard →(generate_storyboard | commit_chapter) → commit_chapter
    细分审阅各自 revise 回到对应生成节点（精准回环，注入 feedback）；
    新角色检测独立成节点放分镜之前（合并进 adapt_script 会让单次输出过长被截断），
    检测后若有新角色先进 character_setup_subgraph 备好特征再分镜，从根上避免分镜/图生图角色对不上。
    均 pass 后 commit_chapter 统一提交（planned/render_batch）。
    推进：commit_chapter → chapter_advance_decision →(load_chapter | configure_audio → render_dispatch)
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
    builder.add_node("review_script", review_script)
    builder.add_node("detect_new_characters_llm", detect_new_characters_llm)
    builder.add_node("generate_storyboard", generate_storyboard)
    builder.add_node("review_storyboard", review_storyboard)
    builder.add_node("commit_chapter", commit_chapter)
    builder.add_node("character_setup_subgraph", character_setup_subgraph_compiled)
    builder.add_node("chapter_advance_decision", chapter_advance_decision)
    builder.add_node("configure_audio", configure_audio)
    # 渲染阶段节点
    builder.add_node("render_dispatch", render_dispatch)
    builder.add_node("render_generate_images", render_generate_images)
    builder.add_node("render_synthesize_audio", render_synthesize_audio)
    builder.add_node("render_build_timeline", render_build_timeline)
    # 收尾节点
    builder.add_node("export_to_jianying", export_to_jianying)
    builder.add_node("final_decision", final_decision)

    builder.set_entry_point("load_chapter")

    # 规划阶段边（每步生成后接细分审阅，审阅 revise 回到对应生成节点）
    builder.add_conditional_edges(
        "load_chapter", _route_load_chapter, {"adapt_script": "adapt_script", END: END}
    )
    builder.add_edge("adapt_script", "review_script")
    # review_script pass → 检测新角色（分镜之前）
    builder.add_conditional_edges(
        "review_script",
        _route_review_script,
        {"adapt_script": "adapt_script", "detect_new_characters_llm": "detect_new_characters_llm"},
    )
    # 检测后：有新角色先进角色设定（分镜前备好特征），否则直接分镜
    builder.add_conditional_edges(
        "detect_new_characters_llm",
        _route_after_detect,
        {
            "character_setup_subgraph": "character_setup_subgraph",
            "generate_storyboard": "generate_storyboard",
        },
    )
    # 角色设定（补三视图 + 落 characters_profile）完成后进入分镜生成
    builder.add_edge("character_setup_subgraph", "generate_storyboard")
    builder.add_edge("generate_storyboard", "review_storyboard")
    builder.add_conditional_edges(
        "review_storyboard",
        _route_review_storyboard,
        {"generate_storyboard": "generate_storyboard", "commit_chapter": "commit_chapter"},
    )
    builder.add_edge("commit_chapter", "chapter_advance_decision")
    builder.add_conditional_edges(
        "chapter_advance_decision",
        _route_chapter_advance,
        {"load_chapter": "load_chapter", "configure_audio": "configure_audio"},
    )
    builder.add_edge("configure_audio", "render_dispatch")

    # 渲染阶段边（顺序循环 + checkpoint 续跑）
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

    # 收尾边
    builder.add_edge("export_to_jianying", "final_decision")
    builder.add_conditional_edges(
        "final_decision",
        _route_final,
        {END: END, "load_chapter": "load_chapter"},
    )

    return builder.compile(checkpointer=checkpointer)
