import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

interface Props {
  runId: string
  exportedCount: number
  remainingPending: number
}

/**
 * 最终决策面板：渲染批次导出后选择是否完结。
 * resume "done" → END；"continue" → 回 load_chapter 继续规划下一批（支持规划 N 章→渲染→再规划的交错）。
 * 由右侧常驻区渲染（body-only，无 Sheet 包装）。
 */
export default function FinalDecisionPanel({
  runId, exportedCount, remainingPending,
}: Props) {
  const { setActiveInteraction, activeInteraction } = useRunStore()

  const handle = async (choice: 'done' | 'continue') => {
    if (!activeInteraction) return
    try {
      await api.resumeRun(runId, activeInteraction.scope, activeInteraction.thread_id, choice)
      setActiveInteraction(null)
    } catch (e) {
      console.error('resume failed', e)
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6">
        <h2 className="text-lg font-semibold text-foreground">最终决策（final_decision）</h2>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 flex flex-col gap-3">
        <p className="text-sm">
          已导出 <span className="font-bold text-green-600">{exportedCount}</span> 章，
          剩余 <span className="font-bold text-orange-600">{remainingPending}</span> 章待规划。
        </p>
        <p className="text-xs text-gray-400">
          全部完结结束流程；继续规划则回到章节加载（支持交错：再规划几章后进入下一渲染批次）。
        </p>
      </div>

      <div className="flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2 px-6 pb-6 gap-2">
        <Button variant="outline" onClick={() => handle('continue')} disabled={remainingPending === 0}>
          继续规划{remainingPending === 0 ? '（无待规划章）' : ''}
        </Button>
        <Button onClick={() => handle('done')}>
          全部完结
        </Button>
      </div>
    </div>
  )
}
