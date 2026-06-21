import { useEffect, useRef, useState } from 'react'
import { api, type CheckpointEntry } from '@/api/client'
import { useRunStore } from '@/store/runStore'

const ITEM_HEIGHT = 36 // px，每行高度（需与实际 py-1.5 + border 对齐）
const OVERSCAN = 5   // 视窗外额外渲染行数，避免快速滚动白屏

// 节点路径（最后一段 leaf）→ 中文友好名称
// 路径形如 "init_subgraph/load_config"，取最后一段匹配
const NODE_LABELS: Record<string, string> = {
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
  generate_storyboard: '生成分镜',
  detect_new_characters_llm: 'LLM 检测新角色',
  review_chapter: '📖 审阅章节分镜',
  chapter_advance_decision: '章节推进决策',
  configure_audio: '配置音频',
  render_dispatch: '渲染调度',
  render_generate_images: '生成图片',
  render_synthesize_audio: '合成音频',
  render_build_timeline: '构建时间轴',
  export_to_jianying: '导出剪映草稿',
  final_decision: '收尾决策',
}

function formatNodeLabel(nodePath: string | null): string {
  if (!nodePath) return '(初始化)'
  const leaf = nodePath.split('/').pop() ?? nodePath
  const label = NODE_LABELS[leaf]
  if (!label) return nodePath
  // 子图路径加前缀展示层级，如 "init_subgraph / 加载配置"
  const parts = nodePath.split('/')
  if (parts.length > 1) {
    const parentLabel = NODE_LABELS[parts[0]] ?? parts[0]
    return `${parentLabel}  /  ${label}`
  }
  return label
}

interface Props {
  runId: string
}

export default function CheckpointTimeline({ runId }: Props) {
  const [entries, setEntries] = useState<CheckpointEntry[]>([])
  const {
    runs,
    setRuns,
    upsertRun,
    resetNodeStatuses,
    resetDrill,
    setCurrentRunId,
    incrementStreamGeneration,
    setRunError,
  } = useRunStore()

  // 虚拟滚动状态
  const scrollRef = useRef<HTMLDivElement>(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewHeight, setViewHeight] = useState(300)

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setViewHeight(el.clientHeight))
    ro.observe(el)
    setViewHeight(el.clientHeight)
    return () => ro.disconnect()
  }, [])

  // 初次加载 + runId 变化时拉取
  useEffect(() => {
    if (!runId) return
    api.getCheckpoints(runId).then(setEntries).catch(console.error)
  }, [runId])

  // 运行中每 3 秒轮询刷新
  const currentRun = runs[runId]
  const isActive = currentRun?.status === 'running' || currentRun?.status === 'waiting_human'
  useEffect(() => {
    if (!runId || !isActive) return
    const timer = setInterval(() => {
      api.getCheckpoints(runId).then(setEntries).catch(console.error)
    }, 3000)
    return () => clearInterval(timer)
  }, [runId, isActive])

  // 覆盖重跑：在原 thread 上从该节点前重放（旧 checkpoint 在 append-only 树中保留）
  const handleRestartFrom = async (nodePath: string) => {
    setRunError(null) // 重新运行前先清空旧错误
    await api.restartFrom(runId, nodePath)
    setCurrentRunId(runId)
    resetNodeStatuses()
    resetDrill()
    const run = runs[runId]
    if (run) upsertRun({ ...run, status: 'running' })
    incrementStreamGeneration() // 触发 SSE 重新连接
  }

  // 分叉：从该 checkpoint 复制出独立新 run，原 run 历史不动
  // 仅顶层 checkpoint 支持分叉（子图内中间点 fork 暂不支持）
  const handleFork = async (checkpointId: string) => {
    setRunError(null)
    const { run_id: newId } = await api.forkRun(runId, checkpointId)
    const all = await api.listRuns()
    setRuns(all)
    setCurrentRunId(newId)
    resetNodeStatuses()
    resetDrill()
    incrementStreamGeneration()
  }

  const totalHeight = entries.length * ITEM_HEIGHT
  const startIdx = Math.max(0, Math.floor(scrollTop / ITEM_HEIGHT) - OVERSCAN)
  const endIdx = Math.min(entries.length, Math.ceil((scrollTop + viewHeight) / ITEM_HEIGHT) + OVERSCAN)
  const visibleEntries = entries.slice(startIdx, endIdx)

  return (
    <div className="text-xs flex flex-col h-full">
      {entries.length === 0 ? (
        <div className="px-3 py-2 text-gray-400">暂无执行记录</div>
      ) : (
        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto relative"
          onScroll={(e) => setScrollTop((e.target as HTMLDivElement).scrollTop)}
        >
          {/* 撑开滚动总高度 */}
          <div style={{ height: totalHeight, position: 'relative' }}>
            {visibleEntries.map((e, i) => (
              <div
                key={e.checkpoint_id}
                style={{ position: 'absolute', top: (startIdx + i) * ITEM_HEIGHT, left: 0, right: 0, height: ITEM_HEIGHT }}
                className="flex items-center gap-2 px-3 border-b hover:bg-gray-50"
              >
                <div className="flex-1 truncate text-gray-700" title={e.node ?? ''}>{formatNodeLabel(e.node)}</div>
                <div className="text-gray-400 shrink-0">
                  {e.created_at ? new Date(e.created_at).toLocaleTimeString() : '—'}
                </div>
                <button
                  className="shrink-0 text-gray-400 hover:text-blue-600"
                  title="从此节点重跑（覆盖当前分支）"
                  onClick={() => e.node && handleRestartFrom(e.node)}
                >
                  ↺
                </button>
                {e.checkpoint_ns === '' && (
                  <button
                    className="shrink-0 text-gray-400 hover:text-green-600"
                    title="从此点分叉新 Run（保留原历史）"
                    onClick={() => handleFork(e.checkpoint_id)}
                  >
                    分叉
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
