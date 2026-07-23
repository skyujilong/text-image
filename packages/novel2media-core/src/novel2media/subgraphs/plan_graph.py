from __future__ import annotations

from langgraph.graph import END, StateGraph
from novel2media.nodes.chapter_nodes import (
    adapt_script,
    chapter_advance_decision,
    commit_chapter,
    detect_new_characters_llm,
    detect_new_scenes_llm,
    generate_storyboard,
    load_chapter,
    review_script,
    review_storyboard,
)
from novel2media.state import PlanGraphState
from novel2media.subgraphs.setup import character_setup_subgraph_compiled


def _route_load_chapter(state: PlanGraphState) -> str:
    """无待处理章节 → END；否则进入 adapt_script 开始规划。"""
    return END if not state.get("current_chapter_id") else "adapt_script"


def _route_review_script(state: PlanGraphState) -> str:
    """剧本审阅路由：revise→重写剧本；pass→检测本章新角色（分镜之前）。"""
    return "adapt_script" if state.get("_script_review_decision") == "revise" else "detect_new_characters_llm"


def _route_after_detect(state: PlanGraphState) -> str:
    """新角色检测后路由：有新角色→先做角色设定（备好特征再分镜）；无→直接分镜。

    新角色由 detect_new_characters_llm 写入 setup_queue。在分镜之前进 character_setup_subgraph
    补三视图 + 落 characters_profile，确保 generate_storyboard 能拿到新角色 visual_trait，
    避免后期图生图错乱。
    """
    if state.get("setup_queue"):
        return "character_setup_subgraph"
    return "generate_storyboard"


def _route_review_storyboard(state: PlanGraphState) -> str:
    """分镜审阅路由：revise→重生成分镜；pass→提交本章规划。"""
    return "generate_storyboard" if state.get("_storyboard_review_decision") == "revise" else "commit_chapter"


def _route_chapter_advance(state: PlanGraphState) -> str:
    """规划图推进路由。

    render（前端唯一发送值）→ END plan 子图 → graph_runner 提取 shared 字段合并回主图
    （render_batch/chapters_status 刷回主图 → 渲染工作台可见可开渲）→ 主图 _has_planned_chapters
    若还有 pending 章则重委派继续规划下一章，无则整体 END。即「刷批次 + 继续规划」。
    next（旧值，UI 已不再发送，保留兼容）→ 留在本子图 load_chapter 循环（批次不刷回主图）。
    """
    return END if state.get("_chapter_advance") == "render" else "load_chapter"


def build_plan_graph(checkpointer=None):
    """plan_graph：文本 → script + storyboard，产出 planned 章节 + render_batch。

    规划阶段（含跨章循环）：
      load_chapter → adapt_script → review_script
        →(revise→adapt_script | pass→detect_new_characters_llm)
      detect_new_characters_llm（写新角色 setup_queue）
        →(有新角色→character_setup_subgraph | 无→generate_storyboard)
      character_setup_subgraph（分镜前补三视图）→ generate_storyboard
        → review_storyboard →(generate_storyboard | commit_chapter) → commit_chapter
      commit_chapter → chapter_advance_decision
        →(next→load_chapter | render→END)

    作为独立顶层图编译，拥有完整内部 checkpoint 历史，支持精准回溯到任意节点。
    """
    builder = StateGraph(PlanGraphState)

    # 规划阶段节点
    builder.add_node("load_chapter", load_chapter)
    builder.add_node("adapt_script", adapt_script)
    builder.add_node("review_script", review_script)
    builder.add_node("detect_new_characters_llm", detect_new_characters_llm)
    builder.add_node("detect_new_scenes_llm", detect_new_scenes_llm)
    builder.add_node("generate_storyboard", generate_storyboard)
    builder.add_node("review_storyboard", review_storyboard)
    builder.add_node("commit_chapter", commit_chapter)
    builder.add_node("character_setup_subgraph", character_setup_subgraph_compiled)
    builder.add_node("chapter_advance_decision", chapter_advance_decision)

    builder.set_entry_point("load_chapter")

    # 规划阶段边
    builder.add_conditional_edges(
        "load_chapter", _route_load_chapter, {"adapt_script": "adapt_script", END: END}
    )
    builder.add_edge("adapt_script", "review_script")
    builder.add_conditional_edges(
        "review_script",
        _route_review_script,
        {"adapt_script": "adapt_script", "detect_new_characters_llm": "detect_new_characters_llm"},
    )
    # 新角色检测后先检测本组新地点（收敛写 scenes_profile），再按有无新角色路由
    builder.add_edge("detect_new_characters_llm", "detect_new_scenes_llm")
    builder.add_conditional_edges(
        "detect_new_scenes_llm",
        _route_after_detect,
        {
            "character_setup_subgraph": "character_setup_subgraph",
            "generate_storyboard": "generate_storyboard",
        },
    )
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
        {"load_chapter": "load_chapter", END: END},
    )

    return builder.compile(checkpointer=checkpointer)
