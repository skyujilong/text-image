from langgraph.graph import StateGraph, END
from novel2media.state import GraphState

_builder = StateGraph(GraphState)
_builder.add_node("placeholder", lambda state: state)
_builder.set_entry_point("placeholder")
_builder.add_edge("placeholder", END)

graph = _builder.compile()
