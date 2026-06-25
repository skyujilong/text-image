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


def build_main_graph(checkpointer=None):
    """主图：init 阶段拍平（把 init_subgraph 节点直接展开进主图）。

    去掉 chapter_loop_subgraph 节点——章节处理由 graph_runner 应用层编排，
    驱动独立的 plan_graph / render_graph 顶层图。
    """
    builder = StateGraph(MainGraphState)

    builder.add_node("load_config", load_config)
    builder.add_node("parse_characters_llm", parse_characters_llm)
    builder.add_node("review_initial_characters", review_initial_characters)
    builder.add_node("character_setup_subgraph", _setup_compiled)

    builder.set_entry_point("load_config")
    builder.add_edge("load_config", "parse_characters_llm")
    builder.add_conditional_edges(
        "parse_characters_llm",
        _route_after_parse,
        {"review_initial_characters": "review_initial_characters", END: END},
    )
    builder.add_conditional_edges(
        "review_initial_characters",
        _route_initial_characters_review,
        {
            "parse_characters_llm": "parse_characters_llm",
            "character_setup_subgraph": "character_setup_subgraph",
            END: END,
        },
    )
    builder.add_edge("character_setup_subgraph", END)

    return builder.compile(checkpointer=checkpointer)


# 向后兼容：保留模块级 graph 对象（现有测试/langgraph dev 仍可引用）
# 新代码应使用 build_main_graph() 获取带 checkpointer 的实例
graph = build_main_graph()

SUBGRAPH_REGISTRY = {
    # init 子图已拍平到主图（build_main_graph 直接包含 load_config→parse→review→setup），
    # 不再作为独立子图节点存在。此处仅保留真正以子图节点形式嵌入主图的 setup 子图。
    "character_setup_subgraph": _setup_compiled,
}
