from __future__ import annotations
from langgraph.graph import StateGraph, END
from novel2media.state import GraphState
from novel2media.nodes.init_nodes import load_config
from novel2media.subgraphs.setup import build_character_setup_subgraph


def build_init_subgraph():
    builder = StateGraph(GraphState)
    builder.add_node("load_config", load_config)
    builder.add_node("character_setup_subgraph", build_character_setup_subgraph())
    builder.set_entry_point("load_config")
    builder.add_edge("load_config", "character_setup_subgraph")
    builder.add_edge("character_setup_subgraph", END)
    return builder.compile()
