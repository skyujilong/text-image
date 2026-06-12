import { memo } from 'react'
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { useRunStore, type NodeStatus } from '@/store/runStore'
import { cn } from '@/lib/utils'

const STATUS_COLORS: Record<NodeStatus, string> = {
  pending: 'border-gray-300 bg-gray-50',
  running: 'border-blue-400 bg-blue-50 animate-pulse',
  waiting_human: 'border-orange-400 bg-orange-50',
  done: 'border-green-400 bg-green-50',
  error: 'border-red-400 bg-red-50',
}

export type InternalNodeData = Node<{ label: string; nodeId: string }, 'internal'>
type InternalNodeProps = NodeProps<InternalNodeData>

function InternalNode({ data }: InternalNodeProps) {
  const { nodeStatuses } = useRunStore()
  const status = (nodeStatuses[data.nodeId] ?? 'pending') as NodeStatus

  return (
    <div
      className={cn(
        'rounded border-2 px-3 py-2 min-w-[140px] text-center',
        STATUS_COLORS[status]
      )}
    >
      <Handle type="target" position={Position.Left} />
      <div className="font-medium text-xs">{data.label}</div>
      <div className="text-xs text-gray-400">{status}</div>
      <Handle type="source" position={Position.Right} />
    </div>
  )
}

export default memo(InternalNode)
