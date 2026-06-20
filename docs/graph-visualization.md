# Graph 可视化规范（LangGraph 导出 → React Flow 渲染）

本文件是 CLAUDE.md 中「Graph 可视化规范」一节的详细展开。**任何修改 Graph 可视化（节点/边渲染、布局、状态联动、后端 schema 导出）前必读**，避免回退到"边重合、无箭头、回边飘出画布"等历史问题。

---

## 1. 整体架构与数据流

```
后端 LangGraph 编译图
  → apps/backend/api/v1/endpoints/graph.py 序列化为 GraphSchema JSON
  → 前端 apps/frontend/src/api/client.ts 拉取
  → apps/frontend/src/hooks/useGraphSchema.ts 转 React Flow Node[]/Edge[] + dagre 布局 + handle 分配 + 状态高亮派生
  → apps/frontend/src/components/flow/* 渲染自定义节点 / 画布
```

关键设计：**后端只导出静态拓扑（节点、边、是否条件、是否回边）**，不参与坐标/handle/样式计算；**前端负责布局、handle 分配、状态高亮**。后端 schema 在模块级缓存（`_top_schema` / `_subgraph_schemas`），首次访问 `_ensure_schemas()` 时构建。

---

## 2. 后端导出规则（`apps/backend/api/v1/endpoints/graph.py`）

### 2.1 节点序列化

- 遍历 `g.nodes.items()`，**过滤 `__start__` / `__end__`**。
- `type`：节点 id 在传入的 `subgraph_id_set` 中 → `"subgraph"`，否则 `"internal"`。
  - 顶层图：`subgraph_id_set = set(SUBGRAPH_REGISTRY.keys())`，即顶层里的 init/setup/chapter 等子图节点标 `subgraph`。
  - 子图内部 schema：`subgraph_id_set = set()`，即子图内所有节点都标 `internal`。
- 节点 `label` 直接用 `node_id`。

### 2.2 边序列化

- 遍历 `g.edges`，**过滤 source 或 target 为 `__start__` / `__end__` 的边**。
- `conditional = bool(getattr(edge, "conditional", False))`。
- `label`：仅当 `conditional and edge.data` 时取 `edge.data`（LangGraph 条件边的路由名），否则 `None`。
- `id`：`f"e-{src}-{tgt}-{label}"`（有 label 时带后缀，避免同源同目标多条件边 id 冲突）。
- `is_back_edge`：初始置 `False`，随后由 DFS 标记（见 2.3）。

### 2.3 回边检测（**必须保留**，不可省略）

这是前端"回边走底部、与前向边分离"的**前提**。算法：

1. 找 entry 节点：`__start__` 的后继（首个真实节点）。
2. 构建邻接表（基于已过滤的 raw_edges）。
3. 从 entry 做 DFS，维护 `visited` 和 `in_stack`：
   - 邻居未访问 → 递归。
   - 邻居在 `in_stack` 中 → 当前 `node → nbr` 的所有边标记 `is_back_edge = True`。
4. DFS 结束出栈时 `in_stack.discard(node)`。

典型场景：init 子图 `review_initial_characters → parse_characters_llm`（revise 回边）会被标为回边。

### 2.4 顶层 vs 子图 schema 构建

- 顶层：`_serialize_graph(graph.get_graph(), SUBGRAPH_REGISTRY.keys())`。
- 子图：遍历 `SUBGRAPH_REGISTRY`，对每个编译子图 `_serialize_graph(v.get_graph(), set())`。
- 子图通过 `/graph/schema/{subgraph_id}` 访问，404 当 subgraph_id 不在 registry。

---

## 3. 前后端契约边界

`GraphSchema`（`apps/frontend/src/api/client.ts`）：

```ts
interface GraphSchemaNode { id: string; label: string; type: 'subgraph' | 'internal' }
interface GraphSchemaEdge {
  id: string; source: string; target: string;
  conditional: boolean; label: string | null; is_back_edge: boolean;
}
interface GraphSchema { nodes: GraphSchemaNode[]; edges: GraphSchemaEdge[] }
```

**契约约束**：

- 后端新增 edge 字段时，**必须同步更新前端 `GraphSchemaEdge` 类型**，否则前端 `.map` 取不到。
- `is_back_edge` 是前端区分前向/回边渲染路径的唯一依据，不可改名/不可默认省略。
- `conditional + label` 决定是否显示边标签。
- 前端不依赖后端返回坐标/handle，这些全部前端算。

---

## 4. 前端渲染规则（`apps/frontend/src/hooks/useGraphSchema.ts` + `components/flow/*`）

### 4.1 布局

- 使用 `@dagrejs/dagre`，`rankdir: 'LR'`，`nodesep: 40`，`ranksep: 60`。
- 节点尺寸常量（`useGraphSchema.ts` 顶部）必须与 `components/flow/FlowCanvas.tsx` 中 `nodeSize()` 保持一致（自动定位可见性判断依赖）。

### 4.2 Handle 分配（`assignHandles`）—— 防边重合的核心

- **前向边**：source 用右侧 `source-{i}`，target 用左侧 `target-{i}`；同节点多条前向出/入边按出现顺序分配递增索引，垂直均匀分散（`top: (i+1)/(count+1)*100%`）。
- **回边**：source 用底部 `back-source`，target 用底部 `back-target`；让回边绕底部回环，与前向边物理分离。
- 节点 data 注入 `sourceCount` / `targetCount`（前向出/入边数）和 `hasBackOut` / `hasBackIn`，供 `renderHandles` 决定渲染哪些 handle。
- **Handle id 命名是前后端约定的硬约束**：`source-i` / `target-i` / `back-source` / `back-target`，`useGraphSchema.assignHandles` 与 `components/flow/multiHandles.tsx` 必须完全一致，否则边连不上。

### 4.3 边样式（行业标准 + 本项目约定）

- **类型**：统一 `type: 'smoothstep'`（直角折线），不用默认 bezier——DAG 路径明确，回边不飘。
- **箭头**：所有边 `markerEnd: MarkerType.ArrowClosed`，颜色随边状态。**箭头是必须的**，缺箭头是历史 bug。
- **颜色**：
  - 普通前向边：灰 `#94a3b8`。
  - 活跃前向边（target 节点 running/waiting_human）：蓝 `#2563eb` + 加粗（`strokeWidth: 2.5`）+ `animated: true` 流动。
  - 回边：橙 `#f97316` + 虚线 `strokeDasharray: '6,4'`；活跃时仅加粗变色，**不**叠加 `animated`——流动动画的 dasharray 会覆盖虚线，导致回边变实线。
- **状态高亮派生**：在 `useGraphSchema` 内用 `useMemo` 从 `nodeStatuses` 派生最终边样式，schema 加载（`subgraphId` 变化）与状态高亮（`nodeStatuses` 变化）解耦，状态变化不重新请求 schema。

### 4.4 节点状态来源

- SSE `node_status` 事件（`hooks/useRunStream.ts`）更新 `runStore.nodeStatuses[status_key]`。
- `status_key` = `[...drillPath, nodeId].join('/')`，下钻后子图内节点 key 带父路径前缀。
- 后端会对祖先子图节点传播同状态（`propagate=True`），因此**定位活跃节点时需 internal 优先于 subgraph**（见 4.5）。

### 4.5 自动定位（`components/flow/FlowCanvas.tsx` 的 `useAutoCenter`）

- `FlowCanvas` 必须被 `ReactFlowProvider` 包裹，内部组件才能用 `useReactFlow`。
- `pickActiveNode`：当前可见层级中选活跃节点，优先级 `waiting_human > running`，同状态 `internal > subgraph`（避开祖先传播的虚假活跃）。
- 仅当活跃节点**不在当前视口可见区**时 `setCenter` 平滑居中（400ms，保持当前缩放）；在视口内则不动，不打断用户手动平移/缩放。

---

## 5. 修改 checklist

改动以下区域时需对照本规范回归：

- `apps/backend/api/v1/endpoints/graph.py` — 导出算法变更，必须检查回边检测、字段契约。
- `apps/frontend/src/hooks/useGraphSchema.ts` — handle 命名、边样式、状态高亮派生。
- `apps/frontend/src/components/flow/multiHandles.tsx` — handle 渲染，id 必须与 `assignHandles` 一致。
- `apps/frontend/src/components/flow/{InternalNode,SubgraphNode}.tsx` — data 类型与 `renderHandles` 调用。
- `apps/frontend/src/components/flow/FlowCanvas.tsx` — ReactFlowProvider 包裹、自动定位。
- `apps/frontend/src/api/client.ts` `GraphSchemaEdge` 类型 — 后端新增字段须同步。

**回归要点**：下钻 init_subgraph 后，`parse_characters_llm ↔ review_initial_characters` 的回边应为橙色虚线 U 形（底部出发绕回底部）带箭头；前向边为灰色直角折线带箭头；运行时当前流转边变蓝流动；运行节点跑出视口时自动平滑居中。
