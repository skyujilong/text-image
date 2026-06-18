import { useEffect, useState } from 'react'
import { api, type CheckpointEntry } from '@/api/client'
import { useRunStore } from '@/store/runStore'

interface Props {
  runId: string
}

export default function CheckpointTimeline({ runId }: Props) {
  const [entries, setEntries] = useState<CheckpointEntry[]>([])
  const { runs, upsertRun, resetNodeStatuses, resetDrill, setCurrentRunId } = useRunStore()

  useEffect(() => {
    if (!runId) return
    api.getCheckpoints(runId).then(setEntries).catch(console.error)
  }, [runId])

  const handleRestartFrom = async (nodePath: string) => {
    await api.restartFrom(runId, nodePath)
    setCurrentRunId(runId)
    resetNodeStatuses()
    resetDrill()
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
