import { create } from 'zustand'
import type { RunMeta } from '@/api/client'

export type NodeStatus = 'pending' | 'running' | 'waiting_human' | 'done' | 'error'

export interface ActiveInteraction {
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
  setViewport: (key: string, vp: ViewportState) => void
  setRunError: (msg: string | null) => void
  setInspectingNode: (path: string | null) => void
  incrementStreamGeneration: () => void
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
      }
    }),

  setCurrentRunId: (id) => set({ currentRunId: id }),

  setNodeStatus: (node, status) =>
    set((s) => ({ nodeStatuses: { ...s.nodeStatuses, [node]: status } })),

  batchSetNodeStatuses: (statuses) =>
    set((s) => ({ nodeStatuses: { ...statuses, ...s.nodeStatuses } })),

  resetNodeStatuses: () => set({ nodeStatuses: {}, activeInteraction: null }),

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

  setViewport: (key, vp) =>
    set((s) => ({ viewports: { ...s.viewports, [key]: vp } })),

  incrementStreamGeneration: () =>
    set((s) => ({ streamGeneration: s.streamGeneration + 1 })),
}))
