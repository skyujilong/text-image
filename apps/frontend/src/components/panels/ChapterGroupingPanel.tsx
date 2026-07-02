import { useState } from 'react'
import { Layers } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

interface Props {
  runId: string
  chapterCount?: number
  defaultGroupSize?: number
  maxGroupSize?: number
}

/**
 * 章节合并设置面板：在剧本化前选择「合并几个连续章节为一组」。
 * 全局固定粒度 N（1..maxGroupSize，默认 defaultGroupSize=1）。
 * resume 值 {group_size: N}（后端校验 1..5）；成功后 setActiveInteraction(null)。
 * 由右侧常驻区渲染（body-only，无 Sheet 包装），结构对齐 ChapterAdvancePanel。
 */
export default function ChapterGroupingPanel({
  runId,
  chapterCount,
  defaultGroupSize = 1,
  maxGroupSize = 5,
}: Props) {
  const { setActiveInteraction, activeInteraction } = useRunStore()
  const [groupSize, setGroupSize] = useState(defaultGroupSize)
  const [submitting, setSubmitting] = useState(false)

  const options = Array.from({ length: maxGroupSize }, (_, i) => i + 1)
  const groupCount =
    chapterCount != null ? Math.ceil(chapterCount / groupSize) : null

  const handleSubmit = async () => {
    if (!activeInteraction || submitting) return
    setSubmitting(true)
    try {
      await api.resumeRun(runId, activeInteraction.scope, activeInteraction.thread_id, {
        group_size: groupSize,
      })
      setActiveInteraction(null)
    } catch (e) {
      console.error('resume failed', e)
      setSubmitting(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6">
        <h2 className="text-lg font-semibold text-foreground">章节合并设置</h2>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        <div className="flex flex-col gap-4">
          <p className="text-sm text-muted-foreground">
            选择将连续几个章节合并为一组做剧本化。默认 1（单章，保持现状），末组不足自成一组。
          </p>

          <div className="flex flex-col gap-2">
            <span className="text-sm font-medium text-foreground">合并粒度</span>
            <div className="flex flex-wrap gap-2">
              {options.map((n) => (
                <Button
                  key={n}
                  variant={n === groupSize ? 'default' : 'outline'}
                  size="icon"
                  aria-pressed={n === groupSize}
                  onClick={() => setGroupSize(n)}
                  className={cn(n === groupSize && 'ring-2 ring-ring/40')}
                >
                  {n}
                </Button>
              ))}
            </div>
          </div>

          {groupCount != null && (
            <p className="text-sm text-muted-foreground">
              共 {chapterCount} 章 → 约 {groupCount} 组（每组最多 {groupSize} 章）
            </p>
          )}
        </div>
      </div>

      <div className="flex flex-col-reverse sm:flex-row sm:justify-end px-6 pb-6 gap-2">
        <Button variant="default" onClick={handleSubmit} disabled={submitting}>
          <Layers className="size-4" />
          确认粒度
        </Button>
      </div>
    </div>
  )
}
