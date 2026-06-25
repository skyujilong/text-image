from __future__ import annotations

from langgraph.graph import END, StateGraph
from novel2media.nodes.init_nodes import (
    load_config,
    parse_characters_llm,
    review_initial_characters,
)
from novel2media.state import MainGraphState
from novel2media.subgraphs.init_graph import (
    _route_after_parse,
    _route_initial_characters_review,
)
from novel2media.subgraphs.plan_graph import build_plan_graph
from novel2media.subgraphs.render_graph import build_render_graph
from novel2media.subgraphs.setup import character_setup_subgraph_compiled
from novel2media_logging import setup_logging

setup_logging()


# R4/R10：复用 setup 模块级单例，与 init_graph / chapter / plan_graph 内引用同一编译对象
_setup_compiled = character_setup_subgraph_compiled

# 规划/渲染子图也复用模块级单例（避免重复编译 + checkpoint namespace 不一致）
_plan_compiled = build_plan_graph()
_render_compiled = build_render_graph()


def _has_planned_chapters(state: MainGraphState) -> str:
    """规划完成后路由：有 planned 章节→进入渲染；无→检查是否还有章节待规划。

    由 chapter_advance_decision 写入 _chapter_advance = "render" 时表示用户决定进入渲染。
    """
    # 优先尊重用户的显式决策：用户点"进入渲染"则走渲染阶段
    if state.get("_chapter_advance") == "render":
        return "render_graph_subgraph"
    # 还有待规划章节则继续规划
    if state.get("plan_cursor") is not None:
        return "plan_graph_subgraph"
    # 全部完成
    return END


def _has_rendered_all(state: MainGraphState) -> str:
    """渲染完成后路由：还有待规划章节→回去继续规划；全部完成→END。"""
    if state.get("plan_cursor") is not None:
        return "plan_graph_subgraph"
    if state.get("render_cursor") is not None:
        return "render_graph_subgraph"
    return END


def build_main_graph(checkpointer=None):
    """主图：完整工作流总控（init → setup → [规划 ↔ 渲染] 交错循环）。

    采用"总图嵌子图"架构（LangGraph 原生模式）：
    - 状态自动传递：子图节点写入的 state 自然合并到主图
    - Checkpoint 连贯：整条链路在同一个 thread namespace 下
    - 中断天然支持：子图 interrupt 冒泡到主图，resume 无缝继续

    执行链路：
        load_config → parse_characters_llm → review_initial_characters
              ↓
        character_setup_subgraph（三视图配置）
              ↓
        plan_graph_subgraph（单章规划：剧本 → 分镜 → 稿件入 render_batch）
              ↓
        render_graph_subgraph（渲染：生图 → 音频 → 时间轴 → 导出）
              ↓
        [循环：还有章节 → 回到 plan_graph_subgraph | 全部完成 → END]

    注：不再需要应用层 _orchestrate Python 循环驱动，
        LangGraph 条件边天然支持"规划一批 → 渲染一批"的交错执行。
    """
    builder = StateGraph(MainGraphState)

    # ── init/setup 阶段节点 ──
    builder.add_node("load_config", load_config)
    builder.add_node("parse_characters_llm", parse_characters_llm)
    builder.add_node("review_initial_characters", review_initial_characters)
    builder.add_node("character_setup_subgraph", _setup_compiled)

    # ── 章节处理阶段节点（子图嵌入主图作为节点） ──
    builder.add_node("plan_graph_subgraph", _plan_compiled)
    builder.add_node("render_graph_subgraph", _render_compiled)

    builder.set_entry_point("load_config")

    # ── init 阶段边 ──
    builder.add_edge("load_config", "parse_characters_llm")
    builder.add_conditional_edges(
        "parse_characters_llm",
        _route_after_parse,
        {"review_initial_characters": "review_initial_characters", END: END},
    )

    # ── 初始角色审阅边 ──
    builder.add_conditional_edges(
        "review_initial_characters",
        _route_initial_characters_review,
        {
            "parse_characters_llm": "parse_characters_llm",  # revise 重解析
            "character_setup_subgraph": "character_setup_subgraph",  # pass 进入配置
            END: END,  # 无角色直接结束（异常分支）
        },
    )

    # ── setup 完成 → 进入规划阶段 ──
    builder.add_edge("character_setup_subgraph", "plan_graph_subgraph")

    # ── 规划完成 → 条件路由：渲染 or 继续规划 or 结束 ──
    builder.add_conditional_edges(
        "plan_graph_subgraph",
        _has_planned_chapters,
        {
            "render_graph_subgraph": "render_graph_subgraph",  # 用户决定进入渲染
            "plan_graph_subgraph": "plan_graph_subgraph",  # 继续规划下一章
            END: END,  # 全部完成
        },
    )

    # ── 渲染完成 → 条件路由：回去继续规划 or 结束 ──
    builder.add_conditional_edges(
        "render_graph_subgraph",
        _has_rendered_all,
        {
            "plan_graph_subgraph": "plan_graph_subgraph",  # 还有章节待规划
            "render_graph_subgraph": "render_graph_subgraph",  # 还有章节待渲染
            END: END,  # 全部完成
        },
    )

    return builder.compile(checkpointer=checkpointer)


# 向后兼容：保留模块级 graph 对象（现有测试/langgraph dev 仍可引用）
# 新代码应使用 build_main_graph() 获取带 checkpointer 的实例
graph = build_main_graph()

SUBGRAPH_REGISTRY = {
    # init 子图已拍平到主图（build_main_graph 直接包含 load_config→parse→review→setup），
    # 不再作为独立子图节点存在。此处仅保留真正以子图节点形式嵌入主图的子图。
    "character_setup_subgraph": _setup_compiled,
    "plan_graph_subgraph": _plan_compiled,
    "render_graph_subgraph": _render_compiled,
}
