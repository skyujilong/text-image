import { useRunStore, type ActiveInteraction } from '@/store/runStore'
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
 * 后端 interrupt payload 的 type 字段 → 对应叶子节点名。
 * 用作兜底：后端 _resolve_interrupted 偶发把中间层子图节点（如 init_subgraph）
 * 当叶子返回、node 名不是叶子时，按 payload.type 仍能正确分发到对应面板。
 * type 值与后端各节点 interrupt() 传入的 {"type": ...} 严格对齐。
 */
const PAYLOAD_TYPE_TO_NODE: Record<string, string> = {
  initial_characters_review: 'review_initial_characters',
  tri_view_upload_batch: 'batch_upload_tri_view',
  chapter_review: 'review_chapter',
  chapter_advance: 'chapter_advance_decision',
  final_decision: 'final_decision',
  audio_config: 'configure_audio',
}

/**
 * 右侧常驻交互区：按 interrupt 叶子节点名（后端 _resolve_interrupted 解析）切换
 * 对应的输入 UI。node 名 + payload 由后端 interrupt() 传入，resume 值严格对齐后端
 * 节点校验。
 *
 * 设计要点：这是一块始终挂载的普通 <aside>，内容随 node 切换——不使用 Radix
 * Dialog/Sheet。此前用受控 Sheet 抽屉时，连续两个不同 node 的 interrupt 之间
 * 会出现「旧 Sheet(open=false) 卸载 + 新 Sheet(open=true) 挂载」同 tick 发生，
 * Radix 受控状态机错乱、对新 Sheet 触发 onOpenChange(false) 瞬间关掉抽屉，
 * 表现为「第二个及之后的审阅弹不出来」。常驻区域无 portal/无 open 状态/无挂载
 * 卸载时机问题，从根本上规避。
 *
 * node 名兜底：后端解析偶发返回中间层节点名（非叶子），此时按 payload.type
 * 映射回正确的叶子节点名再分发，避免「收到事件却不渲染内容」。
 *
 * resume 成功后节点 setActiveInteraction(null) → 本区切回占位态；下一个 interrupt
 * 到来时 Body 重新挂载，内部表单状态（textarea/form）自然重置。
 */
export default function InteractionDispatcher({ runId }: Props) {
  const activeInteraction = useRunStore((s) => s.activeInteraction)

  return (
    <aside className="w-[480px] shrink-0 border-l border-gray-200 bg-white flex flex-col h-full overflow-hidden">
      {activeInteraction ? (
        <InteractionBody key={activeInteraction.node} runId={runId} interaction={activeInteraction} />
      ) : (
        <EmptyState />
      )}
    </aside>
  )
}

/** 按 node 名分发到对应交互面板的 body。node 不匹配时按 payload.type 兜底。 */
function InteractionBody({ runId, interaction }: { runId: string; interaction: ActiveInteraction }) {
  const { node, payload } = interaction
  const p = (payload ?? {}) as Record<string, unknown>

  // node 名匹配不到叶子时，按 payload.type 兜底映射回正确节点名。
  const payloadType = typeof p.type === 'string' ? p.type : ''
  const resolvedNode = PAYLOAD_TYPE_TO_NODE[payloadType] ?? node

  switch (resolvedNode) {
    case 'review_chapter':
      return (
        <ChapterReviewPanel
          runId={runId}
          chapterId={p.chapter_id as string | undefined}
          script={(p.script as Record<string, unknown>[]) ?? []}
          storyboard={(p.storyboard as Record<string, unknown>[]) ?? []}
          newCharacters={(p.new_characters as Record<string, unknown>[]) ?? []}
        />
      )
    case 'review_initial_characters':
      return (
        <InitialCharactersReviewPanel
          runId={runId}
          characters={(p.characters as Record<string, unknown>[]) ?? []}
        />
      )
    case 'batch_upload_tri_view':
      return (
        <TriViewUploadPanel
          runId={runId}
          characters={(p.characters as Record<string, unknown>[]) ?? []}
        />
      )
    case 'chapter_advance_decision':
      return (
        <ChapterAdvancePanel
          runId={runId}
          chapterId={p.chapter_id as string | undefined}
          plannedCount={(p.planned_count as number) ?? 0}
        />
      )
    case 'final_decision':
      return (
        <FinalDecisionPanel
          runId={runId}
          exportedCount={(p.exported_count as number) ?? 0}
          remainingPending={(p.remaining_pending as number) ?? 0}
        />
      )
    case 'configure_audio':
      return (
        <AudioConfigPanel
          runId={runId}
          current={p.current as Record<string, unknown> | undefined}
        />
      )
    default:
      return (
        <div className="p-6 text-sm text-gray-400">
          未知交互节点：<code className="text-gray-600">{node}</code>
          {payloadType && <span>（type={payloadType}）</span>}
        </div>
      )
  }
}

/** 无待处理交互时的占位态。 */
function EmptyState() {
  return (
    <div className="flex-1 flex items-center justify-center text-sm text-gray-400 p-6 text-center">
      运行中，暂无待处理交互
    </div>
  )
}
