import pytest
from novel2media.subgraphs.setup import build_character_setup_subgraph


def test_setup_subgraph_compiles():
    """验证子图可正常编译，节点和边无遗漏。"""
    graph = build_character_setup_subgraph()
    assert graph is not None
