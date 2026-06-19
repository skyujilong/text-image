import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetFooter,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

interface Props {
  runId: string
  exportedCount: number
  remainingPending: number
  open: boolean
  onClose: () => void
}

/**
 * 最终决策面板：渲染批次导出后选择是否完结。
 * resume "done" → END；"continue" → 回 load_chapter 继续规划下一批（支持规划 N 章→渲染→再规划的交错）。
 */
export default function FinalDecisionPanel({
  runId, exportedCount, remainingPending, open, onClose,
}: Props) {
  const { setActiveInteraction } = useRunStore()

  const handle = async (choice: 'done' | 'continue') => {
    try {
      await api.resumeRun(runId, choice)
      setActiveInteraction(null)
      onClose()
    } catch (e) {
      console.error('resume failed', e)
    }
  }

  return (
    <Sheet open={open} onOpenChange={(o: boolean) => !o && onClose()}>
      <SheetContent side="right" className="w-[440px] sm:max-w-[440px]">
        <SheetHeader>
          <SheetTitle>最终决策（final_decision）</SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-3 py-4">
          <p className="text-sm">
            已导出 <span className="font-bold text-green-600">{exportedCount}</span> 章，
            剩余 <span className="font-bold text-orange-600">{remainingPending}</span> 章待规划。
          </p>
          <p className="text-xs text-gray-400">
            全部完结结束流程；继续规划则回到章节加载（支持交错：再规划几章后进入下一渲染批次）。
          </p>
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={() => handle('continue')} disabled={remainingPending === 0}>
            继续规划{remainingPending === 0 ? '（无待规划章）' : ''}
          </Button>
          <Button onClick={() => handle('done')}>
            全部完结
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
