import { useEffect, useState } from 'react'
import { api, type CheckpointEntry } from '@/api/client'
import { useRunStore } from '@/store/runStore'

interface Props {
  runId: string
}

export default function CheckpointTimeline({ runId }: Props) {
  const [entries, setEntries] = useState<CheckpointEntry[]>([])
  const {
    runs,
    setRuns,
    upsertRun,
    resetNodeStatuses,
    resetDrill,
    setCurrentRunId,
    incrementStreamGeneration,
    setRunError,
  } = useRunStore()

  useEffect(() => {
    if (!runId) return
    api.getCheckpoints(runId).then(setEntries).catch(console.error)
  }, [runId])

  // 覆盖重跑：在原 thread 上从该节点前重放（旧 checkpoint 在 append-only 树中保留）
  const handleRestartFrom = async (nodePath: string) => {
    setRunError(null) // 重新运行前先清空旧错误
    await api.restartFrom(runId, nodePath)
    setCurrentRunId(runId)
    resetNodeStatuses()
    resetDrill()
    const run = runs[runId]
    if (run) upsertRun({ ...run, status: 'running' })
    incrementStreamGeneration() // 触发 SSE 重新连接
  }

  // 分叉：从该 checkpoint 复制出独立新 run，原 run 历史不动
  // 仅顶层 checkpoint 支持分叉（子图内中间点 fork 暂不支持）
  const handleFork = async (checkpointId: string) => {
    setRunError(null)
    const { run_id: newId } = await api.forkRun(runId, checkpointId)
    const all = await api.listRuns()
    setRuns(all)
    setCurrentRunId(newId)
    resetNodeStatuses()
    resetDrill()
    incrementStreamGeneration()
  }

  return (
    <div className="text-xs">
      {entries.map((e) => (
        <div key={e.checkpoint_id} className="flex items-center gap-2 px-3 py-1.5 border-b hover:bg-gray-50">
          <div className="flex-1 truncate font-mono text-gray-700">{e.node ?? '(初始化)'}</div>
          <div className="text-gray-400 shrink-0">
            {e.created_at ? new Date(e.created_at).toLocaleTimeString() : '—'}
          </div>
          <button
            className="shrink-0 text-gray-400 hover:text-blue-600"
            title="从此节点重跑（覆盖当前分支）"
            onClick={() => e.node && handleRestartFrom(e.node)}
          >
            ↺
          </button>
          {e.checkpoint_ns === '' && (
            <button
              className="shrink-0 text-gray-400 hover:text-green-600"
              title="从此点分叉新 Run（保留原历史）"
              onClick={() => handleFork(e.checkpoint_id)}
            >
              分叉
            </button>
          )}
        </div>
      ))}
      {entries.length === 0 && (
        <div className="px-3 py-2 text-gray-400">暂无执行记录</div>
      )}
    </div>
  )
}
