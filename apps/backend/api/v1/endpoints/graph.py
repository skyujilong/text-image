from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/graph")


def _serialize_graph(g, subgraph_id_set: set[str]) -> dict:
    """将 LangGraph 的 DrawableGraph 序列化为前端可用的 schema。

    过滤 __start__/__end__ 伪节点，DFS 检测回边，子图节点标记 type=subgraph。
    """
    # 收集所有节点（过滤 __start__ / __end__）
    nodes = []
    for node_id, node_data in g.nodes.items():
        if node_id in ("__start__", "__end__"):
            continue
        node_type = "subgraph" if node_id in subgraph_id_set else "internal"
        nodes.append(
            {
                "id": node_id,
                "label": node_id,
                "type": node_type,
            }
        )

    # 收集所有边（过滤含 __start__ / __end__ 的边）
    raw_edges = []
    for edge in g.edges:
        src = edge.source
        tgt = edge.target
        if src in ("__start__", "__end__") or tgt in ("__start__", "__end__"):
            continue
        # DrawableGraph edge: .source, .target, .data (label for conditional), .conditional
        is_conditional = bool(getattr(edge, "conditional", False))
        edge_label = edge.data if (is_conditional and edge.data) else None
        edge_id_suffix = f"-{edge_label}" if edge_label else ""
        raw_edges.append(
            {
                "id": f"e-{src}-{tgt}{edge_id_suffix}",
                "source": src,
                "target": tgt,
                "conditional": is_conditional,
                "label": edge_label,
                "is_back_edge": False,  # 稍后 DFS 标记
            }
        )

    # DFS 标记回边：找 entry 节点（__start__ 的后继，即首个节点）
    entry_node = None
    for edge in g.edges:
        if edge.source == "__start__":
            entry_node = edge.target
            break

    if entry_node:
        # 构建邻接表
        adj: dict[str, list[str]] = {}
        for e in raw_edges:
            adj.setdefault(e["source"], []).append(e["target"])

        visited: set[str] = set()
        in_stack: set[str] = set()

        def dfs(node: str) -> None:
            visited.add(node)
            in_stack.add(node)
            for nbr in adj.get(node, []):
                if nbr not in visited:
                    dfs(nbr)
                elif nbr in in_stack:
                    # 标记所有 source→nbr 的边为回边
                    for e in raw_edges:
                        if e["source"] == node and e["target"] == nbr:
                            e["is_back_edge"] = True
            in_stack.discard(node)

        dfs(entry_node)

    return {"nodes": nodes, "edges": raw_edges}


def _build_schemas() -> dict[str, dict]:
    """构建图（main/plan）及注册子图的 schema 拓扑。

    两图直接调用 builder 编译（无 checkpointer），获取拓扑结构。
    子图 schema 保留，供前端下钻使用。
    render_graph 已移除（渲染改为独立工作台），不再包含其 schema。
    """
    from novel2media import graph as _graph_module
    from novel2media.subgraphs.plan_graph import build_plan_graph

    # 两图 builder（无 checkpointer，纯拓扑）
    builders = {
        "main": _graph_module.build_main_graph,
        "plan": build_plan_graph,
    }

    # 子图 ID 集合（用于标记 subgraph 类型节点）
    subgraph_id_set = set(_graph_module.SUBGRAPH_REGISTRY.keys())

    schemas: dict[str, dict] = {}
    for scope, builder in builders.items():
        g = builder()
        schemas[scope] = _serialize_graph(g.get_graph(), subgraph_id_set)

    # 保留子图 schema（setup 子图下钻用）
    for k, v in _graph_module.SUBGRAPH_REGISTRY.items():
        schemas[k] = _serialize_graph(v.get_graph(), subgraph_id_set)

    return schemas


# 模块级延迟初始化（避免导入时触发图编译副作用）
_schemas: dict[str, dict] | None = None


def _ensure_schemas() -> None:
    global _schemas
    if _schemas is None:
        _schemas = _build_schemas()


@router.get("/schema")
def get_schema(scope: str = "main"):
    """获取指定 scope 的图 schema。

    scope 可选值：
    - main: 主图（init + setup）
    - plan: 规划图（逐章规划：剧本→分镜→章节推进）
    - character_setup_subgraph: setup 子图（下钻用）
    """
    _ensure_schemas()
    assert _schemas is not None
    if scope not in _schemas:
        raise HTTPException(status_code=404, detail=f"scope '{scope}' not found")
    return _schemas[scope]
