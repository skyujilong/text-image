import { useEffect, useState } from 'react'
import dagre from '@dagrejs/dagre'
import type { Node, Edge } from '@xyflow/react'
import { api } from '@/api/client'

const NODE_WIDTH_SUBGRAPH = 180
const NODE_HEIGHT_SUBGRAPH = 60
const NODE_WIDTH_INTERNAL = 160
const NODE_HEIGHT_INTERNAL = 48

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

export function useGraphSchema(
  subgraphId: string | null,
  drillPath: string[],
): { nodes: Node[]; edges: Edge[]; isLoading: boolean } {
  const [nodes, setNodes] = useState<Node[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [isLoading, setIsLoading] = useState(true)

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

      const rawEdges: Edge[] = schema.edges.map((e) => {
        const edgeStyle = e.is_back_edge
          ? { style: { stroke: '#f97316', strokeDasharray: '5,4' } }
          : {}
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          label: e.conditional && e.label ? e.label : undefined,
          ...edgeStyle,
        }
      })

      const { nodes: laid, edges: laidEdges } = applyDagreLayout(rawNodes, rawEdges)
      setNodes(laid)
      setEdges(laidEdges)
      setIsLoading(false)
    }).catch(() => {
      if (!stale) setIsLoading(false)
    })

    return () => { stale = true }
  }, [subgraphId])

  return { nodes, edges, isLoading }
}
