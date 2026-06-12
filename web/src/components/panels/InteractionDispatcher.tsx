import { useRunStore } from '@/store/runStore'
import PortraitSelector from './PortraitSelector'
import FullbodySelector from './FullbodySelector'
import VoiceCardDraw from './VoiceCardDraw'
import VoiceParamsManual from './VoiceParamsManual'
import NewCharacterDecision from './NewCharacterDecision'

interface Props {
  runId: string
}

export default function InteractionDispatcher({ runId }: Props) {
  const { activeInteraction, setActiveInteraction } = useRunStore()

  if (!activeInteraction) return null

  const { node, payload } = activeInteraction
  const p = payload as Record<string, unknown>
  const onClose = () => setActiveInteraction(null)

  if (node === 'portrait_selector') {
    return (
      <PortraitSelector
        runId={runId}
        candidates={(p.candidates as string[]) ?? []}
        open
        onClose={onClose}
      />
    )
  }

  if (node === 'fullbody_selector') {
    return (
      <FullbodySelector
        runId={runId}
        candidates={(p.candidates as string[]) ?? []}
        open
        onClose={onClose}
      />
    )
  }

  if (node === 'voice_card_draw') {
    type VCand = Parameters<typeof VoiceCardDraw>[0]['candidates']
    return (
      <VoiceCardDraw
        runId={runId}
        candidates={(p.candidates as VCand) ?? []}
        open
        onClose={onClose}
      />
    )
  }

  if (node === 'voice_params_manual') {
    type VParams = Parameters<typeof VoiceParamsManual>[0]['currentParams']
    return (
      <VoiceParamsManual
        runId={runId}
        currentParams={p.current_params as VParams}
        open
        onClose={onClose}
      />
    )
  }

  if (node === 'detect_new_characters') {
    type PChars = Parameters<typeof NewCharacterDecision>[0]['pendingCharacters']
    return (
      <NewCharacterDecision
        runId={runId}
        pendingCharacters={(p.pending_characters as PChars) ?? []}
        open
        onClose={onClose}
      />
    )
  }

  return null
}
