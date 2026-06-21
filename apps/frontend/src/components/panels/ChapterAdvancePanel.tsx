import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

interface Props {
  runId: string
  chapterId?: string
  plannedCount: number
}

/**
 * 章节推进面板：本章规划完成后选择方向。
 * resume "next" → 继续规划下一章；"render" → 进入批量渲染（GPU 批次）。
 * 由右侧常驻区渲染（body-only，无 Sheet 包装）。
 */
export default function ChapterAdvancePanel({ runId, chapterId, plannedCount }: Props) {
  const { setActiveInteraction } = useRunStore()

  const handle = async (choice: 'next' | 'render') => {
    try {
      await api.resumeRun(runId, choice)
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
          <p className="text-sm">
            当前已规划 <span className="font-bold text-blue-600">{plannedCount}</span> 章待渲染。
          </p>
          <p className="text-xs text-gray-400">
            继续规划下一章，或进入批量渲染批次（按小时租用 GPU，集中渲染已规划章节）。
          </p>
        </div>
      </div>

      <div className="flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2 px-6 pb-6 gap-2">
        <Button variant="outline" onClick={() => handle('next')}>
          继续规划下一章
        </Button>
        <Button onClick={() => handle('render')}>
          开始渲染批次
        </Button>
      </div>
    </div>
  )
}
