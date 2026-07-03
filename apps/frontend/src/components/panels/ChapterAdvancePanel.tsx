import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

interface Props {
  runId: string
  chapterId?: string
  plannedCount: number
}

/**
 * 章节推进面板：本章规划完成后继续规划下一章。
 * resume "render" → 提交本章批次到主图（渲染工作台可开渲）+ 继续规划下一章；
 * 全部章节规划完则整体结束。渲染由独立的渲染工作台驱动，与规划并行。
 * 由右侧常驻区渲染（body-only，无 Sheet 包装）。
 */
export default function ChapterAdvancePanel({ runId, chapterId, plannedCount }: Props) {
  const { setActiveInteraction, activeInteraction } = useRunStore()

  // resume "render"：把已规划批次刷回主图（渲染工作台立即可见可开渲）后继续规划下一章。
  const handleContinue = async () => {
    if (!activeInteraction) return
    try {
      await api.resumeRun(runId, activeInteraction.scope, activeInteraction.thread_id, 'render')
      setActiveInteraction(null)
    } catch (e) {
      console.error('resume failed', e)
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6">
        <h2 className="text-lg font-semibold text-foreground">章节推进（chapter_advance_decision{chapterId ? ` · ${chapterId}` : ''}）</h2>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        <div className="flex flex-col gap-3">
          <p className="text-sm text-foreground">
            当前已规划 <span className="font-semibold">{plannedCount}</span> 章待渲染。
          </p>
          <p className="text-xs text-muted-foreground">
            继续规划下一章；已规划章节会同步到渲染工作台，可随时前往开渲（规划与渲染并行）。
          </p>
        </div>
      </div>

      <div className="flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2 px-6 pb-6 gap-2">
        <Button onClick={handleContinue}>
          继续规划下一章
        </Button>
      </div>
    </div>
  )
}
