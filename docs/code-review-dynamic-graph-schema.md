# 代码审查报告：图结构动态化

**审查范围**：本地未提交变更（`git diff HEAD`）  
**审查日期**：2026-06-12  
**涉及文件**：`api/graph_runner.py`、`api/routers/graph.py`（新建）、`api/models.py`、`api/main.py`、`src/novel2media/graph.py`、`web/src/hooks/useGraphSchema.ts`（新建）、`web/src/hooks/useRunStream.ts`、`web/src/components/flow/FlowCanvas.tsx`、`web/src/components/flow/SubgraphNode.tsx`、`web/src/components/flow/InternalNode.tsx`

---

## 总体评价

整体实现方向正确，后端图结构动态导出、层级 statusKey、aget_state 取中断节点名等核心逻辑均符合设计。发现 4 个问题：1 个中优先级竞态（UI 错误渲染）、1 个低优先级竞态（面板短暂不弹）、1 个低优先级状态覆写风险、1 处死代码。

---

## 问题列表（按严重程度排序）

### 1. [中] `useGraphSchema.ts:94` — 快速导航导致过时响应覆盖当前视图

**类型**：竞态 / 正确性  

**问题描述**  
`useEffect` 依赖数组为 `[subgraphId]`，但 Promise 回调没有 stale 标志保护。若用户快速下钻再返回（`subgraphId` 先改为 B 再改回 A），两个 fetch 并发，B 的响应若比 A 慢，会在 A 的视图下写入 B 的节点/边。

**触发场景**  
用户双击进入 `chapter_loop_subgraph`，立即点「← 返回」。顶层 fetch（`/graph/schema`）比 chapter fetch（`/graph/schema/chapter_loop_subgraph`）先返回。chapter 的 16 个节点被写入顶层画布，显示错误内容。

**修复**  
在 `useEffect` 内加 stale 标志：

```ts
useEffect(() => {
  let stale = false
  setIsLoading(true)
  api.getGraphSchema(subgraphId ?? undefined).then((schema) => {
    if (stale) return           // 丢弃过时响应
    // ... setNodes / setEdges / setIsLoading(false)
  }).catch(() => {
    if (!stale) setIsLoading(false)
  })
  return () => { stale = true } // cleanup 时标记 stale
}, [subgraphId])
```

---

### 2. [低] `api/graph_runner.py:89` + `web/src/hooks/useRunStream.ts:29` — 祖先事件缺 `node` 字段，`setActiveInteraction` 被提前调用

**类型**：竞态 / UX 正确性  

**问题描述**  
`_emit` 以 `propagate=True` 发出中断事件时，**祖先事件**（非叶子节点）仅含 `{type, status_key, status}`，不含 `node` 字段。前端 `useRunStream.ts` 在 `waiting_human` 分支对**所有** `waiting_human` 事件（包括祖先）调用 `setActiveInteraction`：

```ts
// 现在（有问题）
if (status === 'waiting_human') {
  setNodeStatus(statusKey, 'waiting_human')
  setActiveInteraction({ node: event.node as string, payload: event.payload })
  // 祖先事件：event.node 为 undefined，payload 也为 undefined
}
```

祖先事件到达顺序早于叶子事件，`setActiveInteraction({node: undefined})` 触发 React 渲染，`InteractionDispatcher` 收到 `node=undefined`，所有 if 分支不匹配，面板不弹出。叶子事件到达后覆盖为正确值。若网络/渲染有延迟，用户会看到面板先不弹再弹的闪烁。

**触发场景**  
`portrait_selector` 触发中断，`_emit` 按顺序发出：  
1. `chapter_loop_subgraph`（无 node）→ `setActiveInteraction({node: undefined})`  
2. `chapter_loop_subgraph/character_setup_subgraph`（无 node）→ 同上  
3. `chapter_loop_subgraph/character_setup_subgraph/portrait_selector`（有 node）→ 面板正确显示  

若步骤 1/2 和步骤 3 之间触发了 React 渲染，面板会短暂不弹。

**修复**  
前端只对**含 `node` 字段**的事件调用 `setActiveInteraction`：

```ts
if (status === 'waiting_human') {
  setNodeStatus(statusKey, 'waiting_human')
  if (event.node !== undefined) {   // 仅叶子事件才触发面板
    setActiveInteraction({ node: event.node as string, payload: event.payload })
  }
}
```

---

### 3. [低] `api/graph_runner.py:135` — `resume_run` 与 `_run_graph` 双重 `update_status("running")`，极端时序下状态被回写

**类型**：竞态 / 状态正确性  

**问题描述**  
`resume_run` 调用 `asyncio.create_task(_run_graph(...))` 后，自身也执行 `await _runs_db.update_status(run_id, "running")`。而 `_run_graph` 内部第一行也会 `await _runs_db.update_status(run_id, "running")`。

通常无害（两次都写 `"running"`），但若 `_run_graph` 在 `resume_run` 的 `await` 执行前已经完成（状态写为 `"done"`），`resume_run` 的 `await` 会把状态回写为 `"running"`，导致 run 永久卡在 `running` 状态。

**触发场景**  
极短的 resume 路径（resume 后立即到达 `END` 或下一个 interrupt）：  
1. `create_task(_run_graph)` — 任务调度但未开始  
2. `_run_graph` 趁当前协程未 `await` 时执行完毕，写 `status="done"`  
3. `resume_run` 的 `await update_status("running")` 执行，覆盖为 `"running"`  

**修复**  
删除 `resume_run` 末尾的重复调用，由 `_run_graph` 唯一负责状态更新：

```python
async def resume_run(run_id: str, resume_value: Any) -> None:
    config = {"configurable": {"thread_id": run_id}}
    get_or_create_sse_queue(run_id)
    asyncio.create_task(_run_graph(Command(resume=resume_value), config, run_id))
    # 删除: await _runs_db.update_status(run_id, "running")
```

---

### 4. [清理] `api/routers/graph.py:27` — 死变量 `label`

**类型**：清理 / 死代码  

**问题描述**  
第 27 行的 `label` 赋值结果从未被读取，下一行 `edge_label` 用不同逻辑重新计算：

```python
# 死代码：label 从未被使用
label = getattr(edge, "data", None) or getattr(edge, "conditional", None)
# 实际使用的变量：
is_conditional = bool(getattr(edge, "conditional", False))
edge_label = edge.data if (is_conditional and edge.data) else None
```

**修复**  
删除第 27 行。

---

## 已验证正确的关键点

| 点 | 结论 |
|---|---|
| `_builder.compile(checkpointer=...)` 二次编译 | 正确，`CompiledStateGraph` 无 `.compile()` 方法，改用 `_builder.compile()` 是修复 |
| `_ns_to_path` 与 `_resolve_interrupted_node` 路径格式一致 | 一致，均为 `a/b/c` 格式，节点着色 key 匹配 |
| DFS 回边标记（`export_to_jianying→load_chapter` 等）| 正确，8 条回边均准确标出 |
| `drillPath` 未纳入 `useEffect` 依赖 | 安全，`drillPath` 与 `subgraphId` 同步变化，不存在 stale 捕获 |
| 顶层/chapter/init/setup 图结构节点数 | 2/16/2/11，全部正确 |
| `_ensure_schemas()` 并发安全性 | 安全，`_build_schemas()` 无 `await`，GIL 保护下不存在竞态 |
