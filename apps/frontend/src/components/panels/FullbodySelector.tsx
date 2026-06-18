import { useState } from 'react'
import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetFooter,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'
import { cn } from '@/lib/utils'

interface Props {
  runId: string
  candidates: string[]
  open: boolean
  onClose: () => void
}

export default function FullbodySelector({ runId, candidates, open, onClose }: Props) {
  const [selected, setSelected] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const { setActiveInteraction } = useRunStore()

  const handleConfirm = async () => {
    if (selected === null) return
    setLoading(true)
    try {
      await api.resumeRun(runId, selected)
      setActiveInteraction(null)
      onClose()
    } catch (e) {
      console.error('resume failed', e)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Sheet open={open} onOpenChange={(o: boolean) => !o && onClose()}>
      <SheetContent side="right" className="w-[560px] sm:max-w-[560px]">
        <SheetHeader>
          <SheetTitle>选择全身立绘（fullbody_selector）</SheetTitle>
        </SheetHeader>

        <div className="grid grid-cols-2 gap-3 py-4 overflow-y-auto max-h-[70vh]">
          {candidates.map((src, i) => (
            <div
              key={i}
              className={cn(
                'border-2 rounded cursor-pointer overflow-hidden',
                selected === i ? 'border-blue-500' : 'border-gray-200 hover:border-gray-400'
              )}
              onClick={() => setSelected(i)}
            >
              <img
                src={`/api/files/${encodeURIComponent(src)}`}
                alt={`候选立绘 ${i + 1}`}
                className="w-full h-auto object-cover"
                onError={(e) => {
                  (e.target as HTMLImageElement).src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg"/>'
                }}
              />
              <div className="text-xs text-center py-1 bg-gray-50">
                {selected === i ? '✓ 已选' : `图 ${i + 1}`}
              </div>
            </div>
          ))}
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={onClose} disabled={loading}>
            取消
          </Button>
          <Button onClick={handleConfirm} disabled={selected === null || loading}>
            {loading ? '提交中...' : '确认选择'}
          </Button>
          <Button variant="secondary" disabled title="后端尚未支持重新生成">
            重新生成
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
