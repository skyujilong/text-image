/**
 * 节点 ID（后端 LangGraph 节点名 / 英文）→ 中文友好名称映射。
 *
 * 后端 graph schema 导出的 node.label 就是英文 node_id（见
 * apps/backend/api/v1/endpoints/graph.py 的 _serialize_graph）。前端在两处需要
 * 中文展示：FlowCanvas 图上节点、CheckpointTimeline 执行记录。映射表集中在此，
 * 避免两处各自维护一份导致漂移。
 *
 * key 取节点路径的叶子段（如 "init_subgraph/load_config" → "load_config"）。
 */

export const NODE_LABELS: Record<string, string> = {
  // 顶层
  init_subgraph: '初始化阶段',
  chapter_loop_subgraph: '章节处理阶段',
  // init 子图
  load_config: '加载配置',
  parse_characters_llm: 'LLM 解析角色',
  review_initial_characters: '👤 审阅初始角色',
  // character_setup 子图
  character_setup_subgraph: '角色设定',
  setup_dispatcher: '角色队列调度',
  batch_upload_tri_view: '📸 上传角色三视图',
  batch_fix_profiles: '修正角色档案',
  // chapter 子图
  load_chapter: '加载章节',
  adapt_script: '剧本改编',
  review_script: '📖 审阅剧本',
  generate_storyboard: '生成分镜',
  review_storyboard: '📖 审阅分镜',
  detect_new_characters_llm: 'LLM 检测新角色',
  commit_chapter: '提交章节规划',
  chapter_advance_decision: '章节推进决策',
  configure_audio: '配置音频',
  render_dispatch: '渲染调度',
  render_generate_images: '生成图片',
  render_synthesize_audio: '合成音频',
  render_build_timeline: '构建时间轴',
  export_to_jianying: '导出剪映草稿',
  final_decision: '收尾决策',
}

/**
 * 取单个节点（叶子 id）的中文名。无映射时返回 undefined，调用方自行回退到英文 id。
 * 用于 FlowCanvas 图上节点：中文名作主标题、英文 id 作副标题。
 */
export function getNodeLabel(nodeId: string): string | undefined {
  return NODE_LABELS[nodeId]
}

/**
 * 把节点路径格式化为带层级前缀的中文展示串。
 * 用于 CheckpointTimeline 执行记录列表：
 *   "init_subgraph/load_config" → "初始化阶段  /  加载配置"
 *   "load_config"               → "加载配置"
 *   null / 未知                 → "(初始化)" / 原路径
 */
export function formatNodePathLabel(nodePath: string | null): string {
  if (!nodePath) return '(初始化)'
  const leaf = nodePath.split('/').pop() ?? nodePath
  const label = NODE_LABELS[leaf]
  if (!label) return nodePath
  // 子图路径加前缀展示层级
  const parts = nodePath.split('/')
  if (parts.length > 1) {
    const parentLabel = NODE_LABELS[parts[0]] ?? parts[0]
    return `${parentLabel}  /  ${label}`
  }
  return label
}
