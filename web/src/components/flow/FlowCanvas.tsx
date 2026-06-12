import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useRunStore } from '@/store/runStore'
import SubgraphNode from './SubgraphNode'
import InternalNode from './InternalNode'

const nodeTypes = {
  subgraph: SubgraphNode,
  internal: InternalNode,
}

const TOP_NODES: Node[] = [
  {
    id: 'init_subgraph',
    type: 'subgraph',
    position: { x: 100, y: 150 },
    data: { label: '角色初始化', subgraphId: 'init_subgraph' },
  },
  {
    id: 'chapter_loop_subgraph',
    type: 'subgraph',
    position: { x: 380, y: 150 },
    data: { label: '章节处理循环', subgraphId: 'chapter_loop_subgraph' },
  },
]

const TOP_EDGES: Edge[] = [
  { id: 'e1', source: 'init_subgraph', target: 'chapter_loop_subgraph' },
]

const CHAPTER_INTERNAL_NODES: Node[] = [
  { id: 'load_chapter', type: 'internal', position: { x: 50, y: 50 }, data: { label: 'load_chapter', nodeId: 'load_chapter' } },
  { id: 'adapt_script', type: 'internal', position: { x: 250, y: 50 }, data: { label: 'adapt_script', nodeId: 'adapt_script' } },
  { id: 'review_script_llm', type: 'internal', position: { x: 450, y: 50 }, data: { label: 'review_script_llm', nodeId: 'review_script_llm' } },
  { id: 'review_script_human', type: 'internal', position: { x: 650, y: 50 }, data: { label: 'review_script_human', nodeId: 'review_script_human' } },
  { id: 'detect_new_characters', type: 'internal', position: { x: 850, y: 50 }, data: { label: 'detect_new_characters', nodeId: 'detect_new_characters' } },
  { id: 'character_setup_subgraph', type: 'internal', position: { x: 850, y: 180 }, data: { label: 'character_setup_subgraph', nodeId: 'character_setup_subgraph' } },
  { id: 'generate_storyboard', type: 'internal', position: { x: 1050, y: 50 }, data: { label: 'generate_storyboard', nodeId: 'generate_storyboard' } },
  { id: 'generate_images', type: 'internal', position: { x: 1250, y: 50 }, data: { label: 'generate_images', nodeId: 'generate_images' } },
  { id: 'synthesize_audio', type: 'internal', position: { x: 1250, y: 180 }, data: { label: 'synthesize_audio', nodeId: 'synthesize_audio' } },
  { id: 'build_timeline', type: 'internal', position: { x: 1450, y: 50 }, data: { label: 'build_timeline', nodeId: 'build_timeline' } },
  { id: 'human_export_decision', type: 'internal', position: { x: 1650, y: 50 }, data: { label: 'human_export_decision', nodeId: 'human_export_decision' } },
]

const CHAPTER_INTERNAL_EDGES: Edge[] = [
  { id: 'ce1', source: 'load_chapter', target: 'adapt_script' },
  { id: 'ce2', source: 'adapt_script', target: 'review_script_llm' },
  { id: 'ce3', source: 'review_script_llm', target: 'review_script_human' },
  { id: 'ce4', source: 'review_script_human', target: 'detect_new_characters' },
  { id: 'ce5', source: 'detect_new_characters', target: 'character_setup_subgraph' },
  { id: 'ce6', source: 'detect_new_characters', target: 'generate_storyboard' },
  { id: 'ce7', source: 'character_setup_subgraph', target: 'generate_storyboard' },
  { id: 'ce8', source: 'generate_storyboard', target: 'synthesize_audio' },
  { id: 'ce9', source: 'synthesize_audio', target: 'generate_images' },
  { id: 'ce10', source: 'generate_images', target: 'build_timeline' },
  { id: 'ce11', source: 'build_timeline', target: 'human_export_decision' },
]

const SETUP_INTERNAL_NODES: Node[] = [
  { id: 'setup_dispatcher', type: 'internal', position: { x: 50, y: 100 }, data: { label: 'setup_dispatcher', nodeId: 'setup_dispatcher' } },
  { id: 'check_needs_visual', type: 'internal', position: { x: 250, y: 100 }, data: { label: 'check_needs_visual', nodeId: 'check_needs_visual' } },
  { id: 'generate_portrait_candidates', type: 'internal', position: { x: 450, y: 50 }, data: { label: 'generate_portrait', nodeId: 'generate_portrait_candidates' } },
  { id: 'portrait_selector', type: 'internal', position: { x: 650, y: 50 }, data: { label: 'portrait_selector', nodeId: 'portrait_selector' } },
  { id: 'fix_character_visual', type: 'internal', position: { x: 850, y: 50 }, data: { label: 'fix_character_visual', nodeId: 'fix_character_visual' } },
  { id: 'generate_fullbody_candidates', type: 'internal', position: { x: 1050, y: 50 }, data: { label: 'generate_fullbody', nodeId: 'generate_fullbody_candidates' } },
  { id: 'fullbody_selector', type: 'internal', position: { x: 1250, y: 50 }, data: { label: 'fullbody_selector', nodeId: 'fullbody_selector' } },
  { id: 'voice_params_choice', type: 'internal', position: { x: 1450, y: 100 }, data: { label: 'voice_params_choice', nodeId: 'voice_params_choice' } },
  { id: 'voice_card_draw', type: 'internal', position: { x: 1650, y: 50 }, data: { label: 'voice_card_draw', nodeId: 'voice_card_draw' } },
  { id: 'voice_params_manual', type: 'internal', position: { x: 1650, y: 180 }, data: { label: 'voice_params_manual', nodeId: 'voice_params_manual' } },
  { id: 'fix_character_profile', type: 'internal', position: { x: 1850, y: 100 }, data: { label: 'fix_character_profile', nodeId: 'fix_character_profile' } },
]

const SETUP_INTERNAL_EDGES: Edge[] = [
  { id: 'se1', source: 'setup_dispatcher', target: 'check_needs_visual' },
  { id: 'se2', source: 'check_needs_visual', target: 'generate_portrait_candidates' },
  { id: 'se3', source: 'generate_portrait_candidates', target: 'portrait_selector' },
  { id: 'se4', source: 'portrait_selector', target: 'fix_character_visual' },
  { id: 'se5', source: 'fix_character_visual', target: 'generate_fullbody_candidates' },
  { id: 'se6', source: 'generate_fullbody_candidates', target: 'fullbody_selector' },
  { id: 'se7', source: 'fullbody_selector', target: 'voice_params_choice' },
  { id: 'se8', source: 'voice_params_choice', target: 'voice_card_draw' },
  { id: 'se9', source: 'voice_params_choice', target: 'voice_params_manual' },
  { id: 'se10', source: 'voice_card_draw', target: 'fix_character_profile' },
  { id: 'se11', source: 'voice_params_manual', target: 'fix_character_profile' },
  { id: 'se12', source: 'fix_character_profile', target: 'setup_dispatcher' },
]

const DRILL_MAP: Record<string, { nodes: Node[]; edges: Edge[] }> = {
  chapter_loop_subgraph: { nodes: CHAPTER_INTERNAL_NODES, edges: CHAPTER_INTERNAL_EDGES },
  init_subgraph: { nodes: SETUP_INTERNAL_NODES, edges: SETUP_INTERNAL_EDGES },
}

export default function FlowCanvas() {
  const { drillPath, popDrill } = useRunStore()
  const currentSubgraph = drillPath[drillPath.length - 1]

  const { nodes, edges } = currentSubgraph
    ? (DRILL_MAP[currentSubgraph] ?? { nodes: [], edges: [] })
    : { nodes: TOP_NODES, edges: TOP_EDGES }

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
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  )
}
