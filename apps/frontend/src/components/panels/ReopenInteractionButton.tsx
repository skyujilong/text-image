import { useRunStore } from '@/store/runStore'

// interrupt 叶子节点 → 中文标签（与 InteractionDispatcher 分发的 node 名一一对应）。
const INTERACTION_LABELS: Record<string, string> = {
  review_chapter: '章节审阅',
  review_initial_characters: '初始角色审核',
  batch_upload_tri_view: '批量上传三视图',
  chapter_advance_decision: '章节推进',
  final_decision: '终局决策',
  configure_audio: '配置音色',
}

/**
 * 重新打开交互抽屉的常驻入口。
 * 仅在「有待处理交互（activeInteraction 非空）但抽屉被用户关掉（interactionVisible=false）」时显示，
 * 避免用户关掉审阅抽屉后无法重新打开、无法 resume 导致流程卡死。
 */
export default function ReopenInteractionButton() {
  const { activeInteraction, interactionVisible, setInteractionVisible } = useRunStore()
  if (!activeInteraction || interactionVisible) return null
  const label = INTERACTION_LABELS[activeInteraction.node] ?? activeInteraction.node
  return (
    <button
      onClick={() => setInteractionVisible(true)}
      className="rounded shadow px-3 py-1 text-sm bg-amber-500 text-white hover:bg-amber-600"
      title="点击重新打开审阅面板"
    >
      待审阅：{label}
    </button>
  )
}
