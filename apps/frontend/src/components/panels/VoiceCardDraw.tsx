import { useState } from 'react'
import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetFooter,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

interface VoiceCandidate {
  index: number
  seed: number
  label: string
  sample_path?: string
}

interface Props {
  runId: string
  character?: { name?: string; appearance?: string }
  candidates: VoiceCandidate[]
  open: boolean
  onClose: () => void
}

/**
 * 音色抽卡面板。TTS 尚未接入时候选为空，仅支持“用默认音色”（resume 0）。
 * 后端 voice_card_draw 对 idx<0（拒绝）在 TTS 未接入时会抛错，故此处不提供拒绝入口，
 * 避免死循环或静默接受。TTS 接入后补回候选列表与拒绝/重抽逻辑。
 */
export default function VoiceCardDraw({ runId, character, candidates, open, onClose }: Props) {
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

  const hasCandidates = candidates.length > 0

  return (
    <Sheet open={open} onOpenChange={(o: boolean) => !o && onClose()}>
      <SheetContent side="right" className="w-[440px] sm:max-w-[440px]">
        <SheetHeader>
          <SheetTitle>选择语音音色（voice_card_draw{character?.name ? ` · ${character.name}` : ''}）</SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-3 py-4 overflow-y-auto max-h-[70vh]">
          {hasCandidates ? (
            candidates.map((c) => (
              <div
                key={c.index}
                className="border-2 rounded p-3 cursor-pointer hover:border-gray-400"
                onClick={() => handleConfirm(c.index)}
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
            ))
          ) : (
            <p className="text-sm text-gray-500">
              TTS 抽卡尚未接入，无候选音色。点击下方按钮使用默认音色继续。
            </p>
          )}
        </div>

        <SheetFooter>
          <Button onClick={() => handleConfirm(0)} disabled={loading}>
            {loading ? '提交中...' : '用默认音色'}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
