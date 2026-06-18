# 计划：错误信息展示

## Context

节点出错时 `run_error` SSE 事件已携带 `message` 字段（后端 `graph_runner.py:121`），但前端完全不展示。用户只看到节点变红，不知道出了什么错，只能去后端终端翻日志。

目标：在 UI 里直接显示错误信息，做到"节点红了 → 立刻知道原因"。

---

## 数据流现状

```
后端 _run_graph except → push_event(run_id, {"type": "run_error", "message": str(exc)})
                                      ↓ SSE
前端 useRunStream.ts → 收到 run_error → 仅更新 run.status = 'error'，message 丢弃
```

---

## 实现方案

### 1. `web/src/store/runStore.ts` — 新增 `runError` 字段

```ts
interface RunStore {
  // 新增
  runError: string | null
  setRunError: (msg: string | null) => void
}

// 初始值
runError: null,
setRunError: (msg) => set({ runError: msg }),
```

`resetNodeStatuses` 里同时清空 `runError`：
```ts
resetNodeStatuses: () => set({ nodeStatuses: {}, activeInteraction: null, runError: null }),
```

### 2. `web/src/hooks/useRunStream.ts` — 解析 message

```ts
if (type === 'run_error') {
  const msg = event.message as string | undefined
  useRunStore.getState().setRunError(msg ?? '未知错误')
  // ...原有 upsertRun 逻辑不变
}
```

### 3. `web/src/components/flow/FlowCanvas.tsx` — 底部错误条

在画布底部加一个固定错误条，仅当 `runError` 非空时展示：

```tsx
import { useRunStore } from '@/store/runStore'

// FlowCanvas 内部
const { runError } = useRunStore()

// JSX，放在最外层 div 末尾
{runError && (
  <div className="absolute bottom-0 left-0 right-0 z-20 bg-red-50 border-t border-red-200 px-4 py-2 text-sm text-red-700 flex items-start gap-2">
    <span className="shrink-0 font-semibold">错误：</span>
    <pre className="whitespace-pre-wrap break-all flex-1">{runError}</pre>
    <button
      className="shrink-0 text-red-400 hover:text-red-600"
      onClick={() => useRunStore.getState().setRunError(null)}
    >
      ✕
    </button>
  </div>
)}
```

---

## 关键文件

| 文件 | 改动 |
|------|------|
| `web/src/store/runStore.ts` | 新增 `runError` / `setRunError`，`resetNodeStatuses` 里清空 |
| `web/src/hooks/useRunStream.ts` | `run_error` 时调 `setRunError(message)` |
| `web/src/components/flow/FlowCanvas.tsx` | 底部错误条，读 `runError` |

**不需要任何后端改动。**

---

## 验证

1. 故意让某个节点抛异常（或用已知出错的 run）
2. 触发 run，确认画布底部出现红色错误条，内容是后端 traceback
3. 点 ✕ 关闭，错误条消失
4. 切换到另一个 run 或新建 run，错误条自动清空
