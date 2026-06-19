import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetFooter,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

interface Character {
  name?: string
  appearance?: string
}
interface Props {
  runId: string
  character: Character
  open: boolean
  onClose: () => void
}

/**
 * 音色参数方式选择面板。
 * resume "manual" → voice_params_manual 手动填写；"draw" → voice_card_draw 抽卡。
 */
export default function VoiceParamsChoicePanel({ runId, character, open, onClose }: Props) {
  const { setActiveInteraction } = useRunStore()

  const handle = async (route: 'manual' | 'draw') => {
    try {
      await api.resumeRun(runId, route)
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
          <SheetTitle>音色参数方式（voice_params_choice · {character.name ?? '未命名'}）</SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-3 py-4">
          <p className="text-sm text-gray-500">
            为角色 <span className="font-medium">{character.name}</span> 选择音色参数设定方式。
          </p>
          <p className="text-xs text-gray-400">
            手动填写：直接指定语速/音调等参数。抽卡：用默认音色（TTS 抽卡尚未接入）。
          </p>
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={() => handle('draw')}>
            用默认音色（抽卡）
          </Button>
          <Button onClick={() => handle('manual')}>
            手动填写
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
