import { useRunStore } from '@/store/runStore'
import ChapterReviewPanel from './ChapterReviewPanel'
import InitialCharactersReviewPanel from './InitialCharactersReviewPanel'
import TriViewUploadPanel from './TriViewUploadPanel'
import ChapterAdvancePanel from './ChapterAdvancePanel'
import FinalDecisionPanel from './FinalDecisionPanel'
import VoiceParamsChoicePanel from './VoiceParamsChoicePanel'
import VoiceCardDraw from './VoiceCardDraw'
import VoiceParamsManual from './VoiceParamsManual'

interface Props {
  runId: string
}

/**
 * 按 interrupt 叶子节点名分发到对应交互面板。
 * node 名由后端 _resolve_interrupted_node 解析（叶子 interrupt 节点名），
 * payload 由后端 interrupt() 传入。resume 值严格对齐后端节点校验。
 */
export default function InteractionDispatcher({ runId }: Props) {
  const { activeInteraction, setActiveInteraction } = useRunStore()

  if (!activeInteraction) return null

  const { node, payload } = activeInteraction
  const p = (payload ?? {}) as Record<string, unknown>
  const onClose = () => setActiveInteraction(null)

  if (node === 'review_chapter') {
    return (
      <ChapterReviewPanel
        runId={runId}
        chapterId={p.chapter_id as string | undefined}
        script={(p.script as Record<string, unknown>[]) ?? []}
        storyboard={(p.storyboard as Record<string, unknown>[]) ?? []}
        newCharacters={(p.new_characters as Record<string, unknown>[]) ?? []}
        open
        onClose={onClose}
      />
    )
  }

  if (node === 'review_initial_characters') {
    return (
      <InitialCharactersReviewPanel
        runId={runId}
        characters={(p.characters as Record<string, unknown>[]) ?? []}
        open
        onClose={onClose}
      />
    )
  }

  if (node === 'upload_tri_view') {
    return (
      <TriViewUploadPanel
        runId={runId}
        character={(p.character as Record<string, unknown>) ?? {}}
        open
        onClose={onClose}
      />
    )
  }

  if (node === 'chapter_advance_decision') {
    return (
      <ChapterAdvancePanel
        runId={runId}
        chapterId={p.chapter_id as string | undefined}
        plannedCount={(p.planned_count as number) ?? 0}
        open
        onClose={onClose}
      />
    )
  }

  if (node === 'final_decision') {
    return (
      <FinalDecisionPanel
        runId={runId}
        exportedCount={(p.exported_count as number) ?? 0}
        remainingPending={(p.remaining_pending as number) ?? 0}
        open
        onClose={onClose}
      />
    )
  }

  if (node === 'voice_params_choice') {
    return (
      <VoiceParamsChoicePanel
        runId={runId}
        character={(p.character as Record<string, unknown>) ?? {}}
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

  if (node === 'voice_card_draw') {
    type VCand = Parameters<typeof VoiceCardDraw>[0]['candidates']
    return (
      <VoiceCardDraw
        runId={runId}
        character={(p.character as Record<string, unknown>) ?? undefined}
        candidates={(p.candidates as VCand) ?? []}
        open
        onClose={onClose}
      />
    )
  }

  return null
}
