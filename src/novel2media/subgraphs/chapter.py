from __future__ import annotations
from langgraph.graph import StateGraph, END
from novel2media.state import GraphState
from novel2media.nodes.chapter_nodes import (
    load_chapter,
    review_script_llm,
    review_storyboard_llm,
    build_timeline,
    export_to_jianying,
)
from novel2media.subgraphs.setup import build_character_setup_subgraph


def _route_load_chapter(state: GraphState) -> str:
    return END if not state.get("current_chapter_id") else "adapt_script"


def _route_review_script_llm(state: GraphState) -> str:
    result = state.get("_script_review_result", "pass")
    if result == "pass":
        return "review_script_human"
    attempts = state.get("script_review_attempts", 0)
    return "adapt_script" if attempts < 3 else "review_script_llm_interrupt"


def _route_review_script_human(state: GraphState) -> str:
    decision = state.get("_human_script_decision", "pass")
    return "detect_new_characters" if decision == "pass" else "adapt_script"


def _route_detect_new_characters(state: GraphState) -> str:
    queue = state.get("setup_queue", [])
    return "character_setup_subgraph" if queue else "generate_storyboard"


def _route_review_storyboard_llm(state: GraphState) -> str:
    result = state.get("_storyboard_review_result", "pass")
    if result == "pass":
        return "review_storyboard_human"
    attempts = state.get("storyboard_review_attempts", 0)
    return "generate_storyboard" if attempts < 3 else "review_storyboard_llm_interrupt"


def _route_review_storyboard_human(state: GraphState) -> str:
    decision = state.get("_human_storyboard_decision", "pass")
    return "synthesize_audio" if decision == "pass" else "generate_storyboard"


def _route_human_export(state: GraphState) -> str:
    return "export_to_jianying" if state.get("_export_now") else "load_chapter"


def _placeholder_node(name: str):
    def node(state: GraphState) -> dict:
        from novel2media.logger import get_logger
        get_logger(name).info(f"{name}: interrupt 占位节点")
        return {}
    node.__name__ = name
    return node


def build_chapter_subgraph():
    builder = StateGraph(GraphState)

    builder.add_node("load_chapter", load_chapter)
    builder.add_node("adapt_script", _placeholder_node("adapt_script"))
    builder.add_node("review_script_llm", review_script_llm)
    builder.add_node("review_script_human", _placeholder_node("review_script_human"))
    builder.add_node("review_script_llm_interrupt", _placeholder_node("review_script_llm_interrupt"))
    builder.add_node("detect_new_characters", _placeholder_node("detect_new_characters"))
    builder.add_node("character_setup_subgraph", build_character_setup_subgraph())
    builder.add_node("generate_storyboard", _placeholder_node("generate_storyboard"))
    builder.add_node("review_storyboard_llm", review_storyboard_llm)
    builder.add_node("review_storyboard_human", _placeholder_node("review_storyboard_human"))
    builder.add_node("review_storyboard_llm_interrupt", _placeholder_node("review_storyboard_llm_interrupt"))
    builder.add_node("synthesize_audio", _placeholder_node("synthesize_audio"))
    builder.add_node("generate_images", _placeholder_node("generate_images"))
    builder.add_node("build_timeline", build_timeline)
    builder.add_node("human_export_decision", _placeholder_node("human_export_decision"))
    builder.add_node("export_to_jianying", export_to_jianying)

    builder.set_entry_point("load_chapter")
    builder.add_conditional_edges("load_chapter", _route_load_chapter,
                                  {"adapt_script": "adapt_script", END: END})
    builder.add_edge("adapt_script", "review_script_llm")
    builder.add_conditional_edges("review_script_llm", _route_review_script_llm,
                                  {"review_script_human": "review_script_human",
                                   "adapt_script": "adapt_script",
                                   "review_script_llm_interrupt": "review_script_llm_interrupt"})
    builder.add_conditional_edges("review_script_human", _route_review_script_human,
                                  {"detect_new_characters": "detect_new_characters",
                                   "adapt_script": "adapt_script"})
    builder.add_edge("review_script_llm_interrupt", "adapt_script")
    builder.add_conditional_edges("detect_new_characters", _route_detect_new_characters,
                                  {"character_setup_subgraph": "character_setup_subgraph",
                                   "generate_storyboard": "generate_storyboard"})
    builder.add_edge("character_setup_subgraph", "generate_storyboard")
    builder.add_edge("generate_storyboard", "review_storyboard_llm")
    builder.add_conditional_edges("review_storyboard_llm", _route_review_storyboard_llm,
                                  {"review_storyboard_human": "review_storyboard_human",
                                   "generate_storyboard": "generate_storyboard",
                                   "review_storyboard_llm_interrupt": "review_storyboard_llm_interrupt"})
    builder.add_conditional_edges("review_storyboard_human", _route_review_storyboard_human,
                                  {"synthesize_audio": "synthesize_audio",
                                   "generate_storyboard": "generate_storyboard"})
    builder.add_edge("review_storyboard_llm_interrupt", "generate_storyboard")
    builder.add_edge("synthesize_audio", "generate_images")
    builder.add_edge("generate_images", "build_timeline")
    builder.add_edge("build_timeline", "human_export_decision")
    builder.add_conditional_edges("human_export_decision", _route_human_export,
                                  {"export_to_jianying": "export_to_jianying",
                                   "load_chapter": "load_chapter"})
    builder.add_edge("export_to_jianying", "load_chapter")

    return builder.compile()
