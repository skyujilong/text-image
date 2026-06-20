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
  // 后端 interrupt 的交互数据（node + payload）。关闭抽屉时不清空，
  // 仅在 resume 成功 / 切 run 时变化，保证用户关掉抽屉后仍能重新打开。
  activeInteraction: ActiveInteraction | null
  // 抽屉显隐（与 activeInteraction 解耦）。关闭只置 false，重新打开入口置 true。
  interactionVisible: boolean
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
  setCurrentRunId: (id: string | null) => void
  setNodeStatus: (node: string, status: NodeStatus) => void
  resetNodeStatuses: () => void
  setActiveInteraction: (interaction: ActiveInteraction | null) => void
  setInteractionVisible: (v: boolean) => void
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
  interactionVisible: true,
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

  setCurrentRunId: (id) => set({ currentRunId: id }),

  setNodeStatus: (node, status) =>
    set((s) => ({ nodeStatuses: { ...s.nodeStatuses, [node]: status } })),

  resetNodeStatuses: () => set({ nodeStatuses: {}, activeInteraction: null, interactionVisible: true }),

  setRunError: (msg) => set({ runError: msg }),

  setInspectingNode: (path) => set({ inspectingNode: path }),

  // 新交互到达（非空）时自动弹出抽屉；resume 成功清空时归零。
  // 关闭抽屉走 setInteractionVisible(false)，不应清空 activeInteraction。
  setActiveInteraction: (interaction) =>
    set(interaction
      ? { activeInteraction: interaction, interactionVisible: true }
      : { activeInteraction: null }),

  setInteractionVisible: (v) => set({ interactionVisible: v }),

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
