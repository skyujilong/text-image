import pytest
from novel2media.subgraphs.setup import build_character_setup_subgraph
from novel2media.subgraphs.chapter import build_chapter_subgraph
from novel2media.subgraphs.init_graph import build_init_subgraph
from novel2media.graph import graph


def test_setup_subgraph_compiles():
    """验证子图可正常编译，节点和边无遗漏。"""
    g = build_character_setup_subgraph()
    assert g is not None


def test_chapter_subgraph_compiles():
    g = build_chapter_subgraph()
    assert g is not None


def test_init_subgraph_compiles():
    g = build_init_subgraph()
    assert g is not None


def test_top_level_graph_compiles():
    assert graph is not None
