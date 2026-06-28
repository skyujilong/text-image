import { create } from 'zustand'
import type { RunMeta, RenderShot, RenderChapter } from '@/api/client'

export type NodeStatus = 'pending' | 'running' | 'waiting_human' | 'done' | 'error'

export type { RenderChapter }

export interface ActiveInteraction {
  scope: string
  thread_id: string
  node: string
  payload: unknown
}

// 单个图层的视口状态（平移 + 缩放），按 levelKey 记忆，切图时恢复。
export interface ViewportState {
  x: number
  y: number
  zoom: number
}

// 顶层图层的 levelKey；子图层用 subgraphId，与 useGraphSchema 的 schema 缓存键一致。
export const ROOT_LEVEL_KEY = '__root__'

interface RunStore {
  runs: Record<string, RunMeta>
  currentRunId: string | null
  nodeStatuses: Record<string, NodeStatus>
  // 后端 interrupt 的交互数据（node + payload）。仅在 resume 成功 / 切 run 时变化，
  // 由右侧常驻交互区渲染：非空时按 node 切换对应输入 UI，空时显示占位态。
  activeInteraction: ActiveInteraction | null
  drillPath: string[]
  // 是否自动跟随运行节点下钻。手动 pushDrill/popDrill 会关闭，resetDrill（切 run）会重开。
  autoFollow: boolean
  // 每个图层记忆的视口，key 为 levelKey（ROOT_LEVEL_KEY 或 subgraphId）。
  viewports: Record<string, ViewportState>
  runError: string | null
  inspectingNode: string | null
  streamGeneration: number
  // 当前查看的顶层图 scope（main/plan），供 Sidebar 过滤执行历史等共享消费。
  graphScope: 'main' | 'plan'
  // 后端委派状态锁定的 scope：非空表示控制权在子 thread（如 plan），
  // useAutoScope 据此强制锁定该 scope tab，不受 main/run_plan_stage running 抢分干扰；
  // null 表示无委派，回退到按活跃节点抢分切换。
  delegatedScope: 'main' | 'plan' | null

  // 图片渲染看板：storyboard_id → shot。由 GET /render/state 全量初始化，
  // SSE render_image 事件增量更新单个 shot（逐张冒出）。区别于 activeInteraction
  // 一次性 payload——看板随渲染持续变化，需独立持久于 store。
  renderBoard: Record<number, RenderShot>

  setRuns: (runs: RunMeta[]) => void
  upsertRun: (run: RunMeta) => void
  removeRun: (runId: string) => void
  setCurrentRunId: (id: string | null) => void
  setNodeStatus: (node: string, status: NodeStatus) => void
  batchSetNodeStatuses: (statuses: Record<string, NodeStatus>) => void
  resetNodeStatuses: () => void
  setActiveInteraction: (interaction: ActiveInteraction | null) => void
  pushDrill: (subgraph: string) => void
  popDrill: () => void
  resetDrill: () => void
  // 自动跟随专用：整体替换 drillPath，不触碰 autoFollow。
  setDrillPath: (path: string[]) => void
  setAutoFollow: (v: boolean) => void
  setGraphScope: (scope: 'main' | 'plan') => void
  setDelegatedScope: (scope: 'main' | 'plan' | null) => void
  setViewport: (key: string, vp: ViewportState) => void
  setRunError: (msg: string | null) => void
  setInspectingNode: (path: string | null) => void
  incrementStreamGeneration: () => void
  // 全量替换渲染看板（GET /render/state 拉取后初始化）
  setRenderBoard: (shots: RenderShot[]) => void
  // 按 shot 合并看板（挂载拉取用，避免覆盖竞态期间 SSE 已写入的增量）
  mergeRenderBoard: (shots: RenderShot[]) => void
  // 增量更新单个 shot（SSE render_image 事件 / select 后局部刷新）
  upsertRenderShot: (shot: RenderShot) => void
  clearRenderBoard: () => void
  // 渲染工作台章节列表
  renderChapters: RenderChapter[]
  setRenderChapters: (chapters: RenderChapter[]) => void
  // 标记章节是否已启动渲染（避免重复调用 start + 控制 getRenderState 时机）
  renderStarted: Record<string, boolean>
  setRenderStarted: (chapterId: string, started: boolean) => void
}

export const useRunStore = create<RunStore>((set) => ({
  runs: {},
  currentRunId: null,
  nodeStatuses: {},
  activeInteraction: null,
  drillPath: [],
  autoFollow: true,
  viewports: {},
  runError: null,
  inspectingNode: null,
  streamGeneration: 0,
  graphScope: 'main',
  delegatedScope: null,
  renderBoard: {},

  setRuns: (runs) =>
    set({ runs: Object.fromEntries(runs.map((r) => [r.run_id, r])) }),

  upsertRun: (run) =>
    set((s) => ({ runs: { ...s.runs, [run.run_id]: run } })),

  // 删除 run：从 runs 移除；若删的是当前 run，回退到空态（清节点状态/交互/下钻），
  // 让 useRunStream(null) 自动关闭 SSE。
  removeRun: (runId) =>
    set((s) => {
      const rest = { ...s.runs }
      delete rest[runId]
      if (s.currentRunId !== runId) {
        return { runs: rest }
      }
      return {
        runs: rest,
        currentRunId: null,
        nodeStatuses: {},
        activeInteraction: null,
        drillPath: [],
        autoFollow: true,
        delegatedScope: null,
      }
    }),

  setCurrentRunId: (id) => set({ currentRunId: id }),

  setNodeStatus: (node, status) =>
    set((s) => ({ nodeStatuses: { ...s.nodeStatuses, [node]: status } })),

  batchSetNodeStatuses: (statuses) =>
    set((s) => ({ nodeStatuses: { ...statuses, ...s.nodeStatuses } })),

  resetNodeStatuses: () => set({ nodeStatuses: {}, activeInteraction: null, delegatedScope: null }),

  setRunError: (msg) => set({ runError: msg }),

  setInspectingNode: (path) => set({ inspectingNode: path }),

  // 新交互到达（非空）时右侧常驻区切换到对应输入 UI；resume 成功清空时切回占位态。
  setActiveInteraction: (interaction) =>
    set(interaction
      ? { activeInteraction: interaction }
      : { activeInteraction: null }),

  // 用户手动下钻：追加路径并关闭自动跟随（用户已主动选择浏览位置）。
  pushDrill: (subgraph) =>
    set((s) => ({ drillPath: [...s.drillPath, subgraph], autoFollow: false })),

  // 用户手动返回：弹出路径并关闭自动跟随。
  popDrill: () =>
    set((s) => ({ drillPath: s.drillPath.slice(0, -1), autoFollow: false })),

  // 切 run 时调用：清空下钻路径并重开自动跟随（新 run 应跟随运行）。
  resetDrill: () => set({ drillPath: [], autoFollow: true }),

  // 自动跟随专用：整体替换 drillPath，不触碰 autoFollow。
  setDrillPath: (path) => set({ drillPath: path }),

  setAutoFollow: (v) => set({ autoFollow: v }),

  setGraphScope: (scope) => set({ graphScope: scope }),

  setDelegatedScope: (scope) => set({ delegatedScope: scope }),

  setViewport: (key, vp) =>
    set((s) => ({ viewports: { ...s.viewports, [key]: vp } })),

  incrementStreamGeneration: () =>
    set((s) => ({ streamGeneration: s.streamGeneration + 1 })),

  setRenderBoard: (shots) =>
    set({ renderBoard: Object.fromEntries(shots.map((s) => [s.storyboard_id, s])) }),

  mergeRenderBoard: (shots) =>
    set((s) => {
      // 按 storyboard_id 合并，不整体替换——挂载全量拉取与 SSE 增量可能竞态：
      // 拉取发出后、resolve 前若 SSE 先 upsert 了新候选，旧快照整体替换会把它回退。
      // 对同一 shot 保留候选更多的那份（窗口期 SSE 那份候选数更多 / 状态更靠后）；
      // SSE 抢先创建、服务端快照尚无的 shot 也保留。
      const merged = { ...s.renderBoard }
      for (const shot of shots) {
        const prev = merged[shot.storyboard_id]
        if (!prev || shot.candidates.length >= prev.candidates.length) {
          merged[shot.storyboard_id] = shot
        }
      }
      return { renderBoard: merged }
    }),

  upsertRenderShot: (shot) =>
    set((s) => ({ renderBoard: { ...s.renderBoard, [shot.storyboard_id]: shot } })),

  clearRenderBoard: () => set({ renderBoard: {} }),

  renderChapters: [],
  setRenderChapters: (chapters) => set({ renderChapters: chapters }),
  renderStarted: {},
  setRenderStarted: (chapterId, started) =>
    set((s) => ({ renderStarted: { ...s.renderStarted, [chapterId]: started } })),
}))
