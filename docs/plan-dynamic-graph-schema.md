# 计划：图结构动态化（从后端实时导出，替换前端硬编码）

## Context

前端 `FlowCanvas.tsx` 目前硬编码了节点和边，与 Python 侧实际的 LangGraph 图结构存在严重偏差：
- `init_subgraph` 显示的是 setup 子图内容（实际只有 `load_config` + `character_setup_subgraph`）
- `chapter_loop_subgraph` 缺少 `review_script_llm_interrupt`、`review_storyboard_*`、`export_to_jianying` 等节点
- 条件边被简化成线性流，丢失了分支信息
- 后端用裸节点名推 SSE 事件，子图内部节点状态推不全，`character_setup_subgraph` 在两处上下文着色冲突

**有保留价值的交互**（全部保留）：
- `SubgraphNode` 双击下钻（`pushDrill/popDrill`）
- 节点状态颜色（`pending/running/waiting_human/done/error`）
- SSE 实时状态更新驱动节点着色

---

## 实现方案

### 1. 修改 `src/novel2media/graph.py`

在模块级暴露所有三层子图引用。`character_setup_subgraph` 同时出现在 init 和 chapter 里，需要独立注册到 registry，但**无需修改 `build_init_subgraph` / `build_chapter_subgraph` 的签名**——registry 单独 build 一次即可，序列化结构完全一致（running 时复用同一实例是纯运行时优化，不影响 schema 正确性）。

```python
from novel2media.subgraphs.setup import build_character_setup_subgraph

_init_compiled = build_init_subgraph()
_chapter_compiled = build_chapter_subgraph()
_setup_compiled = build_character_setup_subgraph()   # 仅供 registry / schema 查询

_builder = StateGraph(GraphState)
_builder.add_node("init_subgraph", _init_compiled)
_builder.add_node("chapter_loop_subgraph", _chapter_compiled)
_builder.set_entry_point("init_subgraph")
_builder.add_edge("init_subgraph", "chapter_loop_subgraph")
_builder.add_edge("chapter_loop_subgraph", END)
graph = _builder.compile()

# 所有可下钻的子图（三层），前端通过 id 查内部结构
SUBGRAPH_REGISTRY = {
    "init_subgraph": _init_compiled,
    "chapter_loop_subgraph": _chapter_compiled,
    "character_setup_subgraph": _setup_compiled,
}
```

---

### 2. 修改 `api/graph_runner.py`：子图流式 + 层级 key + 交互形态

#### 2.1 背景与两条原则

当前 `astream` 没有 `subgraphs=True`，导致子图内部节点状态事件不发出；且中断节点名取自 `interrupt_val.get("node")`——但实际 `interrupt({...})` 的 value **从不带 `node` 字段**（见 `setup_nodes.py` 的 `portrait_selector` 等），所以中断节点名恒为 `"unknown"`，waiting_human 着色和面板分发都失效。

两条必须遵守的原则：

- **原则 A（中断节点名来源）**：不依赖 payload 里的 `node`，改用 LangGraph 原生的 `aget_state(config, subgraphs=True)`，从被暂停 task 的 `.name` 取叶子节点名（`Interrupt` 对象的内部命名空间属性跨版本不稳定，**不要用**）。
- **原则 B（双字段：着色 key 与分发标识分离）**：事件**同时**带 `status_key`（层级路径，给节点着色，解决同名 `character_setup_subgraph` 在 init/chapter 两处的冲突）和 `node`（裸叶子名，给 `InteractionDispatcher` 分发审核 UI）。二者职责不同，**不能合并**——若把路径塞进 `node`，`InteractionDispatcher` 的 `node === 'portrait_selector'` 永远不成立，审核面板再也不弹（关键回归）。

#### 2.2 后端改法

```python
def _ns_to_path(ns: tuple[str, ...], node_name: str) -> str:
    """将 LangGraph 命名空间元组归一为稳定路径，去掉 :task_id 后缀"""
    parts = [p.split(":", 1)[0] for p in ns]
    parts.append(node_name)
    return "/".join(parts)


def _ancestor_keys(path: str) -> list[str]:
    """a/b/c -> [a, a/b, a/b/c]，用于把状态沿路径向祖先传播"""
    parts = path.split("/")
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


async def _emit(run_id: str, status_key: str, status: str, *,
                node: str | None = None, payload=None, propagate: bool = False):
    """推送节点状态。propagate=True 时祖先一并点亮（仅 waiting_human/error 用，
    done/running 不传播——某后代完成 ≠ 父子图完成，否则父节点会提前变色）。"""
    keys = _ancestor_keys(status_key) if propagate else [status_key]
    for key in keys:
        event = {"type": "node_status", "status_key": key, "status": status}
        if key == status_key:                 # 仅叶子带分发标识与 payload
            event["node"] = node or key.split("/")[-1]
            if payload is not None:
                event["payload"] = payload
        await push_event(run_id, event)


async def _run_graph(params: dict, config: dict, run_id: str) -> None:
    await _runs_db.update_status(run_id, "running")
    try:
        async for ns, event in _compiled_graph.astream(
            params, config=config, stream_mode="updates", subgraphs=True
        ):
            for node_name, update in event.items():
                if node_name == "__interrupt__":
                    interrupt_val = update[0].value if update else {}
                    # 原则 A：从状态快照取被暂停节点的真实叶子名 + 完整路径
                    snap = await _compiled_graph.aget_state(config, subgraphs=True)
                    leaf_name, leaf_path = _resolve_interrupted_node(snap)  # 见 2.3
                    await _runs_db.update_status(run_id, "waiting_human")
                    await _emit(run_id, leaf_path, "waiting_human",
                                node=leaf_name, payload=interrupt_val, propagate=True)
                else:
                    await _emit(run_id, _ns_to_path(ns, node_name), "done")  # 不传播祖先
        await _runs_db.update_status(run_id, "done")
        await push_event(run_id, {"type": "run_complete"})
    except Exception as exc:
        await _runs_db.update_status(run_id, "error")
        await push_event(run_id, {"type": "run_error", "message": str(exc)})
    finally:
        _sse_queues.pop(run_id, None)
```

#### 2.3 `_resolve_interrupted_node`（原则 A 的落地）

`aget_state(config, subgraphs=True)` 返回的 `StateSnapshot` 中：

- `snap.next` 是即将执行（被暂停）的节点名；
- `snap.tasks` 是 `PregelTask` 列表，每个有 `.name`、`.interrupts`、`.state`（子图 task 的 `.state` 又是嵌套 `StateSnapshot`）。

递归找到 `interrupts` 非空的最深 task，沿途累积 `.name` 即得叶子名与完整路径：

```python
def _resolve_interrupted_node(snap) -> tuple[str, str]:
    parts: list[str] = []
    cur = snap
    while cur is not None:
        task = next((t for t in cur.tasks if getattr(t, "interrupts", None)), None)
        if task is None:
            break
        parts.append(task.name)
        cur = getattr(task, "state", None)  # 子图快照；非快照则停止
        if not hasattr(cur, "tasks"):
            break
    return (parts[-1], "/".join(parts)) if parts else ("unknown", "unknown")
```

> 路径与 `_ns_to_path` 的口径一致（祖先节点名 + 叶子名），与前端 `statusKey` 对齐。具体属性名按锁定的 langgraph 版本验证（见第 3 节风险）。

#### 2.4 resume 也走流式

现有 `resume_run` 用 `ainvoke`（不流式），恢复后到下一次中断/结束之间所有节点**不着色**。改为复用 `_run_graph` 的流式逻辑，输入换成 `Command(resume=resume_value)`：

```python
async def resume_run(run_id: str, resume_value):
    config = {"configurable": {"thread_id": run_id}}
    get_or_create_sse_queue(run_id)
    asyncio.create_task(_run_graph(Command(resume=resume_value), config, run_id))
```

（`_run_graph` 第一参数从 `params: dict` 泛化为"input（dict 或 Command）"，`astream(input, ...)` 直接透传。）

#### 2.5 前端消费（`useRunStream.ts` 小改）

着色用 `status_key`，面板分发仍用裸 `node`：

```ts
if (type === 'node_status') {
  const statusKey = event.status_key as string
  const status = event.status as string
  if (status === 'waiting_human') {
    setNodeStatus(statusKey, 'waiting_human')
    // 裸名分发 + 携带 statusKey 供面板显示阶段路径
    setActiveInteraction({ node: event.node as string, statusKey, payload: event.payload })
  } else {
    setNodeStatus(statusKey, status as 'running' | 'done' | 'error')
  }
}
```

`InteractionDispatcher.tsx` 分发逻辑不改（仍按裸 `node` 匹配），仅面板顶部新增一行阶段路径展示（读 `activeInteraction.statusKey`）。`runStore.ts` 需微改：`ActiveInteraction` 接口增加 `statusKey: string` 字段（`nodeStatuses` 的 key 仍是普通字符串，store 逻辑无感）。

#### 2.6 层级路径 key 对照表

| 视图 / 节点 | 前端 statusKey | 后端 status_key |
|---|---|---|
| 顶层 `chapter_loop_subgraph` | `chapter_loop_subgraph` | 同 |
| chapter 内 `character_setup_subgraph` | `chapter_loop_subgraph/character_setup_subgraph` | 同 |
| init 内 `character_setup_subgraph` | `init_subgraph/character_setup_subgraph` | 同 |
| setup 内 `portrait_selector`（从 chapter 进，中断） | `chapter_loop_subgraph/character_setup_subgraph/portrait_selector` | 同（叶子事件另带 `node: "portrait_selector"`） |

#### 2.7 交互形态决策（固化现有架构，勿改方向）

**结论：流程图只做只读状态可视化；审核输入放独立面板（`InteractionDispatcher`），由 `waiting_human` 事件自动弹出，不靠点击节点触发。** 这是现状架构（`RunPage` 顶层挂 `InteractionDispatcher` + `activeInteraction` 驱动），也是最稳的形态，原因：

- **多层下钻下"点节点出输入"会卡死**：等待节点可能在第 3 层，用户停在顶层既看不到也点不到 → 自动面板把"在哪一层"与"能否操作"解耦。
- 节点交互面要兼顾状态色 + 双击下钻，再叠加点击出输入会冲突；各类审核 UI（选图/抽卡/调参/新角色决策）形态各异，独立面板更合适。

让用户清楚"停在哪个阶段"的三层提示：

- **Run 级**：`Sidebar` 已有 `waiting_human` 橙色 badge（无需改）。
- **面板级**：`InteractionDispatcher` 弹出的面板顶部加一行人类可读阶段路径，由 `status_key` 翻译（如「章节处理 › 角色设定 › 选择头像」）。
- **画布级**：靠 2.2 的祖先传播——无论停在哪层都有橙色节点；可选增强：收到 `waiting_human` 时自动 `pushDrill` 到该节点所在层并高亮。

#### 2.8 已知限制

- **无 "running" 高亮**：`stream_mode="updates"` 仅在节点完成后产事件，没有"开始执行"事件，节点只会 pending→done，不显示蓝色 running 脉冲。若需要 running，须改用 `astream_events(version="v2")`，从 `metadata["langgraph_node"]` / `metadata["langgraph_checkpoint_ns"]` 取节点与层级（事件量更大）。当前方案接受"只着 done/waiting_human/error"。
- **resume 后祖先橙色短暂滑留**：`waiting_human` 会把祖先点橙；resume 后叶子节点 done 只清自己，祖先要等其所属子图整体完成（在父级 ns 产出 done）才转绿，期间短暂保持橙色，可接受。

---

### 3. 新建 `api/routers/graph.py`

提供两个只读接口：

```
GET /api/graph/schema           → 顶层图（init + chapter 两个子图节点 + 一条边）
GET /api/graph/schema/{id}      → 指定子图内部结构
```

**数据来源**：`compiled_graph.get_graph()` 返回 `DrawableGraph`，包含 `.nodes`（dict）和 `.edges`（list）。

**序列化辅助函数 `_serialize_graph(g, subgraph_id_set)`**：

1. **节点**：遍历 `g.nodes`，过滤 id 为 `__start__` / `__end__` 的节点；其余节点若 id 在 `SUBGRAPH_REGISTRY` 中则 type = `"subgraph"`，否则 type = `"internal"`。
2. **边**：遍历 `g.edges`，过滤 `source` 或 `target` 为 `__start__` / `__end__` 的边（悬空边会导致 React Flow 报错）；保留 `source`、`target`、`conditional`（bool）、`label`（分支路由 key，非条件边为 null）。
3. **多分支保留**：条件边若同一 source 有多个 target，各自保留，不合并，以保留分支语义（label 即路由 key）。
4. **边 id 唯一性**：`id = f"e-{source}-{target}-{label}"` （label 为 null 时省略 `-{label}`），保证同一 source-target 若有多条边（不同 label）id 不冲突。
5. **回边标记**：从 entry 节点做 **DFS**，把指向"当前 DFS 递归栈中节点"的边标为 `is_back_edge: true`（图论标准 back-edge 定义，在有环图上有效；拓扑排序序号法在有环图上不可用）。

**响应格式**：

```json
{
  "nodes": [
    {"id": "load_chapter", "label": "load_chapter", "type": "internal"},
    {"id": "character_setup_subgraph", "label": "character_setup_subgraph", "type": "subgraph"}
  ],
  "edges": [
    {"id": "e-load_chapter-adapt_script-adapt_script",
     "source": "load_chapter", "target": "adapt_script",
     "conditional": true, "label": "adapt_script", "is_back_edge": false},
    {"id": "e-export_to_jianying-load_chapter",
     "source": "export_to_jianying", "target": "load_chapter",
     "conditional": false, "label": null, "is_back_edge": true}
  ]
}
```

**缓存**：图结构编译期固定，在路由模块加载时预计算一次存入模块级变量，请求直接返回缓存。

---

### 4. 修改 `api/main.py`

```python
from api.routers import runs, interact, novels, files, graph as graph_router
app.include_router(graph_router.router)
```

---

### 5. 安装前端布局库

```bash
cd web && pnpm add @dagrejs/dagre
```

> 只装 `@dagrejs/dagre`，自带 TypeScript 类型，**不需要** `@types/dagre`（混装产生类型冲突）。

---

### 6. 修改 `web/src/api/client.ts`

新增类型和方法：

```ts
export interface GraphSchemaNode {
  id: string
  label: string
  type: 'subgraph' | 'internal'
}

export interface GraphSchemaEdge {
  id: string
  source: string
  target: string
  conditional: boolean
  label: string | null
  is_back_edge: boolean
}

export interface GraphSchema {
  nodes: GraphSchemaNode[]
  edges: GraphSchemaEdge[]
}

// api 对象新增：
getGraphSchema: (subgraphId?: string) =>
  request<GraphSchema>(subgraphId ? `/graph/schema/${subgraphId}` : '/graph/schema'),
```

---

### 7. 新建 `web/src/hooks/useGraphSchema.ts`

```ts
function useGraphSchema(subgraphId: string | null, drillPath: string[]): {
  nodes: Node[]
  edges: Edge[]
  isLoading: boolean
}
```

内部步骤：

1. `useEffect` 监听 `subgraphId` 变化，调用 `api.getGraphSchema(subgraphId ?? undefined)`
2. 用 `@dagrejs/dagre` 计算坐标（`rankdir: 'LR'`，subgraph 节点 180×60，internal 节点 160×48）
3. 构造 React Flow `Node` 时**按 type 补全 data 字段**，并加入 `statusKey`（层级路径，与后端 key 对齐）：
   - `type === 'subgraph'` → `data: { label, subgraphId: id, statusKey: [...drillPath, id].join('/') }`
   - `type === 'internal'` → `data: { label, nodeId: id, statusKey: [...drillPath, id].join('/') }`
4. 构造 React Flow `Edge` 时按 `is_back_edge` 设置样式：
   - `is_back_edge: true` → `style: { stroke: '#f97316', strokeDasharray: '5,4' }`（橙色虚线）
   - `conditional: true` → `label` 字段填入路由 key（前端边上显示分支名）
   - 普通边 → 默认样式

---

### 8. 修改 `web/src/components/flow/SubgraphNode.tsx` 和 `InternalNode.tsx`

两个组件改为从 `data.statusKey` 取状态（而不是 `data.subgraphId` / `data.nodeId`），各改一行：

```tsx
// SubgraphNode.tsx
const status = (nodeStatuses[data.statusKey] ?? 'pending') as NodeStatus

// InternalNode.tsx
const status = (nodeStatuses[data.statusKey] ?? 'pending') as NodeStatus
```

对应 data 接口类型也需补上 `statusKey: string` 字段。

---

### 9. 重写 `web/src/components/flow/FlowCanvas.tsx`

- 删除所有硬编码常量（`TOP_NODES`、`TOP_EDGES`、`CHAPTER_INTERNAL_NODES` 等及 `DRILL_MAP`）
- `currentSubgraph` = `drillPath` 最后一项（或 null 顶层）
- 用 `useGraphSchema(currentSubgraph ?? null, drillPath)` 替换
- `isLoading` 时在画布区域显示居中 Loading 文字
- 下钻/返回逻辑不变（`pushDrill` / `popDrill`）

---

## 关键文件

| 文件 | 操作 |
|------|------|
| `src/novel2media/graph.py` | 修改：暴露 `SUBGRAPH_REGISTRY`（含三层子图） |
| `api/graph_runner.py` | 修改：`subgraphs=True` + `_ns_to_path` 层级 key + `aget_state` 取中断节点（原则 A）+ 祖先传播 + resume 流式化 |
| `api/routers/graph.py` | 新建：两个 GET 接口 + DFS 回边标记 + 模块级缓存 |
| `api/main.py` | 修改：注册 graph router |
| `web/src/api/client.ts` | 修改：新增 3 个类型 + `getGraphSchema` 方法 |
| `web/src/hooks/useGraphSchema.ts` | 新建：dagre 布局 + statusKey 路径拼接 + 回边样式 |
| `web/src/components/flow/FlowCanvas.tsx` | 重写：删除硬编码，使用 hook |
| `web/src/components/flow/SubgraphNode.tsx` | 小改：`data.statusKey` 替换 `data.subgraphId` |
| `web/src/components/flow/InternalNode.tsx` | 小改：`data.statusKey` 替换 `data.nodeId` |
| `web/src/hooks/useRunStream.ts` | 小改：着色读 `status_key`，`activeInteraction.node` 仍用裸 `node`（原则 B） |
| `api/models.py` | 修改：`SSEEvent` 增加 `status_key` 字段 |

无需修改：`runStore.ts`（key 仍是普通字符串，store 无感）、`InteractionDispatcher.tsx`（仍按裸 `node` 分发）。

---

## 验证步骤

1. 启动后端：`uvicorn api.main:app --reload`
2. `GET /api/graph/schema` → 返回 2 个节点（init + chapter）、1 条边
3. `GET /api/graph/schema/chapter_loop_subgraph` → 15+ 节点，含 `review_script_llm_interrupt`、`export_to_jianying`；条件边带 label；`export_to_jianying→load_chapter` 标注 `is_back_edge: true`
4. `GET /api/graph/schema/init_subgraph` → 2 个节点（`load_config` + `character_setup_subgraph`）
5. `GET /api/graph/schema/character_setup_subgraph` → 11 个节点
6. 启动前端：`cd web && pnpm dev`
7. 画布渲染顶层 → 双击 `chapter_loop_subgraph` 下钻，验证所有真实节点出现，回边显示橙色虚线，条件边显示 label
8. 双击 `init_subgraph` 下钻，验证只有 2 个节点
9. 在 chapter 内部双击 `character_setup_subgraph` 下钻，验证第三层 11 个节点正常渲染
10. 跑一次 run，在顶层和各层内部确认节点颜色随 SSE 事件变化（注意：updates 模式只有 done/waiting_human/error，无 running 高亮，见 2.8）；特别验证 chapter 和 init 两个上下文的 `character_setup_subgraph` 着色互不干扰
11. 触发一次人工审核（如 `portrait_selector` 中断）：确认 `InteractionDispatcher` 自动弹出对应面板（裸 `node` 分发生效）；确认即使停在顶层视图，祖先节点（`chapter_loop_subgraph` 等）也显示 waiting_human 橙色
12. 完成审核 resume 后，确认后续节点继续着色（resume 已走流式，见 2.4）
13. 验证 `ns` / `aget_state` task 结构符合预期：临时打印 `ns` 原始值和 `snap.tasks`，确认命名空间格式为 `name:task_id` 且能解析出叶子节点（锁定 langgraph 版本）
