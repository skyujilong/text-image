import { useEffect, useState } from 'react'
import { RotateCcw, Check, Loader2, AlertCircle, Grid3x3, List } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { api, fileUrl, type RenderShot } from '@/api/client'
import { useRunStore } from '@/store/runStore'
import { cn } from '@/lib/utils'

interface StoryboardShot {
  storyboard_id?: number
  scene_change?: boolean
  text?: string
  speaker?: string
  subjects?: string[]
  scene_prompt?: string
  [key: string]: unknown
}

interface Props {
  runId: string
  chapterId: string
  storyboard: StoryboardShot[]
}

/**
 * 图片渲染看板：工作台风格，从 ImageRenderPanel 升级。
 *
 * - 网格/列表视图切换
 * - 批量重新抽卡
 * - 完成进度统计
 * - SSE render_image 事件驱动增量更新（由 useRunStream 处理）
 */
export default function ImageRenderBoard({ runId, chapterId, storyboard }: Props) {
  const { renderBoard, mergeRenderBoard, upsertRenderShot } = useRunStore()
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid')
  const [batchRerolling, setBatchRerolling] = useState(false)

  useEffect(() => {
    api.getRenderState(runId)
      .then((board) => mergeRenderBoard(board.shots))
      .catch((e) => console.warn('[render-board] 拉取看板失败', e))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])

  const changeShotIds = new Set(
    storyboard.filter((s) => s.scene_change).map((s) => s.storyboard_id)
  )

  const allDone =
    changeShotIds.size > 0 &&
    [...changeShotIds].every((sid) => {
      const shot = renderBoard[sid as number]
      return shot && shot.status === 'done' && shot.selected
    })

  const completedCount = [...changeShotIds].filter((sid) => {
    const shot = renderBoard[sid as number]
    return shot && shot.status === 'done' && shot.selected
  }).length

  const pendingCount = changeShotIds.size - completedCount

  const handleBatchReroll = async () => {
    if (batchRerolling) return
    setBatchRerolling(true)
    try {
      for (const sid of changeShotIds) {
        const shot = renderBoard[sid as number]
        if (shot && shot.status !== 'rendering') {
          upsertRenderShot({ ...shot, status: 'rendering' })
          await api.rerollShot(runId, sid as number)
        }
      }
    } catch (e) {
      console.error('批量抽卡失败', e)
    } finally {
      setBatchRerolling(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-border shrink-0">
        <span className="text-sm font-medium">图片渲染 · {chapterId}</span>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-muted-foreground">
            已完成 {completedCount}/{changeShotIds.size}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={handleBatchReroll}
            disabled={batchRerolling || pendingCount === 0}
          >
            <RotateCcw className={cn('size-3.5', batchRerolling && 'animate-spin')} />
            全部重新抽卡
          </Button>
          <div className="flex border border-border rounded-md overflow-hidden">
            <button
              className={cn('p-1.5', viewMode === 'grid' ? 'bg-accent text-foreground' : 'text-muted-foreground hover:bg-accent')}
              onClick={() => setViewMode('grid')}
              title="网格视图"
            >
              <Grid3x3 className="size-4" />
            </button>
            <button
              className={cn('p-1.5', viewMode === 'list' ? 'bg-accent text-foreground' : 'text-muted-foreground hover:bg-accent')}
              onClick={() => setViewMode('list')}
              title="列表视图"
            >
              <List className="size-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Shot cards */}
      <div className={cn(
        'flex-1 overflow-y-auto p-4',
        viewMode === 'grid' ? 'grid grid-cols-2 gap-3 auto-rows-min' : 'flex flex-col gap-3'
      )}>
        {storyboard.map((sb, i) => {
          const sid = sb.storyboard_id ?? i
          if (!sb.scene_change) {
            return (
              <div
                key={i}
                className="border border-dashed border-border rounded p-2 text-xs text-muted-foreground bg-accent/20"
              >
                <span className="font-mono mr-2">{sid}</span>
                {sb.speaker ?? ''}：{sb.text}
                <span className="ml-2 italic">（复用上一镜头画面）</span>
              </div>
            )
          }
          return (
            <ShotCard
              key={i}
              runId={runId}
              storyboardId={sid as number}
              text={sb.text}
              speaker={sb.speaker}
              fallbackPrompt={sb.scene_prompt ?? ''}
              shot={renderBoard[sid as number]}
              compact={viewMode === 'grid'}
            />
          )
        })}
        {Object.keys(renderBoard).length === 0 && (
          <div className="col-span-full flex items-center justify-center py-12 text-sm text-muted-foreground">
            渲染队列启动中，图片即将逐张出现…
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center gap-2 px-4 py-2 border-t border-border shrink-0">
        {!allDone && (
          <span className="text-xs text-muted-foreground">
            还有 {pendingCount} 个镜头未完成
          </span>
        )}
        {allDone && (
          <span className="text-xs text-green-600 flex items-center gap-1">
            <Check className="size-3.5" />
            全部换图点已完成选图
          </span>
        )}
      </div>
    </div>
  )
}

/** 单个换图点卡片：提示词 + 选定图 + 候选切换 + 重新抽卡。 */
function ShotCard({
  runId,
  storyboardId,
  text,
  speaker,
  fallbackPrompt,
  shot,
  compact,
}: {
  runId: string
  storyboardId: number
  text?: string
  speaker?: string
  fallbackPrompt: string
  shot?: RenderShot
  compact?: boolean
}) {
  const [prompt, setPrompt] = useState(shot?.prompt ?? fallbackPrompt)
  const [edited, setEdited] = useState(false)
  const { upsertRenderShot } = useRunStore()

  useEffect(() => {
    if (!edited && shot?.prompt) setPrompt(shot.prompt)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shot?.prompt])

  const status = shot?.status ?? 'pending'
  const rendering = status === 'rendering' || status === 'pending'

  const handleReroll = async () => {
    try {
      if (shot) upsertRenderShot({ ...shot, status: 'rendering' })
      await api.rerollShot(runId, storyboardId, prompt)
    } catch (e) {
      console.error('重新抽卡失败', e)
    }
  }

  const handleSelect = async (candidatePath: string) => {
    try {
      await api.selectCandidate(runId, storyboardId, candidatePath)
      if (shot) {
        upsertRenderShot({
          ...shot,
          selected: candidatePath,
          selected_url: fileUrl(candidatePath),
        })
      }
    } catch (e) {
      console.error('选定候选失败', e)
    }
  }

  return (
    <div className={cn(
      'border border-border rounded bg-accent/40 flex flex-col gap-2',
      compact ? 'p-2' : 'p-3'
    )}>
      <div className="flex items-center gap-2 text-xs">
        <span className="font-mono text-muted-foreground">{storyboardId}</span>
        {shot?.workflow && (
          <span className="px-1 rounded bg-accent text-muted-foreground">
            {shot.workflow === 'qwen_edit' ? '参考图生图' : '文生图'}
          </span>
        )}
        {shot?.subjects && shot.subjects.length > 0 && (
          <span className="text-muted-foreground truncate">主体：{shot.subjects.join('、')}</span>
        )}
        <StatusDot status={status} />
      </div>

      {(speaker || text) && (
        <div className="text-xs text-foreground truncate">
          {speaker ?? ''}：{text}
        </div>
      )}

      {/* Selected image preview */}
      <div className="flex items-center justify-center bg-background rounded border border-border min-h-[120px]">
        {shot?.selected_url ? (
          <img
            src={shot.selected_url}
            alt={`shot ${storyboardId}`}
            className={cn('rounded object-contain', compact ? 'max-h-32' : 'max-h-48')}
          />
        ) : rendering ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground py-6">
            <Loader2 className="size-4 animate-spin" /> 生成中…
          </div>
        ) : status === 'error' ? (
          <div className="flex items-center gap-2 text-xs text-destructive py-6">
            <AlertCircle className="size-4" /> {shot?.error ?? '生成失败'}
          </div>
        ) : (
          <div className="text-xs text-muted-foreground py-6">等待生成</div>
        )}
      </div>

      {/* Candidate thumbnails */}
      {shot && shot.candidates.length > 1 && (
        <div className="flex flex-wrap gap-1.5">
          {shot.candidates.map((c) => (
            <button
              key={c.path}
              onClick={() => handleSelect(c.path)}
              className={cn(
                'size-12 rounded border overflow-hidden transition',
                c.path === shot.selected
                  ? 'border-primary ring-2 ring-ring'
                  : 'border-border opacity-70 hover:opacity-100'
              )}
            >
              <img src={c.url} alt="候选" className="size-full object-cover" />
            </button>
          ))}
        </div>
      )}

      {/* Editable prompt */}
      <textarea
        value={prompt}
        onChange={(e) => {
          setPrompt(e.target.value)
          setEdited(true)
        }}
        className="w-full min-h-[48px] text-xs border border-input rounded p-2 resize-y bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
        placeholder="画面提示词"
      />

      <div className="flex justify-end">
        <Button variant="ghost" size="sm" onClick={handleReroll} disabled={rendering}>
          <RotateCcw className={cn('size-3.5', rendering && 'animate-spin')} />
          重新抽卡
        </Button>
      </div>
    </div>
  )
}

/** 状态圆点：渲染中蓝 / 完成绿 / 出错红 / 待处理灰。 */
function StatusDot({ status }: { status: RenderShot['status'] }) {
  const meta: Record<RenderShot['status'], { color: string; label: string }> = {
    pending: { color: 'bg-muted-foreground', label: '待生成' },
    rendering: { color: 'bg-blue-500', label: '生成中' },
    done: { color: 'bg-green-500', label: '已完成' },
    error: { color: 'bg-destructive', label: '出错' },
  }
  const m = meta[status]
  return (
    <span className="ml-auto flex items-center gap-1 text-muted-foreground">
      <span className={cn('size-2 rounded-full', m.color)} />
      {m.label}
    </span>
  )
}
