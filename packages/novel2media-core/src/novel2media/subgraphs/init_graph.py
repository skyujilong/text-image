from __future__ import annotations

from langgraph.graph import END, StateGraph
from novel2media.nodes.init_nodes import (
    load_config,
    parse_characters_llm,
    review_initial_characters,
)
from novel2media.state import InitSubgraphState
from novel2media.subgraphs.setup import character_setup_subgraph_compiled


def _route_after_parse(state: InitSubgraphState) -> str:
    """parse_characters_llm 后路由：有候选角色→人工审阅；无→直接 END（跳过审阅）。"""
    return "review_initial_characters" if state.get("pending_new_characters") else END


def _route_initial_characters_review(state: InitSubgraphState) -> str:
    """review_initial_characters 路由：revise→重解析；pass+有角色→角色设定；pass+无→END。"""
    decision = state.get("_init_characters_review", "")
    if decision == "revise":
        return "parse_characters_llm"
    if decision == "pass" and state.get("setup_queue"):
        return "character_setup_subgraph"
    return END


def build_init_subgraph(checkpointer=None):
    """init 子图：加载配置 → LLM 解析初始角色 → 人工审阅 → 角色设定（三视图+音色）。

    load_config → parse_characters_llm → [有角色: review_initial_characters | 无: END]
    review_initial_characters → [revise: parse_characters_llm | pass+有角色: character_setup_subgraph | pass+无: END]
    character_setup_subgraph → END

    角色设定复用 setup 模块级单例 character_setup_subgraph_compiled（R4/R10：init/chapter/graph
    三处共用同一编译对象，避免 checkpoint namespace 不一致）。
    checkpointer 可选：主图作子图节点时由父图统一注入；独立测试可传 MemorySaver
    以支持 interrupt/resume 跨 invoke。
    """
    builder = StateGraph(InitSubgraphState)
    builder.add_node("load_config", load_config)
    builder.add_node("parse_characters_llm", parse_characters_llm)
    builder.add_node("review_initial_characters", review_initial_characters)
    builder.add_node("character_setup_subgraph", character_setup_subgraph_compiled)

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
