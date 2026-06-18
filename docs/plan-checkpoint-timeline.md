# 计划：Checkpoint 时间线

## Context

LangGraph Studio 左侧有 checkpoint 列表，显示每一步的执行时间和节点名，可以快速了解"图跑到哪了"以及各节点耗时。当前 UI 没有任何执行历史展示。

目标：在 Sidebar 下方（或独立 tab）展示 checkpoint 时间线，每条记录显示节点名 + 时间，配合"从此节点重跑"按钮，使历史回溯和重跑形成闭环。

---

## 数据来源（已验证）

`aget_state_history(config)` 每条 `StateSnapshot` 包含：
- `snap.created_at`：`datetime` ISO 格式，精确到微秒（如 `2026-06-12T06:15:16.786479+00:00`）
- `snap.metadata.step`：步骤序号（-1 为 input，0+ 为实际执行步骤）
- `snap.metadata.writes`：`{"node_name": {...}}` ← 产生这条 checkpoint 的节点名（可为空）
- `snap.next`：下一步要执行的节点列表
- `snap.config.configurable.checkpoint_id`：此 checkpoint 的 ID

子图内 checkpoint 需通过 `checkpoint_ns` 单独查询（逻辑同 `restart_from_node`）。

**"节点名"提取**：`metadata.writes` 的第一个 key，即产生该 checkpoint 的节点。空 writes 说明是 input 或 fork 型 checkpoint，可跳过显示或标记为"初始化"。

---

## 实现方案

### 1. 后端：`api/graph_runner.py` — 新增 `get_checkpoints`

```python
async def get_checkpoints(run_id: str) -> list[dict]:
    config = {"configurable": {"thread_id": run_id}}
    result = []

    async for snap in _compiled_graph.aget_state_history(config):
        meta = snap.metadata if isinstance(snap.metadata, dict) else {}
        writes = meta.get("writes") or {}
        step = meta.get("step", -1)
        node_name = next(iter(writes.keys()), None)  # 产生此 checkpoint 的节点
        result.append({
            "checkpoint_id": snap.config["configurable"]["checkpoint_id"],
            "step": step,
            "node": node_name,          # None 表示 input/fork
            "created_at": snap.created_at.isoformat() if snap.created_at else None,
            "next": list(snap.next),
            "checkpoint_ns": "",        # 顶层
        })

    # 子图 namespace 的 checkpoints
    async with aiosqlite.connect(CHECKPOINT_DB) as db:
        async with db.execute(
            "SELECT DISTINCT checkpoint_ns FROM checkpoints "
            "WHERE thread_id=? AND checkpoint_ns != ''",
            (run_id,),
        ) as cur:
            nss = [r[0] for r in await cur.fetchall()]

    for ns in nss:
        sub_config = {"configurable": {"thread_id": run_id, "checkpoint_ns": ns}}
        top_node = ns.split(":")[0]
        async for snap in _compiled_graph.aget_state_history(sub_config):
            meta = snap.metadata if isinstance(snap.metadata, dict) else {}
            writes = meta.get("writes") or {}
            step = meta.get("step", -1)
            leaf_node = next(iter(writes.keys()), None)
            node_path = f"{top_node}/{leaf_node}" if leaf_node else None
            result.append({
                "checkpoint_id": snap.config["configurable"]["checkpoint_id"],
                "step": step,
                "node": node_path,
                "created_at": snap.created_at.isoformat() if snap.created_at else None,
                "next": list(snap.next),
                "checkpoint_ns": ns,
            })

    # 按时间升序，过滤掉 node=None 的（input/fork）
    result = [r for r in result if r["node"] is not None]
    result.sort(key=lambda r: r["created_at"] or "")
    return result
```

### 2. 后端：`api/routers/runs.py` — 新增路由

```python
@router.get("/runs/{run_id}/checkpoints")
async def get_checkpoints(run_id: str):
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    return await runner.get_checkpoints(run_id)
```

### 3. 前端：`web/src/api/client.ts` — 新增 `getCheckpoints`

```typescript
export interface CheckpointEntry {
  checkpoint_id: string
  step: number
  node: string | null
  created_at: string | null
  next: string[]
  checkpoint_ns: string
}

getCheckpoints: (runId: string) =>
  request<CheckpointEntry[]>(`/runs/${runId}/checkpoints`),
```

### 4. 前端：新增 `web/src/components/panels/CheckpointTimeline.tsx`

```tsx
// 时间线面板，放在 Sidebar 下方折叠区，或右侧 tab
// 每条目显示：
//   · 节点名（node_path，如 init_subgraph/load_config）
//   · 时间（created_at 格式化为 HH:mm:ss）
//   · "从此跑" 按钮（调 api.restartFrom，逻辑同 InternalNode 的 ↺）

interface Props {
  runId: string
}

export default function CheckpointTimeline({ runId }: Props) {
  const [entries, setEntries] = useState<CheckpointEntry[]>([])
  const { runs, currentRunId, upsertRun, resetNodeStatuses } = useRunStore()

  useEffect(() => {
    if (!runId) return
    api.getCheckpoints(runId).then(setEntries).catch(console.error)
  }, [runId])

  const handleRestartFrom = async (nodePath: string) => {
    await api.restartFrom(runId, nodePath)
    resetNodeStatuses()
    const run = runs[runId]
    if (run) upsertRun({ ...run, status: 'running' })
  }

  return (
    <div className="text-xs">
      {entries.map((e) => (
        <div key={e.checkpoint_id} className="flex items-center gap-2 px-3 py-1.5 border-b hover:bg-gray-50">
          <div className="flex-1 truncate font-mono text-gray-700">{e.node}</div>
          <div className="text-gray-400 shrink-0">
            {e.created_at ? new Date(e.created_at).toLocaleTimeString() : '—'}
          </div>
          <button
            className="shrink-0 text-gray-400 hover:text-blue-600"
            title="从此节点重新运行"
            onClick={() => e.node && handleRestartFrom(e.node)}
          >
            ↺
          </button>
        </div>
      ))}
      {entries.length === 0 && (
        <div className="px-3 py-2 text-gray-400">暂无执行记录</div>
      )}
    </div>
  )
}
```

### 5. 前端：`web/src/components/layout/Sidebar.tsx` — 挂载时间线

在 Sidebar 底部加一个折叠区（`currentRunId` 选中时展开）：

```tsx
// Sidebar 底部，currentRunId 存在时显示
{currentRunId && (
  <div className="border-t">
    <div className="px-3 py-2 text-xs font-semibold text-gray-500">执行历史</div>
    <div className="max-h-48 overflow-y-auto">
      <CheckpointTimeline runId={currentRunId} />
    </div>
  </div>
)}
```

---

## 关键文件

| 文件 | 改动 |
|------|------|
| `api/graph_runner.py` | 新增 `get_checkpoints(run_id)` |
| `api/routers/runs.py` | 新增 `GET /runs/{run_id}/checkpoints` |
| `web/src/api/client.ts` | 新增 `getCheckpoints` + `CheckpointEntry` 类型 |
| `web/src/components/panels/CheckpointTimeline.tsx` | 新建时间线组件 |
| `web/src/components/layout/Sidebar.tsx` | Sidebar 底部挂载时间线 |

---

## 验证

1. 跑一个有多步骤的 run（至少 2 个节点完成）
2. Sidebar 底部出现"执行历史"区，列出已完成节点 + 时间
3. 点击某条的 `↺`，确认从该节点重跑，后续节点重置
4. 刷新页面，切换到已完成的 run，时间线仍然显示（从后端读，不依赖内存状态）
