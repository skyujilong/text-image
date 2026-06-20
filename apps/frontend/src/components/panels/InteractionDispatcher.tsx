import { useRunStore } from '@/store/runStore'
import ChapterReviewPanel from './ChapterReviewPanel'
import InitialCharactersReviewPanel from './InitialCharactersReviewPanel'
import TriViewUploadPanel from './TriViewUploadPanel'
import ChapterAdvancePanel from './ChapterAdvancePanel'
import FinalDecisionPanel from './FinalDecisionPanel'
import AudioConfigPanel from './AudioConfigPanel'

interface Props {
  runId: string
}

/**
 * 按 interrupt 叶子节点名分发到对应交互面板。
 * node 名由后端 _resolve_interrupted 解析（叶子 interrupt 节点名），
 * payload 由后端 interrupt() 传入。resume 值严格对齐后端节点校验。
 */
export default function InteractionDispatcher({ runId }: Props) {
  const { activeInteraction, interactionVisible, setInteractionVisible } = useRunStore()

  if (!activeInteraction) return null

  const { node, payload } = activeInteraction
  const p = (payload ?? {}) as Record<string, unknown>
  // 关闭抽屉只隐藏，不清空 activeInteraction，避免用户关掉后无法重新打开 / 无法 resume。
  const onClose = () => setInteractionVisible(false)

  if (node === 'review_chapter') {
    return (
      <ChapterReviewPanel
        runId={runId}
        chapterId={p.chapter_id as string | undefined}
        script={(p.script as Record<string, unknown>[]) ?? []}
        storyboard={(p.storyboard as Record<string, unknown>[]) ?? []}
        newCharacters={(p.new_characters as Record<string, unknown>[]) ?? []}
        open={interactionVisible}
        onClose={onClose}
      />
    )
  }

  if (node === 'review_initial_characters') {
    return (
      <InitialCharactersReviewPanel
        runId={runId}
        characters={(p.characters as Record<string, unknown>[]) ?? []}
        open={interactionVisible}
        onClose={onClose}
      />
    )
  }

  if (node === 'batch_upload_tri_view') {
    return (
      <TriViewUploadPanel
        runId={runId}
        characters={(p.characters as Record<string, unknown>[]) ?? []}
        open={interactionVisible}
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
        open={interactionVisible}
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
        open={interactionVisible}
        onClose={onClose}
      />
    )
  }

  if (node === 'configure_audio') {
    return (
      <AudioConfigPanel
        runId={runId}
        current={p.current as Record<string, unknown> | undefined}
        open={interactionVisible}
        onClose={onClose}
      />
    )
  }

  return null
}
