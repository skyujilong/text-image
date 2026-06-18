import { useState } from 'react'
import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetFooter,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'
import { cn } from '@/lib/utils'

interface VoiceCandidate {
  index: number
  seed: number
  label: string
  sample_path?: string
}

interface Props {
  runId: string
  candidates: VoiceCandidate[]
  open: boolean
  onClose: () => void
}

export default function VoiceCardDraw({ runId, candidates, open, onClose }: Props) {
  const [selected, setSelected] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const { setActiveInteraction } = useRunStore()

  const handleConfirm = async (resumeValue: number) => {
    setLoading(true)
    try {
      await api.resumeRun(runId, resumeValue)
      setActiveInteraction(null)
      onClose()
    } catch (e) {
      console.error('resume failed', e)
    } finally {
      setLoading(false)
    }
  }

  const handleRejectAll = () => handleConfirm(-1)

  return (
    <Sheet open={open} onOpenChange={(o: boolean) => !o && onClose()}>
      <SheetContent side="right" className="w-[440px] sm:max-w-[440px]">
        <SheetHeader>
          <SheetTitle>选择语音音色（voice_card_draw）</SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-3 py-4 overflow-y-auto max-h-[70vh]">
          {candidates.map((c) => (
            <div
              key={c.index}
              className={cn(
                'border-2 rounded p-3 cursor-pointer',
                selected === c.index ? 'border-blue-500 bg-blue-50' : 'border-gray-200 hover:border-gray-400'
              )}
              onClick={() => setSelected(c.index)}
            >
              <div className="font-medium text-sm">{c.label}</div>
              <div className="text-xs text-gray-400">seed: {c.seed}</div>
              {c.sample_path && (
                <audio
                  controls
                  src={`/api/files/${encodeURIComponent(c.sample_path)}`}
                  className="mt-2 w-full h-8"
                  onClick={(e) => e.stopPropagation()}
                />
              )}
            </div>
          ))}
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={handleRejectAll} disabled={loading}>
            全部拒绝（重抽）
          </Button>
          <Button
            onClick={() => selected !== null && handleConfirm(selected)}
            disabled={selected === null || loading}
          >
            {loading ? '提交中...' : '确认选择'}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
