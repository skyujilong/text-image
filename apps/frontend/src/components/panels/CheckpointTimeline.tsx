import { useEffect, useRef, useState } from 'react'
import { GitBranch, RotateCcw } from 'lucide-react'
import { api, type CheckpointEntry } from '@/api/client'
import { useRunStore } from '@/store/runStore'
import { formatNodePathLabel, formatRestartTooltip } from '@/constants/nodeLabels'

const ITEM_HEIGHT = 36 // px，每行高度（需与实际 py-1.5 + border 对齐）
const OVERSCAN = 5   // 视窗外额外渲染行数，避免快速滚动白屏

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
    graphScope,
  } = useRunStore()

  // 虚拟滚动状态
  const roRef = useRef<ResizeObserver | null>(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewHeight, setViewHeight] = useState(300)

  // ref callback：容器 DOM 出现/消失时立即挂/卸 ResizeObserver
  const scrollRef = (el: HTMLDivElement | null) => {
    roRef.current?.disconnect()
    roRef.current = null
    if (!el) return
    setViewHeight(el.clientHeight)
    const ro = new ResizeObserver(() => setViewHeight(el.clientHeight))
    ro.observe(el)
    roRef.current = ro
  }

  // 初次加载 + runId 变化时拉取
  useEffect(() => {
    if (!runId) return
    api.getCheckpoints(runId).then(setEntries).catch(console.error)
  }, [runId])

  // 运行中每 3 秒轮询刷新
  const currentRun = runs[runId]
  const isActive = currentRun?.status === 'running' || currentRun?.status === 'waiting_human'
  useEffect(() => {
    if (!runId || !isActive) return
    const timer = setInterval(() => {
      api.getCheckpoints(runId).then(setEntries).catch(console.error)
    }, 3000)
    return () => clearInterval(timer)
  }, [runId, isActive])

  // 覆盖重跑：在指定 scope 的 thread 上从该 checkpoint 重放（精准回溯）
  const handleRestartFrom = async (scope: string, checkpointId: string, node: string) => {
    setRunError(null)
    await api.restartFrom(runId, scope, checkpointId, node)
    setCurrentRunId(runId)
    resetNodeStatuses()
    resetDrill()
    const run = runs[runId]
    if (run) upsertRun({ ...run, status: 'running' })
    incrementStreamGeneration()
  }

  // 分叉：从该 scope 的 checkpoint 复制出独立新 run，原 run 历史不动
  const handleFork = async (scope: string, checkpointId: string) => {
    setRunError(null)
    const { run_id: newId } = await api.forkRun(runId, scope, checkpointId)
    const all = await api.listRuns()
    setRuns(all)
    setCurrentRunId(newId)
    resetNodeStatuses()
    resetDrill()
    incrementStreamGeneration()
  }

  // 按当前 scope 过滤执行历史（规划阶段只看 plan、渲染阶段只看 render、主流程只看 main）
  const scopedEntries = entries.filter((e) => e.scope === graphScope)

  const totalHeight = scopedEntries.length * ITEM_HEIGHT
  const startIdx = Math.max(0, Math.floor(scrollTop / ITEM_HEIGHT) - OVERSCAN)
  const endIdx = Math.min(scopedEntries.length, Math.ceil((scrollTop + viewHeight) / ITEM_HEIGHT) + OVERSCAN)
  const visibleEntries = scopedEntries.slice(startIdx, endIdx)

  return (
    <div className="text-xs flex flex-col h-full">
      {scopedEntries.length === 0 ? (
        <div className="px-3 py-3 text-muted-foreground text-center">暂无执行记录</div>
      ) : (
        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto relative"
          onScroll={(e) => setScrollTop((e.target as HTMLDivElement).scrollTop)}
        >
          {/* 撑开滚动总高度 */}
          <div style={{ height: totalHeight, position: 'relative' }}>
            {visibleEntries.map((e, i) => {
              return (
              <div
                key={`${e.scope}-${e.checkpoint_id}`}
                style={{ position: 'absolute', top: (startIdx + i) * ITEM_HEIGHT, left: 0, right: 0, height: ITEM_HEIGHT }}
                className="group flex items-center gap-1.5 px-3 border-b border-border/60 hover:bg-accent"
              >
                <span className="shrink-0 text-[10px] uppercase tracking-wider text-muted-foreground/50 w-10">
                  {e.scope}
                </span>
                <div className="flex-1 truncate text-foreground" title={e.node ?? ''}>
                  {formatNodePathLabel(e.node)}
                </div>
                <div className="text-muted-foreground/70 shrink-0 tabular-nums">
                  {e.created_at ? new Date(e.created_at).toLocaleTimeString() : '—'}
                </div>
                <button
                  className="shrink-0 size-6 inline-flex items-center justify-center rounded text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-blue-600 hover:bg-blue-500/10 transition-colors"
                  title={formatRestartTooltip(e.node)}
                  onClick={() => e.node && handleRestartFrom(e.scope, e.checkpoint_id, e.node)}
                >
                  <RotateCcw className="size-3.5" />
                </button>
                <button
                  className="shrink-0 size-6 inline-flex items-center justify-center rounded text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-green-600 hover:bg-green-500/10 transition-colors"
                  title="从此点分叉新 Run（保留原历史）"
                  onClick={() => handleFork(e.scope, e.checkpoint_id)}
                >
                  <GitBranch className="size-3.5" />
                </button>
              </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
