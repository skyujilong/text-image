from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/graph")


def _serialize_graph(g, subgraph_id_set: set[str]) -> dict:
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


def _build_schemas() -> tuple[dict, dict[str, dict]]:
    from novel2media import graph as _graph_module

    subgraph_id_set = set(_graph_module.SUBGRAPH_REGISTRY.keys())
    top = _serialize_graph(_graph_module.graph.get_graph(), subgraph_id_set)
    # 子图序列化同样传入 subgraph_id_set：嵌套子图节点（如 init_subgraph 内的
    # character_setup_subgraph）才能被标 type=subgraph，前端方可下钻。
    # 传空集会让嵌套子图降级成 internal，前端无法展开其内部节点。
    subs = {
        k: _serialize_graph(v.get_graph(), subgraph_id_set)
        for k, v in _graph_module.SUBGRAPH_REGISTRY.items()
    }
    return top, subs


# 模块级延迟初始化（避免导入时触发图编译副作用）
_top_schema: dict | None = None
_subgraph_schemas: dict[str, dict] | None = None


def _ensure_schemas() -> None:
    global _top_schema, _subgraph_schemas
    if _top_schema is None:
        _top_schema, _subgraph_schemas = _build_schemas()


@router.get("/schema")
def get_top_schema():
    _ensure_schemas()
    assert _top_schema is not None
    return _top_schema


@router.get("/schema/{subgraph_id}")
def get_subgraph_schema(subgraph_id: str):
    _ensure_schemas()
    assert _subgraph_schemas is not None
    if subgraph_id not in _subgraph_schemas:
        raise HTTPException(status_code=404, detail="subgraph not found")
    return _subgraph_schemas[subgraph_id]
