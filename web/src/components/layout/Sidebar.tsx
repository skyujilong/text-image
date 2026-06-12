import { useEffect } from 'react'
import { useRunStore } from '@/store/runStore'
import { api } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const STATUS_BADGE: Record<string, string> = {
  pending: 'bg-gray-100 text-gray-700',
  running: 'bg-blue-100 text-blue-700',
  waiting_human: 'bg-orange-100 text-orange-700',
  done: 'bg-green-100 text-green-700',
  error: 'bg-red-100 text-red-700',
}

interface SidebarProps {
  onNewRun: () => void
}

export default function Sidebar({ onNewRun }: SidebarProps) {
  const { runs, currentRunId, setRuns, setCurrentRunId, resetNodeStatuses, resetDrill } = useRunStore()

  useEffect(() => {
    api.listRuns().then(setRuns).catch(console.error)
  }, [])

  const handleSelectRun = (runId: string) => {
    setCurrentRunId(runId)
    resetNodeStatuses()
    resetDrill()
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
            <Badge className={cn('text-xs mt-1', STATUS_BADGE[run.status])}>
              {run.status}
            </Badge>
          </div>
        ))}
      </div>
    </aside>
  )
}
