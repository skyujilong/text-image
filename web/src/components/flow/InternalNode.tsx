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

export type InternalNodeData = Node<{ label: string; nodeId: string; statusKey: string }, 'internal'>
type InternalNodeProps = NodeProps<InternalNodeData>

function InternalNode({ data }: InternalNodeProps) {
  const { nodeStatuses, currentRunId, runs, resetNodeStatuses, upsertRun, setInspectingNode } = useRunStore()
  const status = (nodeStatuses[data.statusKey] ?? 'pending') as NodeStatus

  const handleRestartFrom = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!currentRunId) return
    try {
      await api.restartFrom(currentRunId, data.statusKey)
      resetNodeStatuses()
      const run = runs[currentRunId]
      if (run) upsertRun({ ...run, status: 'running' })
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
        <Handle type="target" position={Position.Left} />
        <div className="font-medium text-xs">{data.label}</div>
        <div className="text-xs text-gray-400">{status}</div>
        <Handle type="source" position={Position.Right} />
      </div>
    </div>
  )
}

export default memo(InternalNode)
