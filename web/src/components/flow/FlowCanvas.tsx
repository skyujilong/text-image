import {
  ReactFlow,
  Background,
  Controls,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useRunStore } from '@/store/runStore'
import { useGraphSchema } from '@/hooks/useGraphSchema'
import SubgraphNode from './SubgraphNode'
import InternalNode from './InternalNode'

const nodeTypes = {
  subgraph: SubgraphNode,
  internal: InternalNode,
}

export default function FlowCanvas() {
  const { drillPath, popDrill, runError, setRunError } = useRunStore()
  const currentSubgraph = drillPath[drillPath.length - 1] ?? null

  const { nodes, edges, isLoading } = useGraphSchema(currentSubgraph, drillPath)

  return (
    <div className="relative w-full h-full">
      {drillPath.length > 0 && (
        <div className="absolute top-3 left-3 z-10 flex items-center gap-2 bg-white rounded shadow px-3 py-1 text-sm">
          <button onClick={popDrill} className="text-blue-600 hover:underline">
            ← 返回
          </button>
          <span className="text-gray-400">/</span>
          <span>{currentSubgraph}</span>
        </div>
      )}
      {isLoading ? (
        <div className="w-full h-full flex items-center justify-center text-gray-400 text-sm">
          加载中...
        </div>
      ) : (
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
        >
          <Background />
          <Controls />
        </ReactFlow>
      )}
      {runError && (
        <div className="absolute bottom-0 left-0 right-0 z-20 bg-red-50 border-t border-red-200 px-4 py-2 text-sm text-red-700 flex items-start gap-2">
          <span className="shrink-0 font-semibold">错误：</span>
          <pre className="whitespace-pre-wrap break-all flex-1">{runError}</pre>
          <button
            className="shrink-0 text-red-400 hover:text-red-600"
            onClick={() => setRunError(null)}
          >
            ✕
          </button>
        </div>
      )}
    </div>
  )
}
