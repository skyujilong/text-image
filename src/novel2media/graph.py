from __future__ import annotations
from langgraph.graph import StateGraph, END
from novel2media.state import GraphState
from novel2media.subgraphs.init_graph import build_init_subgraph
from novel2media.subgraphs.chapter import build_chapter_subgraph
from novel2media.logger import setup_logging

setup_logging()

_builder = StateGraph(GraphState)
_builder.add_node("init_subgraph", build_init_subgraph())
_builder.add_node("chapter_loop_subgraph", build_chapter_subgraph())
_builder.set_entry_point("init_subgraph")
_builder.add_edge("init_subgraph", "chapter_loop_subgraph")
_builder.add_edge("chapter_loop_subgraph", END)

# langgraph dev 环境由平台自动托管 checkpointer
# 本地直接调用时可传入 SqliteSaver，此处不硬编码以便 dev 模式兼容
graph = _builder.compile()
