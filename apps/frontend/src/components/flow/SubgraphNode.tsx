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

export type SubgraphNodeData = Node<{ label: string; subgraphId: string; statusKey: string; sourceCount?: number; targetCount?: number; hasBackOut?: boolean; hasBackIn?: boolean }, 'subgraph'>
type SubgraphNodeProps = NodeProps<SubgraphNodeData>

function SubgraphNode({ data }: SubgraphNodeProps) {
  const { nodeStatuses, pushDrill } = useRunStore()
  const status = (nodeStatuses[data.statusKey] ?? 'pending') as NodeStatus

  // 中文名（前端映射）；后端 label 即英文 subgraph id。两者都展示：
  // 有中文映射时主显示中文、副显示英文 id；无映射时只显示英文 id，不重复。
  const zhLabel = getNodeLabel(data.subgraphId)
  const enLabel = data.subgraphId

  return (
    <div className="group relative">
      <div
        className={cn(
          'rounded-lg border-2 px-4 py-3 cursor-pointer min-w-[160px] text-center',
          STATUS_COLORS[status]
        )}
        // 只保留双击下钻：原先单击还会打开 inspect 面板，与双击触发时序冲突
        // （双击时单击先触发一次），已移除单击逻辑。
        onDoubleClick={() => pushDrill(data.subgraphId)}
      >
        {renderHandles(data.sourceCount ?? 0, data.targetCount ?? 0, data.hasBackOut ?? false, data.hasBackIn ?? false)}
        <div className="font-semibold text-sm">{zhLabel ?? enLabel}</div>
        {zhLabel && zhLabel !== enLabel && (
          <div className="text-[10px] text-gray-500 font-mono">{enLabel}</div>
        )}
        <div className="text-xs text-gray-500 mt-1">{status}</div>
        <div className="text-xs text-gray-400 mt-1">双击下钻</div>
      </div>
    </div>
  )
}

export default memo(SubgraphNode)
