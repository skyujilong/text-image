import { useEffect, useState } from 'react'
import { useRunStore } from '@/store/runStore'
import { api, type RunMeta } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import CheckpointTimeline from '@/components/panels/CheckpointTimeline'

const STATUS_BADGE: Record<string, string> = {
  pending: 'bg-gray-100 text-gray-700',
  running: 'bg-blue-100 text-blue-700',
  waiting_human: 'bg-orange-100 text-orange-700',
  done: 'bg-green-100 text-green-700',
  error: 'bg-red-100 text-red-700',
}

interface SidebarProps {
  onNewRun: () => void
  onCloneRun: (run: RunMeta) => void
}

export default function Sidebar({ onNewRun, onCloneRun }: SidebarProps) {
  const { runs, currentRunId, setRuns, setCurrentRunId, upsertRun, resetNodeStatuses, resetDrill, incrementStreamGeneration, setRunError } = useRunStore()
  const [retrying, setRetrying] = useState<string | null>(null)

  useEffect(() => {
    api.listRuns().then(setRuns).catch(console.error)
  }, [])

  const handleSelectRun = (runId: string) => {
    setCurrentRunId(runId)
    resetNodeStatuses()
    resetDrill()
    setRunError(null) // 切换 Run 时清空错误
  }

  const handleRetry = async (e: React.MouseEvent, runId: string) => {
    e.stopPropagation()
    setRetrying(runId)
    try {
      setRunError(null) // 重试前先清空旧错误
      await api.retryRun(runId)
      upsertRun({ ...runs[runId], status: 'running' })
      setCurrentRunId(runId)
      resetNodeStatuses()
      resetDrill()
      incrementStreamGeneration() // 触发 SSE 重新连接
    } catch (err) {
      console.error(err)
    } finally {
      setRetrying(null)
    }
  }

  const sorted = Object.values(runs).sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  )

  return (
    <aside className="w-56 border-r flex flex-col h-full">
      <div className="p-3 border-b">
        <Button className="w-full" size="sm" onClick={onNewRun}>
          + 新建 Run
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto">
        {sorted.map((run) => (
          <div
            key={run.run_id}
            className={cn(
              'px-3 py-2 cursor-pointer hover:bg-gray-50 border-b',
              currentRunId === run.run_id && 'bg-blue-50'
            )}
            onClick={() => handleSelectRun(run.run_id)}
          >
            <div className="text-sm font-medium truncate">
              {run.novel_title || run.run_id.slice(0, 8)}
            </div>
            <div className="flex items-center gap-2 mt-1">
              <Badge className={cn('text-xs', STATUS_BADGE[run.status])}>
                {run.status}
              </Badge>
              {run.status === 'error' && (
                <button
                  className="text-xs text-red-600 underline disabled:opacity-50"
                  disabled={retrying === run.run_id}
                  onClick={(e) => handleRetry(e, run.run_id)}
                >
                  {retrying === run.run_id ? '...' : '重试'}
                </button>
              )}
              {(run.status === 'error' || run.status === 'done') && (
                <button
                  className="text-xs text-gray-500 underline hover:text-blue-600"
                  onClick={(e) => { e.stopPropagation(); onCloneRun(run) }}
                >
                  改参数
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
      {currentRunId && (
        <div className="border-t">
          <div className="px-3 py-2 text-xs font-semibold text-gray-500">执行历史</div>
          <div className="max-h-48 overflow-y-auto">
            <CheckpointTimeline runId={currentRunId} />
          </div>
        </div>
      )}
    </aside>
  )
}
