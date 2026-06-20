import { useEffect, useMemo, useState } from 'react'
import dagre from '@dagrejs/dagre'
import { MarkerType, type Node, type Edge } from '@xyflow/react'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

const NODE_WIDTH_SUBGRAPH = 180
const NODE_HEIGHT_SUBGRAPH = 60
const NODE_WIDTH_INTERNAL = 160
const NODE_HEIGHT_INTERNAL = 48

// 前向边走「左入右出」；回边（循环）走「底部出、底部入」绕底部回环，
// 与前向边物理分离，避免重合并让方向清晰。
const FWD_SOURCE_PREFIX = 'source-' // 右侧前向出边 handle
const FWD_TARGET_PREFIX = 'target-' // 左侧前向入边 handle
const BACK_SOURCE = 'back-source' // 底部回边出 handle
const BACK_TARGET = 'back-target' // 底部回边入 handle

// 边的中间态：携带 isBack 标记，供最终派生样式时使用
interface RawEdge extends Edge {
  data: { isBack: boolean }
}

function applyDagreLayout(
  nodes: Node[],
  edges: Edge[],
): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'LR', nodesep: 40, ranksep: 60 })

  for (const node of nodes) {
    const w = node.type === 'subgraph' ? NODE_WIDTH_SUBGRAPH : NODE_WIDTH_INTERNAL
    const h = node.type === 'subgraph' ? NODE_HEIGHT_SUBGRAPH : NODE_HEIGHT_INTERNAL
    g.setNode(node.id, { width: w, height: h })
  }

  for (const edge of edges) {
    g.setEdge(edge.source, edge.target)
  }

  dagre.layout(g)

  const positionedNodes = nodes.map((node) => {
    const { x, y } = g.node(node.id)
    const w = node.type === 'subgraph' ? NODE_WIDTH_SUBGRAPH : NODE_WIDTH_INTERNAL
    const h = node.type === 'subgraph' ? NODE_HEIGHT_SUBGRAPH : NODE_HEIGHT_INTERNAL
    return {
      ...node,
      position: { x: x - w / 2, y: y - h / 2 },
    }
  })

  return { nodes: positionedNodes, edges }
}

/**
 * 给边分配 handle：
 * - 前向边：source 用右侧 source-i，target 用左侧 target-i（同节点多边垂直分散）。
 * - 回边：source 用底部 back-source，target 用底部 back-target（绕底部回环，与前向边分离）。
 * 同时统计每个节点的前向出/入边数及是否涉及回边，注入 data 供节点组件渲染对应 handle。
 */
function assignHandles(
  nodes: Node[],
  edges: RawEdge[],
): { nodes: Node[]; edges: Edge[] } {
  const fwdOut: Record<string, number> = {}
  const fwdIn: Record<string, number> = {}
  const backOut: Record<string, boolean> = {}
  const backIn: Record<string, boolean> = {}
  for (const e of edges) {
    if (e.data.isBack) {
      backOut[e.source] = true
      backIn[e.target] = true
    } else {
      fwdOut[e.source] = (fwdOut[e.source] ?? 0) + 1
      fwdIn[e.target] = (fwdIn[e.target] ?? 0) + 1
    }
  }

  const outIdx: Record<string, number> = {}
  const inIdx: Record<string, number> = {}
  const laidEdges: Edge[] = edges.map((e) => {
    if (e.data.isBack) {
      return { ...e, sourceHandle: BACK_SOURCE, targetHandle: BACK_TARGET }
    }
    const si = outIdx[e.source] ?? 0
    outIdx[e.source] = si + 1
    const ti = inIdx[e.target] ?? 0
    inIdx[e.target] = ti + 1
    return {
      ...e,
      sourceHandle: `${FWD_SOURCE_PREFIX}${si}`,
      targetHandle: `${FWD_TARGET_PREFIX}${ti}`,
    }
  })

  const laidNodes = nodes.map((node) => ({
    ...node,
    data: {
      ...node.data,
      sourceCount: fwdOut[node.id] ?? 0,
      targetCount: fwdIn[node.id] ?? 0,
      hasBackOut: !!backOut[node.id],
      hasBackIn: !!backIn[node.id],
    },
  }))

  return { nodes: laidNodes, edges: laidEdges }
}

export function useGraphSchema(
  subgraphId: string | null,
  drillPath: string[],
): { nodes: Node[]; edges: Edge[]; isLoading: boolean } {
  const [nodes, setNodes] = useState<Node[]>([])
  const [edges, setEdges] = useState<RawEdge[]>([])
  const [isLoading, setIsLoading] = useState(true)
  // 订阅节点状态，用于派生「指向运行中节点的边」的高亮/流动动画。
  // schema 本身的加载只在 subgraphId 变化时触发，状态变化不会重新请求。
  const nodeStatuses = useRunStore((s) => s.nodeStatuses)

  useEffect(() => {
    let stale = false
    setIsLoading(true)

    api.getGraphSchema(subgraphId ?? undefined).then((schema) => {
      if (stale) return
      const rawNodes: Node[] = schema.nodes.map((n) => {
        const statusKey = [...drillPath, n.id].join('/')
        if (n.type === 'subgraph') {
          return {
            id: n.id,
            type: 'subgraph',
            position: { x: 0, y: 0 },
            data: { label: n.label, subgraphId: n.id, statusKey },
          }
        }
        return {
          id: n.id,
          type: 'internal',
          position: { x: 0, y: 0 },
          data: { label: n.label, nodeId: n.id, statusKey },
        }
      })

      const rawEdges: RawEdge[] = schema.edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        label: e.conditional && e.label ? e.label : undefined,
        data: { isBack: e.is_back_edge },
      }))

      const { nodes: laid, edges: laidEdges } = applyDagreLayout(rawNodes, rawEdges)
      const { nodes: withDegree, edges: withHandles } = assignHandles(laid, laidEdges)
      setNodes(withDegree)
      setEdges(withHandles as RawEdge[])
      setIsLoading(false)
    }).catch(() => {
      if (!stale) setIsLoading(false)
    })

    return () => { stale = true }
  }, [subgraphId])

  // 派生最终边：统一 smoothstep 直角折线 + 箭头；回边橙色虚线走底部，前向活跃边蓝色流动。
  // 回边不叠加 animated（流动动画的 dasharray 会覆盖虚线），仅以加粗变色高亮活跃态。
  const styledEdges = useMemo(() => {
    const statusOf = (nodeId: string) => {
      const node = nodes.find((n) => n.id === nodeId)
      const sk = node?.data?.statusKey as string | undefined
      return sk ? nodeStatuses[sk] : undefined
    }
    return edges.map((e) => {
      const isBack = e.data.isBack
      const st = statusOf(e.target)
      const active = st === 'running' || st === 'waiting_human'
      const color = isBack ? '#f97316' : active ? '#2563eb' : '#94a3b8'
      return {
        ...e,
        type: 'smoothstep',
        animated: active && !isBack,
        markerEnd: { type: MarkerType.ArrowClosed, color, width: 18, height: 18 },
        style: {
          stroke: color,
          strokeWidth: active ? 2.5 : 1.5,
          strokeDasharray: isBack ? '6,4' : undefined,
        },
      }
    })
  }, [edges, nodes, nodeStatuses])

  return { nodes, edges: styledEdges, isLoading }
}
