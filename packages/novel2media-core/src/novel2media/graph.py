from __future__ import annotations

from langgraph.graph import END, StateGraph
from novel2media.logger import setup_logging
from novel2media.state import GraphState
from novel2media.subgraphs.chapter import build_chapter_subgraph
from novel2media.subgraphs.init_graph import build_init_subgraph
from novel2media.subgraphs.setup import build_character_setup_subgraph

setup_logging()

_init_compiled = build_init_subgraph()
_chapter_compiled = build_chapter_subgraph()
_setup_compiled = build_character_setup_subgraph()

_builder = StateGraph(GraphState)
_builder.add_node("init_subgraph", _init_compiled)
_builder.add_node("chapter_loop_subgraph", _chapter_compiled)
_builder.set_entry_point("init_subgraph")
_builder.add_edge("init_subgraph", "chapter_loop_subgraph")
_builder.add_edge("chapter_loop_subgraph", END)

# langgraph dev 环境由平台自动托管 checkpointer
# 本地直接调用时可传入 SqliteSaver，此处不硬编码以便 dev 模式兼容
graph = _builder.compile()

SUBGRAPH_REGISTRY = {
    "init_subgraph": _init_compiled,
    "chapter_loop_subgraph": _chapter_compiled,
    "character_setup_subgraph": _setup_compiled,
}
