import { memo } from 'react'
import { type Node, type NodeProps } from '@xyflow/react'
import { useRunStore, type NodeStatus } from '@/store/runStore'
import { cn } from '@/lib/utils'
import { renderHandles } from './multiHandles'
import { getNodeLabel } from '@/constants/nodeLabels'

const STATUS_COLORS: Record<NodeStatus, string> = {
  pending: 'border-gray-300 bg-gray-50',
  running: 'border-blue-400 bg-blue-50 animate-pulse',
  waiting_human: 'border-orange-400 bg-orange-50',
  done: 'border-green-400 bg-green-50',
  error: 'border-red-400 bg-red-50',
}

export type InternalNodeData = Node<{ label: string; nodeId: string; statusKey: string; sourceCount?: number; targetCount?: number; hasBackOut?: boolean; hasBackIn?: boolean }, 'internal'>
type InternalNodeProps = NodeProps<InternalNodeData>

function InternalNode({ data }: InternalNodeProps) {
  const { nodeStatuses, setInspectingNode } = useRunStore()
  const status = (nodeStatuses[data.statusKey] ?? 'pending') as NodeStatus

  // 中文名（前端映射）；后端 label 即英文 node id。两者都展示：
  // 有中文映射时主显示中文、副显示英文 id；无映射时只显示英文 id，不重复。
  const zhLabel = getNodeLabel(data.nodeId)
  const enLabel = data.nodeId

  return (
    <div className="group relative">
      <div
        className={cn(
          'rounded border-2 px-3 py-2 min-w-[140px] text-center',
          (status === 'done' || status === 'error') && 'cursor-pointer',
          STATUS_COLORS[status]
        )}
        onClick={() => {
          if (status === 'done' || status === 'error') {
            setInspectingNode(data.statusKey)
          }
        }}
      >
        {renderHandles(data.sourceCount ?? 0, data.targetCount ?? 0, data.hasBackOut ?? false, data.hasBackIn ?? false)}
        <div className="font-medium text-xs">{zhLabel ?? enLabel}</div>
        {zhLabel && zhLabel !== enLabel && (
          <div className="text-[10px] text-gray-400 font-mono">{enLabel}</div>
        )}
        <div className="text-xs text-gray-400">{status}</div>
      </div>
    </div>
  )
}

export default memo(InternalNode)
