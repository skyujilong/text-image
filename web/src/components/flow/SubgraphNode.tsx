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

export type SubgraphNodeData = Node<{ label: string; subgraphId: string }, 'subgraph'>
type SubgraphNodeProps = NodeProps<SubgraphNodeData>

function SubgraphNode({ data }: SubgraphNodeProps) {
  const { nodeStatuses, pushDrill } = useRunStore()
  const status = (nodeStatuses[data.subgraphId] ?? 'pending') as NodeStatus

  return (
    <div
      className={cn(
        'rounded-lg border-2 px-4 py-3 cursor-pointer min-w-[160px] text-center',
        STATUS_COLORS[status]
      )}
      onDoubleClick={() => pushDrill(data.subgraphId)}
    >
      <Handle type="target" position={Position.Left} />
      <div className="font-semibold text-sm">{data.label}</div>
      <div className="text-xs text-gray-500 mt-1">{status}</div>
      <div className="text-xs text-gray-400 mt-1">双击下钻</div>
      <Handle type="source" position={Position.Right} />
    </div>
  )
}

export default memo(SubgraphNode)
