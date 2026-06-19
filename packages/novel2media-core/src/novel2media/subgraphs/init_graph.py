from __future__ import annotations

from langgraph.graph import END, StateGraph
from novel2media.nodes.init_nodes import load_config
from novel2media.state import InitSubgraphState
from novel2media.subgraphs.setup import build_character_setup_subgraph


def build_init_subgraph():
    builder = StateGraph(InitSubgraphState)
    builder.add_node("load_config", load_config)
    builder.add_node("character_setup_subgraph", build_character_setup_subgraph())
    builder.set_entry_point("load_config")
    builder.add_edge("load_config", "character_setup_subgraph")
    builder.add_edge("character_setup_subgraph", END)
    return builder.compile()
