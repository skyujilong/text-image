import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Check,
  ChevronDown,
  GitBranch,
  GitCompare,
  LayoutGrid,
  Pencil,
  Plus,
  RotateCcw,
  Settings2,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import { useRunStore } from '@/store/runStore'
import { api, type RunMeta } from '@/api/client'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import CheckpointTimeline from '@/components/panels/CheckpointTimeline'

// 运行状态 → 语义化展示（圆点颜色 + 中文标签）。
// 与 CheckpointTimeline 等面板共用同一套状态语义，避免各处配色不一致。
const STATUS_META: Record<
  RunMeta['status'],
  { label: string; dot: string; badge: string }
> = {
  pending: { label: '待运行', dot: 'bg-gray-400', badge: 'bg-gray-100 text-gray-600' },
  running: { label: '运行中', dot: 'bg-blue-500', badge: 'bg-blue-100 text-blue-700' },
  waiting_human: { label: '待审阅', dot: 'bg-orange-500', badge: 'bg-orange-100 text-orange-700' },
  done: { label: '已完成', dot: 'bg-green-500', badge: 'bg-green-100 text-green-700' },
  error: { label: '出错', dot: 'bg-red-500', badge: 'bg-red-100 text-red-700' },
}

interface SidebarProps {
  onNewRun: () => void
  onCloneRun: (run: RunMeta) => void
}

export default function Sidebar({ onNewRun, onCloneRun }: SidebarProps) {
  const navigate = useNavigate()
  const {
    runs,
    currentRunId,
    setRuns,
    setCurrentRunId,
    upsertRun,
    removeRun,
    resetNodeStatuses,
    resetDrill,
    incrementStreamGeneration,
    setRunError,
  } = useRunStore()
  const [retrying, setRetrying] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
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
    resetDrill()
    setRunError(null) // 切换 Run 时清空错误
    setPickerOpen(false)
    navigate(`/runs/${runId}`)
  }

  const handleRetry = async (e: React.MouseEvent, runId: string) => {
    e.stopPropagation()
    setRetrying(runId)
    try {
      setRunError(null) // 重试前先清空旧错误
      await api.retryRun(runId)
      upsertRun({ ...runs[runId], status: 'running' })
      setCurrentRunId(runId)
      navigate(`/runs/${runId}`)
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

  // 删除废弃 run：仅清理运行记录 + checkpoint，不动 novel_dir。
  // running 状态前端隐藏删除入口；后端兜底 409。
  const handleDelete = async (e: React.MouseEvent, run: RunMeta) => {
    e.stopPropagation()
    const title = run.novel_title || run.run_id.slice(0, 8)
    if (!window.confirm(`确认删除「${title}」？\n仅清理运行记录与 checkpoint，不会删除小说文件。`)) {
      return
    }
    setDeleting(run.run_id)
    try {
      await api.deleteRun(run.run_id)
      removeRun(run.run_id) // 若删的是当前 run，store 自动回退空态并清 SSE
    } catch (err: unknown) {
      const detail =
        (err as { detail?: string })?.detail || '删除失败（可能 run 已变为 running 状态）'
      setRunError(detail)
      console.error(err)
    } finally {
      setDeleting(null)
    }
  }

  const sorted = Object.values(runs).sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  )
  const currentRun = currentRunId ? runs[currentRunId] : undefined
  const currentStatus = currentRun?.status ? STATUS_META[currentRun.status] : undefined

  return (
    <aside className="w-96 border-r border-sidebar-border bg-sidebar flex flex-col h-full">
      {/* 顶部：新建 Run + 全局进化台入口 */}
      <div className="p-3 border-b border-sidebar-border space-y-2">
        <Button className="w-full" size="sm" onClick={onNewRun}>
          <Plus className="size-4" />
          新建 Run
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="w-full text-muted-foreground"
          title="提示词进化台：摩擦度排行 + 归纳候选规则 + 校正规则台账"
          onClick={() => navigate('/prompt-evolution')}
        >
          <Sparkles className="size-4" />
          提示词进化台
        </Button>
      </div>

      {/* Run 选择器：紧凑下拉，替代原先占用大量空间的列表 */}
      <div className="p-3 border-b border-sidebar-border" ref={pickerRef}>
        {editingId === currentRunId && currentRun ? (
          <div className="flex items-center gap-1">
            <input
              autoFocus
              className="text-sm font-medium flex-1 min-w-0 px-2 py-1.5 bg-background border border-input rounded-md focus:outline-none focus:ring-2 focus:ring-ring"
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              onBlur={() => commitEdit(currentRun.run_id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitEdit(currentRun.run_id)
                if (e.key === 'Escape') setEditingId(null)
              }}
            />
            <Button variant="ghost" size="icon" className="size-8" title="确认"
              onMouseDown={(e) => { e.preventDefault(); commitEdit(currentRun.run_id) }}>
              <Check className="size-4" />
            </Button>
            <Button variant="ghost" size="icon" className="size-8" title="取消"
              onMouseDown={(e) => { e.preventDefault(); setEditingId(null) }}>
              <X className="size-4" />
            </Button>
          </div>
        ) : (
          <button
            className="w-full flex items-center gap-2 px-3 py-2 border border-input bg-background rounded-md hover:bg-accent text-left transition-colors"
            onClick={() => setPickerOpen((v) => !v)}
          >
            {currentRun ? (
              <span className={cn('size-2 rounded-full shrink-0', currentStatus?.dot)} />
            ) : (
              <span className="size-2 rounded-full shrink-0 bg-transparent border border-gray-300" />
            )}
            <span className="text-sm font-medium truncate flex-1">
              {currentRun?.novel_title || currentRun?.run_id.slice(0, 8) || '选择 Run'}
            </span>
            <ChevronDown
              className={cn('size-4 text-muted-foreground shrink-0 transition-transform', pickerOpen && 'rotate-180')}
            />
          </button>
        )}

        {/* 下拉列表 */}
        {pickerOpen && (
          <div className="mt-1.5 border border-border rounded-md bg-popover shadow-md max-h-80 overflow-y-auto z-10">
            {sorted.length === 0 && (
              <div className="px-3 py-3 text-xs text-muted-foreground text-center">暂无 Run</div>
            )}
            {sorted.map((run) => {
              const meta = STATUS_META[run.status]
              const isActive = currentRunId === run.run_id
              const title = run.novel_title || run.run_id.slice(0, 8)
              return (
                <div
                  key={run.run_id}
                  className={cn(
                    'group flex items-center gap-2 px-2.5 py-2 cursor-pointer border-b border-border/60 last:border-b-0 transition-colors',
                    isActive ? 'bg-sidebar-accent' : 'hover:bg-accent'
                  )}
                  onClick={() => handleSelectRun(run.run_id)}
                >
                  <span className={cn('size-2 rounded-full shrink-0', meta.dot)} title={meta.label} />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm truncate">{title}</div>
                    <div className="flex items-center gap-1.5 mt-0.5">
                      <span className="text-[10px] text-muted-foreground">{meta.label}</span>
                      {run.parent_run_id && (
                        <span className="text-[10px] text-muted-foreground/80 flex items-center gap-0.5 truncate">
                          <GitBranch className="size-2.5" />
                          {run.parent_run_id.slice(0, 8)}
                        </span>
                      )}
                    </div>
                  </div>
                  {/* running 不可删（无入口）；其余状态显示删除按钮 */}
                  {run.status !== 'running' && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-7 shrink-0 text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-destructive hover:bg-destructive/10"
                      title="删除（仅清理运行记录与 checkpoint，不删小说文件）"
                      disabled={deleting === run.run_id}
                      onClick={(e) => handleDelete(e, run)}
                    >
                      {deleting === run.run_id ? (
                        <RotateCcw className="size-3.5 animate-spin" />
                      ) : (
                        <Trash2 className="size-3.5" />
                      )}
                    </Button>
                  )}
                </div>
              )
            })}
          </div>
        )}

        {/* 当前 Run 的操作行 */}
        {currentRun && editingId !== currentRun.run_id && (
          <div className="flex items-center gap-0.5 mt-2">
            <Button variant="ghost" size="sm" className="h-7 px-2 text-xs text-muted-foreground"
              title="重命名" onClick={(e) => startEdit(e, currentRun.run_id)}>
              <Pencil className="size-3.5" />
              重命名
            </Button>
            {currentRun.status === 'error' && (
              <Button variant="ghost" size="sm" className="h-7 px-2 text-xs text-destructive"
                title="重试" disabled={retrying === currentRun.run_id}
                onClick={(e) => handleRetry(e, currentRun.run_id)}>
                <RotateCcw className={cn('size-3.5', retrying === currentRun.run_id && 'animate-spin')} />
                {retrying === currentRun.run_id ? '重试中' : '重试'}
              </Button>
            )}
            {(currentRun.status === 'error' || currentRun.status === 'done') && (
              <Button variant="ghost" size="sm" className="h-7 px-2 text-xs text-muted-foreground"
                title="改参数重跑" onClick={() => onCloneRun(currentRun)}>
                <Settings2 className="size-3.5" />
                改参数
              </Button>
            )}
            {currentRun.status !== 'pending' && (
              <Button variant="ghost" size="sm" className="h-7 px-2 text-xs text-muted-foreground"
                title="渲染工作台" onClick={() => navigate(`/runs/${currentRun.run_id}/render`)}>
                <LayoutGrid className="size-3.5" />
                渲染工作台
              </Button>
            )}
            {currentRun.status !== 'pending' && (
              <Button variant="ghost" size="sm" className="h-7 px-2 text-xs text-muted-foreground"
                title="提示词检视：本 run 模板 vs 预设对比 + 审阅记录"
                onClick={() => navigate(`/runs/${currentRun.run_id}/prompts`)}>
                <GitCompare className="size-3.5" />
                提示词
              </Button>
            )}
          </div>
        )}
      </div>

      {/* 执行历史：占据剩余空间，滚动由 CheckpointTimeline 内部虚拟滚动容器负责 */}
      {currentRunId ? (
        <div className="flex-1 flex flex-col min-h-0">
          <div className="px-3 py-2 text-xs font-semibold text-muted-foreground border-b border-sidebar-border">
            执行历史
          </div>
          <CheckpointTimeline runId={currentRunId} />
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center text-xs text-muted-foreground px-4 text-center">
          选择一个 Run 查看执行历史
        </div>
      )}
    </aside>
  )
}
