import { useState } from 'react'
import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetFooter,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

interface PendingCharacter {
  name: string
  first_appearance: string
}

interface Props {
  runId: string
  pendingCharacters: PendingCharacter[]
  open: boolean
  onClose: () => void
}

type Decision = 'keep' | 'ignore'

export default function NewCharacterDecision({ runId, pendingCharacters, open, onClose }: Props) {
  const [decisions, setDecisions] = useState<Record<string, Decision>>(() =>
    Object.fromEntries(pendingCharacters.map((c) => [c.name, 'keep']))
  )
  const [loading, setLoading] = useState(false)
  const { setActiveInteraction } = useRunStore()

  const handleConfirm = async () => {
    setLoading(true)
    try {
      await api.resumeRun(runId, { decisions })
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
      <SheetContent side="right" className="w-[420px] sm:max-w-[420px]">
        <SheetHeader>
          <SheetTitle>新角色决策（detect_new_characters）</SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-3 py-4 overflow-y-auto max-h-[70vh]">
          {pendingCharacters.map((c) => (
            <div
              key={c.name}
              className="flex items-center justify-between border rounded px-3 py-2"
            >
              <div>
                <div className="font-medium text-sm">{c.name}</div>
                <div className="text-xs text-gray-400">首次出现：{c.first_appearance}</div>
              </div>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant={decisions[c.name] === 'keep' ? 'default' : 'outline'}
                  onClick={() => setDecisions((d) => ({ ...d, [c.name]: 'keep' }))}
                >
                  保留
                </Button>
                <Button
                  size="sm"
                  variant={decisions[c.name] === 'ignore' ? 'destructive' : 'outline'}
                  onClick={() => setDecisions((d) => ({ ...d, [c.name]: 'ignore' }))}
                >
                  忽略
                </Button>
              </div>
            </div>
          ))}
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={onClose} disabled={loading}>
            取消
          </Button>
          <Button onClick={handleConfirm} disabled={loading}>
            {loading ? '提交中...' : '确认'}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
