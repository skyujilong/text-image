# 计划：State Inspector（节点状态检查面板）

## Context

LangGraph Studio 最核心的调试功能：点击任意节点，右侧弹出面板，显示该节点执行后的 state 内容。当前点击节点无任何反应，无法知道节点跑完后 state 里有什么。

目标：点击 `done`/`error` 节点 → 右侧面板展示该节点执行后的完整 state（JSON 格式）。

---

## 数据来源（已验证）

LangGraph checkpoint 历史中，`metadata.writes` 记录"哪个节点写了这个 checkpoint"。

```
aget_state_history(config) 中每条记录：
  snap.metadata.writes = {"node_name": {state_diff}}  ← 该节点执行后写入的变化
  snap.values = {完整 state 快照}
  snap.next = (下一个要执行的节点,)
```

**找"某节点执行后的 state"** = 遍历历史，找 `metadata.writes` 的 key 包含目标节点名的那条，取其 `snap.values`。

- **顶层节点**（如 `init_subgraph`）：在 `aget_state_history({"configurable": {"thread_id": run_id}})` 里找
- **子图内节点**（如 `init_subgraph/load_config`）：在 `aget_state_history({"configurable": {"thread_id": run_id, "checkpoint_ns": "init_subgraph:UUID"}})` 里找

---

## 实现方案

### 1. 后端：`api/routers/runs.py` — 新增 `GET /runs/{run_id}/state`

```python
@router.get("/runs/{run_id}/state")
async def get_node_state(run_id: str, node_path: str = Query(...)):
    state = await runner.get_node_state(run_id, node_path)
    if state is None:
        raise HTTPException(status_code=404, detail="node state not found")
    return state  # dict: {values: {...}, node: str}
```

### 2. 后端：`api/graph_runner.py` — 新增 `get_node_state`

```python
async def get_node_state(run_id: str, node_path: str) -> dict | None:
    parts = node_path.split("/")
    top_node = parts[0]
    leaf_node = parts[-1] if len(parts) > 1 else top_node

    config = {"configurable": {"thread_id": run_id}}

    if len(parts) == 1:
        # 顶层节点：在顶层历史里找
        async for snap in _compiled_graph.aget_state_history(config):
            writes = snap.metadata.get("writes") if isinstance(snap.metadata, dict) else {}
            if writes and top_node in writes:
                return {"node": top_node, "values": snap.values}
    else:
        # 子图内节点：找子图 namespace 再查历史
        async with aiosqlite.connect(CHECKPOINT_DB) as db:
            async with db.execute(
                "SELECT DISTINCT checkpoint_ns FROM checkpoints "
                "WHERE thread_id=? AND checkpoint_ns LIKE ?",
                (run_id, f"{top_node}:%"),
            ) as cur:
                ns_rows = await cur.fetchall()
        if not ns_rows:
            return None
        sub_ns = ns_rows[-1][0]
        sub_config = {"configurable": {"thread_id": run_id, "checkpoint_ns": sub_ns}}
        async for snap in _compiled_graph.aget_state_history(sub_config):
            writes = snap.metadata.get("writes") if isinstance(snap.metadata, dict) else {}
            if writes and leaf_node in writes:
                return {"node": leaf_node, "values": snap.values}

    return None
```

### 3. 前端：`web/src/api/client.ts` — 新增 `getNodeState`

```typescript
getNodeState: (runId: string, nodePath: string) =>
  request<{ node: string; values: Record<string, unknown> }>(
    `/runs/${runId}/state?node_path=${encodeURIComponent(nodePath)}`
  ),
```

### 4. 前端：新增 `web/src/components/panels/StateInspector.tsx`

右侧滑出面板（使用已有的 `Sheet` 组件 `web/src/components/ui/sheet.tsx`）：

```tsx
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'

interface Props {
  open: boolean
  nodePath: string | null
  runId: string | null
  onClose: () => void
}

export default function StateInspector({ open, nodePath, runId, onClose }: Props) {
  const [data, setData] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open || !nodePath || !runId) return
    setLoading(true)
    api.getNodeState(runId, nodePath)
      .then((r) => setData(r.values))
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [open, nodePath, runId])

  return (
    <Sheet open={open} onOpenChange={(v) => !v && onClose()}>
      <SheetContent side="right" className="w-[420px] overflow-y-auto">
        <SheetHeader>
          <SheetTitle>State: {nodePath}</SheetTitle>
        </SheetHeader>
        {loading && <div className="text-sm text-gray-400 mt-4">加载中...</div>}
        {!loading && data && (
          <pre className="mt-4 text-xs bg-gray-50 rounded p-3 overflow-auto whitespace-pre-wrap break-all">
            {JSON.stringify(data, null, 2)}
          </pre>
        )}
        {!loading && !data && (
          <div className="text-sm text-gray-400 mt-4">暂无数据（节点尚未执行或无 state 写入）</div>
        )}
      </SheetContent>
    </Sheet>
  )
}
```

### 5. 前端：`InternalNode.tsx` 和 `SubgraphNode.tsx` — 点击打开面板

在 store 新增 `inspectingNode: string | null` 和 `setInspectingNode`：

```ts
// runStore.ts 新增
inspectingNode: string | null,
setInspectingNode: (path: string | null) => set({ inspectingNode: path }),
```

节点点击（`onClick` 不是 `onDoubleClick`）：
```tsx
onClick={() => {
  if (status === 'done' || status === 'error') {
    setInspectingNode(data.statusKey)
  }
}}
```

在 `RunPage.tsx` 里挂载 `StateInspector`：
```tsx
<StateInspector
  open={!!inspectingNode}
  nodePath={inspectingNode}
  runId={currentRunId}
  onClose={() => setInspectingNode(null)}
/>
```

---

## 关键文件

| 文件 | 改动 |
|------|------|
| `api/graph_runner.py` | 新增 `get_node_state(run_id, node_path)` |
| `api/routers/runs.py` | 新增 `GET /runs/{run_id}/state?node_path=` |
| `web/src/api/client.ts` | 新增 `getNodeState` |
| `web/src/store/runStore.ts` | 新增 `inspectingNode` / `setInspectingNode` |
| `web/src/components/panels/StateInspector.tsx` | 新建右侧面板组件 |
| `web/src/components/flow/InternalNode.tsx` | done/error 时 onClick 打开 inspector |
| `web/src/components/flow/SubgraphNode.tsx` | 同上 |
| `web/src/pages/RunPage.tsx` | 挂载 `StateInspector` |

---

## 验证

1. 跑一个完整 run，等若干节点 `done`
2. 点击 `done` 节点，右侧面板滑出，显示该节点后的 state JSON
3. 点击 `error` 节点，面板展示出错前最后一次 state
4. 点击 `pending`/`running` 节点，面板不弹出（或弹出"暂无数据"）
5. 点击子图内节点（下钻后），确认能正确读取子图 state
