import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetFooter,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

interface Props {
  runId: string
  chapterId?: string
  plannedCount: number
  open: boolean
  onClose: () => void
}

/**
 * 章节推进面板：本章规划完成后选择方向。
 * resume "next" → 继续规划下一章；"render" → 进入批量渲染（GPU 批次）。
 */
export default function ChapterAdvancePanel({
  runId, chapterId, plannedCount, open, onClose,
}: Props) {
  const { setActiveInteraction } = useRunStore()

  const handle = async (choice: 'next' | 'render') => {
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
          <SheetTitle>章节推进（chapter_advance_decision{chapterId ? ` · ${chapterId}` : ''}）</SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-3 py-4">
          <p className="text-sm">
            当前已规划 <span className="font-bold text-blue-600">{plannedCount}</span> 章待渲染。
          </p>
          <p className="text-xs text-gray-400">
            继续规划下一章，或进入批量渲染批次（按小时租用 GPU，集中渲染已规划章节）。
          </p>
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={() => handle('next')}>
            继续规划下一章
          </Button>
          <Button onClick={() => handle('render')}>
            开始渲染批次
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
