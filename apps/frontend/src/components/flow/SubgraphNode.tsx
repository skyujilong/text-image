import { memo } from 'react'
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { useRunStore, type NodeStatus } from '@/store/runStore'
import { api } from '@/api/client'
import { cn } from '@/lib/utils'

const STATUS_COLORS: Record<NodeStatus, string> = {
  pending: 'border-gray-300 bg-gray-50',
  running: 'border-blue-400 bg-blue-50 animate-pulse',
  waiting_human: 'border-orange-400 bg-orange-50',
  done: 'border-green-400 bg-green-50',
  error: 'border-red-400 bg-red-50',
}

export type SubgraphNodeData = Node<{ label: string; subgraphId: string; statusKey: string }, 'subgraph'>
type SubgraphNodeProps = NodeProps<SubgraphNodeData>

function SubgraphNode({ data }: SubgraphNodeProps) {
  const { nodeStatuses, currentRunId, runs, pushDrill, resetNodeStatuses, upsertRun, setInspectingNode, incrementStreamGeneration, setRunError } = useRunStore()
  const status = (nodeStatuses[data.statusKey] ?? 'pending') as NodeStatus

  const handleRestartFrom = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!currentRunId) return
    try {
      setRunError(null) // 重新运行前先清空旧错误
      await api.restartFrom(currentRunId, data.statusKey)
      resetNodeStatuses()
      const run = runs[currentRunId]
      if (run) upsertRun({ ...run, status: 'running' })
      incrementStreamGeneration() // 触发 SSE 重新连接
    } catch (err) {
      console.error(err)
    }
  }

  const canRestart = (status === 'done' || status === 'error') && currentRunId

  return (
    <div className="group relative">
      {canRestart && (
        <button
          className={cn(
            'absolute -top-2 -right-2 z-10 text-xs w-5 h-5 flex items-center justify-center rounded-full bg-white border shadow-sm cursor-pointer',
            status === 'done' && 'opacity-0 group-hover:opacity-100 transition-opacity text-gray-500 hover:text-blue-600 border-gray-300',
            status === 'error' && 'opacity-100 text-red-600 border-red-300',
          )}
          title="从此节点重新运行"
          onClick={handleRestartFrom}
        >
          ↺
        </button>
      )}
      <div
        className={cn(
          'rounded-lg border-2 px-4 py-3 cursor-pointer min-w-[160px] text-center',
          STATUS_COLORS[status]
        )}
        onClick={(e) => {
          if (e.detail === 1 && (status === 'done' || status === 'error')) {
            setInspectingNode(data.statusKey)
          }
        }}
        onDoubleClick={() => pushDrill(data.subgraphId)}
      >
        <Handle type="target" position={Position.Left} />
        <div className="font-semibold text-sm">{data.label}</div>
        <div className="text-xs text-gray-500 mt-1">{status}</div>
        <div className="text-xs text-gray-400 mt-1">双击下钻</div>
        <Handle type="source" position={Position.Right} />
      </div>
    </div>
  )
}

export default memo(SubgraphNode)
