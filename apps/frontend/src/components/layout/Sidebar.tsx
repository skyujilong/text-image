import { useEffect, useRef, useState } from 'react'
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
  const {
    runs,
    currentRunId,
    setRuns,
    setCurrentRunId,
    upsertRun,
    resetNodeStatuses,
    resetDrill,
    incrementStreamGeneration,
    setRunError,
  } = useRunStore()
  const [retrying, setRetrying] = useState<string | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const pickerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.listRuns().then(setRuns).catch(console.error)
  }, [])

  // 点击选择器外部时收起下拉
  useEffect(() => {
    if (!pickerOpen) return
    const onDown = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setPickerOpen(false)
      }
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [pickerOpen])

  const handleSelectRun = (runId: string) => {
    setCurrentRunId(runId)
    resetNodeStatuses()
    resetDrill()
    setRunError(null) // 切换 Run 时清空错误
    setPickerOpen(false)
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

  // 进入重命名编辑态
  const startEdit = (e: React.MouseEvent, runId: string) => {
    e.stopPropagation()
    const run = runs[runId]
    setEditingId(runId)
    setEditTitle(run?.novel_title || '')
  }

  // 提交重命名
  const commitEdit = async (runId: string) => {
    const title = editTitle.trim()
    setEditingId(null)
    if (!title) return
    try {
      await api.updateRun(runId, title)
      const run = runs[runId]
      if (run) upsertRun({ ...run, novel_title: title })
    } catch (err) {
      console.error(err)
    }
  }

  const sorted = Object.values(runs).sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  )
  const currentRun = currentRunId ? runs[currentRunId] : undefined

  return (
    <aside className="w-56 border-r flex flex-col h-full">
      <div className="p-3 border-b">
        <Button className="w-full" size="sm" onClick={onNewRun}>
          + 新建 Run
        </Button>
      </div>

      {/* Run 选择器：紧凑下拉，替代原先占用大量空间的列表 */}
      <div className="p-2 border-b" ref={pickerRef}>
        {editingId === currentRunId && currentRun ? (
          <input
            autoFocus
            className="text-sm font-medium w-full px-2 py-1 border rounded"
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            onBlur={() => commitEdit(currentRun.run_id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitEdit(currentRun.run_id)
              if (e.key === 'Escape') setEditingId(null)
            }}
          />
        ) : (
          <button
            className="w-full flex items-center gap-2 px-2 py-1.5 border rounded hover:bg-gray-50 text-left"
            onClick={() => setPickerOpen((v) => !v)}
          >
            <Badge className={cn('text-xs shrink-0', STATUS_BADGE[currentRun?.status || 'pending'])}>
              {currentRun?.status || '—'}
            </Badge>
            <span className="text-sm font-medium truncate flex-1">
              {currentRun?.novel_title || currentRun?.run_id.slice(0, 8) || '选择 Run'}
            </span>
            <span className="text-gray-400 text-xs shrink-0">{pickerOpen ? '▲' : '▼'}</span>
          </button>
        )}

        {pickerOpen && (
          <div className="mt-1 border rounded bg-white shadow-md max-h-72 overflow-y-auto z-10">
            {sorted.length === 0 && (
              <div className="px-3 py-2 text-xs text-gray-400">暂无 Run</div>
            )}
            {sorted.map((run) => (
              <div
                key={run.run_id}
                className={cn(
                  'px-2 py-1.5 cursor-pointer hover:bg-gray-50 border-b last:border-b-0',
                  currentRunId === run.run_id && 'bg-blue-50'
                )}
                onClick={() => handleSelectRun(run.run_id)}
              >
                <div className="flex items-center gap-2">
                  <Badge className={cn('text-xs shrink-0', STATUS_BADGE[run.status])}>
                    {run.status}
                  </Badge>
                  <span className="text-sm truncate flex-1">
                    {run.novel_title || run.run_id.slice(0, 8)}
                  </span>
                </div>
                {run.parent_run_id && (
                  <div className="text-[10px] text-gray-400 mt-0.5 truncate">
                    ← 分叉自 {run.parent_run_id.slice(0, 8)}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* 当前 Run 的操作行 */}
        {currentRun && editingId !== currentRun.run_id && (
          <div className="flex items-center gap-3 mt-1.5 px-1">
            <button
              className="text-xs text-gray-500 hover:text-blue-600"
              title="重命名"
              onClick={(e) => startEdit(e, currentRun.run_id)}
            >
              ✎ 重命名
            </button>
            {currentRun.status === 'error' && (
              <button
                className="text-xs text-red-600 underline disabled:opacity-50"
                disabled={retrying === currentRun.run_id}
                onClick={(e) => handleRetry(e, currentRun.run_id)}
              >
                {retrying === currentRun.run_id ? '重试中...' : '重试'}
              </button>
            )}
            {(currentRun.status === 'error' || currentRun.status === 'done') && (
              <button
                className="text-xs text-gray-500 underline hover:text-blue-600"
                onClick={() => onCloneRun(currentRun)}
              >
                改参数
              </button>
            )}
          </div>
        )}
      </div>

      {/* 执行历史：占据剩余空间，往上扩展可见条目 */}
      {currentRunId ? (
        <div className="flex-1 flex flex-col min-h-0">
          <div className="px-3 py-2 text-xs font-semibold text-gray-500 border-b">执行历史</div>
          <div className="flex-1 overflow-y-auto min-h-0">
            <CheckpointTimeline runId={currentRunId} />
          </div>
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center text-xs text-gray-400">
          选择一个 Run 查看执行历史
        </div>
      )}
    </aside>
  )
}
