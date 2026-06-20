import { useEffect } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  ReactFlowProvider,
  useReactFlow,
  useStore,
  type Node,
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

// 与 useGraphSchema 中 dagre 用的节点尺寸保持一致，用于可见性判断与居中定位
const NODE_WIDTH_SUBGRAPH = 180
const NODE_HEIGHT_SUBGRAPH = 60
const NODE_WIDTH_INTERNAL = 160
const NODE_HEIGHT_INTERNAL = 48

function nodeSize(type?: string) {
  return type === 'subgraph'
    ? { w: NODE_WIDTH_SUBGRAPH, h: NODE_HEIGHT_SUBGRAPH }
    : { w: NODE_WIDTH_INTERNAL, h: NODE_HEIGHT_INTERNAL }
}

/**
 * 在当前可见层级中选出需要被定位的活跃节点。
 * 优先级：waiting_human > running；同状态取 internal（叶子）优先于 subgraph（更具体）。
 * 后端会对祖先子图节点传播同状态，因此需用 internal 优先来命中真正运行的叶子节点。
 */
function pickActiveNode(
  nodes: Node[],
  nodeStatuses: Record<string, string>,
): Node | null {
  let best: { node: Node; score: number } | null = null
  for (const n of nodes) {
    const sk = n.data?.statusKey as string | undefined
    const st = sk ? nodeStatuses[sk] : undefined
    if (st !== 'running' && st !== 'waiting_human') continue
    let score = st === 'waiting_human' ? 2 : 1
    if (n.type === 'internal') score += 0.5
    if (!best || score > best.score) best = { node: n, score }
  }
  return best?.node ?? null
}

/**
 * 自动定位：当活跃节点不在当前视口可见区时，平滑平移使其居中（保持当前缩放）。
 * 节点已在可见区则不动，避免打断用户手动平移/缩放。
 */
function useAutoCenter(nodes: Node[]) {
  const { getNode, getViewport, setCenter } = useReactFlow()
  const width = useStore((s) => s.width)
  const height = useStore((s) => s.height)
  const nodeStatuses = useRunStore((s) => s.nodeStatuses)

  useEffect(() => {
    const active = pickActiveNode(nodes, nodeStatuses)
    if (!active) return
    const node = getNode(active.id) ?? active
    const { w, h } = nodeSize(node.type)
    const vp = getViewport()
    // 节点在屏幕坐标系下的包围盒
    const screenX = node.position.x * vp.zoom + vp.x
    const screenY = node.position.y * vp.zoom + vp.y
    const sw = w * vp.zoom
    const sh = h * vp.zoom
    const inViewport =
      width > 0 &&
      height > 0 &&
      screenX >= 0 &&
      screenY >= 0 &&
      screenX + sw <= width &&
      screenY + sh <= height
    if (inViewport) return
    const cx = node.position.x + w / 2
    const cy = node.position.y + h / 2
    setCenter(cx, cy, { zoom: vp.zoom, duration: 400 })
  }, [nodes, nodeStatuses, getNode, getViewport, setCenter, width, height])
}

function FlowCanvasInner() {
  const { drillPath, popDrill, runError, setRunError } = useRunStore()
  const currentSubgraph = drillPath[drillPath.length - 1] ?? null

  const { nodes, edges, isLoading } = useGraphSchema(currentSubgraph, drillPath)
  useAutoCenter(nodes)

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

export default function FlowCanvas() {
  return (
    <ReactFlowProvider>
      <FlowCanvasInner />
    </ReactFlowProvider>
  )
}
