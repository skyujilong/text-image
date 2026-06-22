import { useEffect, useState } from 'react'
import { RotateCcw, Check, Loader2, AlertCircle } from 'lucide-react'
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
  chapterId?: string
  storyboard: StoryboardShot[]
}

/**
 * 图片渲染（抽卡）面板：渲染期间常驻，图随 SSE render_image 事件逐张冒出。
 *
 * 交互：每个换图点展示「可编辑提示词 + 当前选定图 + 候选切换 + 重新抽卡」。
 * 「完成渲染」按钮仅在所有换图点都已生成且已选定终图（无空帧）时可点——
 * 与后端 resume 兜底校验双重把关，防带空帧跑进 audio/timeline。
 *
 * 顺序保证：以 storyboard 数组（含非换图点）为序展示，换图点用 storyboard_id 去
 * renderBoard 查图；非换图点标注「复用上一镜头画面」，不单独出图。
 */
export default function ImageRenderPanel({ runId, chapterId, storyboard }: Props) {
  const { setActiveInteraction, renderBoard, setRenderBoard } = useRunStore()
  const [submitting, setSubmitting] = useState(false)

  // 挂载时全量拉取看板（恢复刷新前已生成的图；SSE 后续增量更新）
  useEffect(() => {
    api.getRenderState(runId)
      .then((board) => setRenderBoard(board.shots))
      .catch((e) => console.warn('[render] 拉取看板失败', e))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])

  // 换图点 storyboard_id 集合（决定哪些镜头要出图）
  const changeShotIds = new Set(
    storyboard.filter((s) => s.scene_change).map((s) => s.storyboard_id)
  )
  const shots = Object.values(renderBoard)
  // 完成校验：所有换图点都已 done 且有选定图。无任何 shot（看板未初始化）视为未完成。
  const allDone =
    changeShotIds.size > 0 &&
    [...changeShotIds].every((sid) => {
      const shot = renderBoard[sid as number]
      return shot && shot.status === 'done' && shot.selected
    })
  const pendingCount = [...changeShotIds].filter((sid) => {
    const shot = renderBoard[sid as number]
    return !shot || shot.status !== 'done' || !shot.selected
  }).length

  const handleFinish = async () => {
    if (!allDone || submitting) return
    setSubmitting(true)
    try {
      await api.resumeRun(runId, { decision: 'done' })
      setActiveInteraction(null)
    } catch (e) {
      console.error('完成渲染失败', e)
      setSubmitting(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6">
        <h2 className="text-lg font-semibold text-foreground">
          图片渲染{chapterId ? ` · ${chapterId}` : ''}
        </h2>
        <p className="text-xs text-muted-foreground mt-1">
          图片逐张生成，可调整提示词重新抽卡。全部完成后方可结束渲染。
        </p>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 flex flex-col gap-3">
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
            />
          )
        })}
        {shots.length === 0 && (
          <p className="text-sm text-muted-foreground">渲染队列启动中，图片即将逐张出现…</p>
        )}
      </div>

      <div className="flex flex-col gap-2 px-6 pb-6">
        {!allDone && (
          <p className="text-xs text-muted-foreground text-right">
            还有 {pendingCount} 个镜头未完成，不能结束渲染
          </p>
        )}
        <div className="flex justify-end">
          <Button onClick={handleFinish} disabled={!allDone || submitting}>
            {submitting ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
            完成渲染
          </Button>
        </div>
      </div>
    </div>
  )
}

/** 单个换图点卡片：提示词（可编辑）+ 选定图 + 候选切换 + 重新抽卡。 */
function ShotCard({
  runId,
  storyboardId,
  text,
  speaker,
  fallbackPrompt,
  shot,
}: {
  runId: string
  storyboardId: number
  text?: string
  speaker?: string
  fallbackPrompt: string
  shot?: RenderShot
}) {
  // 提示词初值取看板（生成时用的 prompt），看板未到则用分镜的 scene_prompt
  const [prompt, setPrompt] = useState(shot?.prompt ?? fallbackPrompt)
  const [edited, setEdited] = useState(false)
  const { upsertRenderShot } = useRunStore()

  // 看板首次到达时同步提示词（用户未编辑过才覆盖，避免打断输入）
  useEffect(() => {
    if (!edited && shot?.prompt) setPrompt(shot.prompt)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shot?.prompt])

  const status = shot?.status ?? 'pending'
  const rendering = status === 'rendering' || status === 'pending'

  const handleReroll = async () => {
    try {
      // 乐观置为 rendering，反馈即时（SSE done 事件会覆盖）
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
    <div className="border border-border rounded p-3 bg-accent/40 flex flex-col gap-2">
      <div className="flex items-center gap-2 text-xs">
        <span className="font-mono text-muted-foreground">{storyboardId}</span>
        {shot?.workflow && (
          <span className="px-1 rounded bg-accent text-muted-foreground">
            {shot.workflow === 'qwen_edit' ? '参考图生图' : '文生图'}
          </span>
        )}
        {shot?.subjects && shot.subjects.length > 0 && (
          <span className="text-muted-foreground">主体：{shot.subjects.join('、')}</span>
        )}
        <StatusDot status={status} />
      </div>

      {(speaker || text) && (
        <div className="text-xs text-foreground">{speaker ?? ''}：{text}</div>
      )}

      {/* 选定图预览 */}
      <div className="flex items-center justify-center bg-background rounded border border-border min-h-[160px]">
        {shot?.selected_url ? (
          <img
            src={shot.selected_url}
            alt={`shot ${storyboardId}`}
            className="max-h-64 rounded object-contain"
          />
        ) : rendering ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground py-8">
            <Loader2 className="size-4 animate-spin" /> 生成中…
          </div>
        ) : status === 'error' ? (
          <div className="flex items-center gap-2 text-xs text-destructive py-8">
            <AlertCircle className="size-4" /> {shot?.error ?? '生成失败'}
          </div>
        ) : (
          <div className="text-xs text-muted-foreground py-8">等待生成</div>
        )}
      </div>

      {/* 候选缩略图切换（多于 1 张时显示） */}
      {shot && shot.candidates.length > 1 && (
        <div className="flex flex-wrap gap-2">
          {shot.candidates.map((c) => (
            <button
              key={c.path}
              onClick={() => handleSelect(c.path)}
              className={cn(
                'size-14 rounded border overflow-hidden transition',
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

      {/* 可编辑提示词 */}
      <textarea
        value={prompt}
        onChange={(e) => {
          setPrompt(e.target.value)
          setEdited(true)
        }}
        className="w-full min-h-[60px] text-xs border border-input rounded p-2 resize-y bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
        placeholder="画面提示词"
      />

      <div className="flex justify-end">
        <Button variant="ghost" size="sm" onClick={handleReroll} disabled={rendering}>
          <RotateCcw className={cn('size-4', rendering && 'animate-spin')} />
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
