import { useEffect, useState, useMemo } from 'react'
import { RotateCcw, Check, Loader2, AlertCircle, Play, X } from 'lucide-react'
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
 * 图片渲染看板：网格布局，所有镜头一目了然。
 *
 * - 每行 2-3 个镜头卡片，无需点击切换
 * - 每个卡片包含：预览图 + 状态 + 提示词 + 重新抽卡
 * - 不自动启动渲染：需用户点击【启动渲染】按钮
 */
export default function ImageRenderBoard({ runId, chapterId, storyboard }: Props) {
  const { renderBoard, mergeRenderBoard, upsertRenderShot, renderStarted, setRenderStarted } = useRunStore()
  const [startingRender, setStartingRender] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<number | null>(null)

  // 筛选出有 scene_change 的镜头（换图点）
  const changeShotIds = useMemo(() => {
    return storyboard
      .filter((s) => s.scene_change)
      .map((s) => s.storyboard_id as number)
      .sort((a, b) => a - b)
  }, [storyboard])

  // 初始化预览数据：只调用 preview 端点，不触发渲染
  useEffect(() => {
    if (!runId || !chapterId) return

    api.getRenderPreview(runId, chapterId)
      .then((board) => {
        mergeRenderBoard(board.shots)
        // 只要有候选图或处于渲染中的 shot，标记为已启动
        // 保证刷新页面/切回来后，能继续定时拉取状态
        const hasCandidates = board.shots.some((s) => s.candidates.length > 0)
        const isRendering = board.shots.some((s) => s.status === 'rendering')
        if (hasCandidates || isRendering) {
          setRenderStarted(chapterId, true)
        }
      })
      .catch((e) => {
        console.warn('[render-board] 拉取预览失败', e)
        setError(e instanceof Error ? e.message : String(e))
      })
  }, [runId, chapterId, mergeRenderBoard, setRenderStarted])

  // 渲染已启动时，才调用 getRenderState（会触发渲染会话）
  // 同时也用于：SSE 断连重连后，补拉断连期间生成的图片
  useEffect(() => {
    if (!runId || !renderStarted[chapterId]) return

    const refreshBoard = () => {
      api.getRenderState(runId)
        .then((board) => mergeRenderBoard(board.shots))
        .catch((e) => console.warn('[render-board] 拉取渲染状态失败', e))
    }

    // 立即拉取一次
    refreshBoard()

    // SSE 重连时（通过 streamGeneration 检测），补拉一次
    // 切走页面再切回来期间可能丢失 render_image 事件
    const interval = setInterval(refreshBoard, 10000)

    return () => clearInterval(interval)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, chapterId, renderStarted[chapterId]])

  const completedCount = changeShotIds.filter((sid) => {
    const shot = renderBoard[sid]
    return shot && shot.status === 'done' && shot.selected
  }).length

  const renderingId = changeShotIds.find((sid) => {
    const shot = renderBoard[sid]
    return shot && shot.status === 'rendering'
  })

  const allDone = changeShotIds.length > 0 && completedCount === changeShotIds.length
  const isStarted = renderStarted[chapterId] || false
  const [batchRerolling, setBatchRerolling] = useState(false)

  const handleStartRender = async () => {
    if (!runId || !chapterId) return
    setStartingRender(true)
    setError(null)
    try {
      await api.startChapterRender(runId, chapterId)
      setRenderStarted(chapterId, true)
    } catch (e) {
      console.error('启动渲染失败', e)
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setStartingRender(false)
    }
  }

  const handleBatchReroll = async () => {
    if (!runId || !chapterId || batchRerolling) return
    setBatchRerolling(true)
    try {
      for (const sid of changeShotIds) {
        const shot = renderBoard[sid]
        if (shot && shot.status !== 'rendering') {
          upsertRenderShot({ ...shot, status: 'rendering' })
          await api.rerollShot(runId, sid, chapterId)
        }
      }
    } catch (e) {
      console.error('批量抽卡失败', e)
    } finally {
      setBatchRerolling(false)
    }
  }

  // Build storyboard map for quick lookup of scene_prompt
  const storyboardMap = useMemo(() => {
    const map = new Map<number, StoryboardShot>()
    for (const sb of storyboard) {
      if (sb.storyboard_id != null) map.set(sb.storyboard_id, sb)
    }
    return map
  }, [storyboard])

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-border shrink-0">
        <span className="text-sm font-medium">图片渲染 · {chapterId}</span>
        <span className="text-xs text-muted-foreground">
          {isStarted
            ? `已完成 ${completedCount}/${changeShotIds.length}`
            : `${changeShotIds.length} 个镜头待渲染`}
        </span>

        {isStarted && renderingId != null && (
          <span className="text-xs text-blue-600 flex items-center gap-1">
            <Loader2 className="size-3 animate-spin" />
            正在渲染：镜头 #{renderingId}（后台持续运行，切走不中断）
          </span>
        )}

        {error && (
          <span className="text-xs text-destructive ml-2">{error}</span>
        )}

        <div className="ml-auto flex items-center gap-2">
          {isStarted && !allDone && (
            <Button
              variant="ghost"
              size="sm"
              onClick={handleBatchReroll}
              disabled={batchRerolling}
            >
              <RotateCcw className={cn('size-4 mr-1', batchRerolling && 'animate-spin')} />
              全部重新抽卡
            </Button>
          )}
          {!isStarted && !allDone && (
            <Button
              variant="default"
              size="sm"
              onClick={handleStartRender}
              disabled={startingRender}
            >
              {startingRender ? (
                <Loader2 className="size-4 animate-spin mr-2" />
              ) : (
                <Play className="size-4 mr-2" />
              )}
              启动渲染
            </Button>
          )}
          {allDone && (
            <span className="text-xs text-green-600 flex items-center gap-1">
              <Check className="size-4" />
              全部完成
            </span>
          )}
        </div>
      </div>

      {/* Shot grid */}
      <div className="flex-1 overflow-y-auto p-4">
        <div className="grid grid-cols-2 xl:grid-cols-3 gap-4">
          {storyboard.map((sb, i) => {
            const sid = sb.storyboard_id ?? i
            const shot = sb.scene_change ? renderBoard[sid as number] : undefined
            const isExpanded = expandedId === sid

            if (!sb.scene_change) {
              // 非场景变化镜头：显示简洁的复用标记
              return (
                <div
                  key={i}
                  className="border border-dashed border-border/50 rounded-lg p-3 bg-accent/20"
                >
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span className="font-mono">#{sid}</span>
                    <span className="px-1.5 py-0.5 rounded bg-accent text-[10px]">复用镜头</span>
                  </div>
                  <div className="mt-2 text-xs text-muted-foreground line-clamp-2">
                    {sb.speaker ? `${sb.speaker}：` : ''}{sb.text}
                  </div>
                </div>
              )
            }

            return (
              <ShotCard
                key={i}
                runId={runId}
                shotId={sid as number}
                chapterId={chapterId}
                shot={shot}
                storyboardText={sb.text}
                storyboardSpeaker={sb.speaker}
                fallbackPrompt={sb.scene_prompt ?? ''}
                renderStarted={isStarted}
                expanded={isExpanded}
                onToggleExpand={() => setExpandedId(isExpanded ? null : sid)}
              />
            )
          })}
        </div>
      </div>
    </div>
  )
}

/** 单个镜头卡片：预览图 + 状态 + 提示词 + 操作。 */
function ShotCard({
  runId,
  shotId,
  chapterId,
  shot,
  storyboardText,
  storyboardSpeaker,
  fallbackPrompt,
  renderStarted,
  expanded,
  onToggleExpand,
}: {
  runId: string
  shotId: number
  chapterId: string
  shot?: RenderShot
  storyboardText?: string
  storyboardSpeaker?: string
  fallbackPrompt: string
  renderStarted: boolean
  expanded: boolean
  onToggleExpand: () => void
}) {
  const [prompt, setPrompt] = useState(shot?.prompt ?? fallbackPrompt)
  const [edited, setEdited] = useState(false)
  const [rerolling, setRerolling] = useState(false)
  const { upsertRenderShot } = useRunStore()

  useEffect(() => {
    if (!edited && shot?.prompt) setPrompt(shot.prompt)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shot?.prompt])

  const status = shot?.status ?? 'pending'

  const handleReroll = async () => {
    if (!renderStarted) return
    try {
      setRerolling(true)
      if (shot) {
        // 乐观更新：立即更新状态和提示词
        upsertRenderShot({ ...shot, status: 'rendering', prompt })
      }
      await api.rerollShot(runId, shotId, chapterId, edited ? prompt : undefined)
      setEdited(false)
    } catch (e) {
      console.error('重新抽卡失败', e)
    } finally {
      setRerolling(false)
    }
  }

  const handleSelect = async (candidatePath: string) => {
    try {
      await api.selectCandidate(runId, shotId, chapterId, candidatePath)
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

  const statusColors: Record<RenderShot['status'], string> = {
    pending: 'border-muted-foreground/30',
    rendering: 'border-blue-500 bg-blue-500/5',
    done: 'border-green-500 bg-green-500/5',
    error: 'border-destructive bg-destructive/5',
  }

  return (
    <div className={cn(
      'border rounded-lg overflow-hidden flex flex-col transition-all',
      statusColors[status],
      expanded ? 'col-span-2 xl:col-span-1' : ''
    )}>
      {/* Card header */}
      <div className="px-3 py-2 border-b border-border/50 flex items-center justify-between bg-background/50">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-medium">#{shotId}</span>
          <StatusDot status={status} />
          {shot?.workflow && (
            <span className="px-1.5 py-0.5 rounded bg-accent text-[10px] text-muted-foreground">
              {shot.workflow === 'qwen_edit' ? '参考图' : '文生图'}
            </span>
          )}
        </div>
        {status === 'done' && (
          <Check className="size-4 text-green-500" />
        )}
      </div>

      {/* Storyboard text */}
      {(storyboardSpeaker || storyboardText) && (
        <div className="px-3 py-2 text-xs text-muted-foreground bg-accent/30 border-b border-border/30 line-clamp-1">
          {storyboardSpeaker && <span className="font-medium">{storyboardSpeaker}：</span>}
          {storyboardText}
        </div>
      )}

      {/* Preview image */}
      <div className="relative bg-background aspect-video flex items-center justify-center">
        {shot?.selected_url ? (
          <img
            src={shot.selected_url}
            alt={`镜头 ${shotId}`}
            className="w-full h-full object-cover cursor-pointer"
            onClick={onToggleExpand}
          />
        ) : status === 'rendering' || rerolling ? (
          <div className="flex flex-col items-center gap-2 text-muted-foreground">
            <Loader2 className="size-8 animate-spin" />
            <span className="text-xs">生成中...</span>
          </div>
        ) : status === 'error' ? (
          <div className="flex flex-col items-center gap-2 text-destructive">
            <AlertCircle className="size-8" />
            <span className="text-xs">{shot?.error ?? '生成失败'}</span>
          </div>
        ) : renderStarted ? (
          <div className="text-xs text-muted-foreground">等待生成...</div>
        ) : (
          <div className="text-xs text-muted-foreground text-center px-4">
            点击顶部【启动渲染】<br />开始生成图片
          </div>
        )}

        {/* Expand toggle button */}
        {shot?.selected_url && (
          <button
            onClick={onToggleExpand}
            className="absolute bottom-2 right-2 bg-black/50 text-white rounded p-1 opacity-0 hover:opacity-100 transition-opacity"
            title={expanded ? '收起' : '展开查看候选图'}
          >
            {expanded ? <X className="size-4" /> : '展开'}
          </button>
        )}
      </div>

      {/* Expanded: Candidate thumbnails */}
      {expanded && shot && shot.candidates.length > 1 && (
        <div className="p-2 border-t border-border/30 bg-background/50">
          <div className="text-[10px] text-muted-foreground mb-1.5">候选图（点击切换）</div>
          <div className="flex flex-wrap gap-1.5">
            {shot.candidates.map((c) => (
              <button
                key={c.path}
                onClick={() => handleSelect(c.path)}
                className={cn(
                  'size-12 rounded border overflow-hidden transition-all',
                  c.path === shot.selected
                    ? 'border-primary ring-2 ring-ring'
                    : 'border-border opacity-70 hover:opacity-100'
                )}
              >
                <img src={c.url} alt="候选" className="size-full object-cover" />
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Prompt editor */}
      <div className="p-3 flex-1 flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="text-[10px] text-muted-foreground">提示词</span>
          {edited && (
            <span className="text-[10px] text-amber-600 flex items-center gap-1">
              <span className="size-1.5 rounded-full bg-amber-500" />
              已修改
            </span>
          )}
        </div>
        <textarea
          value={prompt}
          onChange={(e) => {
            setPrompt(e.target.value)
            setEdited(true)
          }}
          disabled={!renderStarted}
          className="w-full min-h-[60px] text-xs border border-input rounded p-2 resize-none bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50"
          placeholder="等待渲染启动..."
        />

        {/* Actions */}
        <div className="flex items-center justify-between mt-auto">
          {shot?.subjects && shot.subjects.length > 0 && (
            <span className="text-[10px] text-muted-foreground truncate max-w-[50%]">
              {shot.subjects.join('、')}
            </span>
          )}
          <div className="flex gap-1 ml-auto">
            {edited && (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs px-2"
                onClick={() => {
                  setPrompt(shot?.prompt ?? fallbackPrompt)
                  setEdited(false)
                }}
                disabled={status === 'rendering' || rerolling}
              >
                撤销
              </Button>
            )}
            <Button
              variant={edited ? 'default' : 'ghost'}
              size="sm"
              className="h-7 text-xs px-2"
              onClick={handleReroll}
              disabled={!renderStarted || status === 'rendering' || rerolling}
            >
              <RotateCcw className={cn('size-3 mr-1', rerolling && 'animate-spin')} />
              抽卡
            </Button>
          </div>
        </div>
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
    <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
      <span className={cn('size-2 rounded-full', m.color)} />
      {m.label}
    </span>
  )
}
