import { create } from 'zustand'
import type { RunMeta } from '@/api/client'

export type NodeStatus = 'pending' | 'running' | 'waiting_human' | 'done' | 'error'

export interface ActiveInteraction {
  node: string
  payload: unknown
}

interface RunStore {
  runs: Record<string, RunMeta>
  currentRunId: string | null
  nodeStatuses: Record<string, NodeStatus>
  activeInteraction: ActiveInteraction | null
  drillPath: string[]
  runError: string | null
  inspectingNode: string | null

  setRuns: (runs: RunMeta[]) => void
  upsertRun: (run: RunMeta) => void
  setCurrentRunId: (id: string | null) => void
  setNodeStatus: (node: string, status: NodeStatus) => void
  resetNodeStatuses: () => void
  setActiveInteraction: (interaction: ActiveInteraction | null) => void
  pushDrill: (subgraph: string) => void
  popDrill: () => void
  resetDrill: () => void
  setRunError: (msg: string | null) => void
  setInspectingNode: (path: string | null) => void
}

export const useRunStore = create<RunStore>((set) => ({
  runs: {},
  currentRunId: null,
  nodeStatuses: {},
  activeInteraction: null,
  drillPath: [],
  runError: null,
  inspectingNode: null,

  setRuns: (runs) =>
    set({ runs: Object.fromEntries(runs.map((r) => [r.run_id, r])) }),

  upsertRun: (run) =>
    set((s) => ({ runs: { ...s.runs, [run.run_id]: run } })),

  setCurrentRunId: (id) => set({ currentRunId: id }),

  setNodeStatus: (node, status) =>
    set((s) => ({ nodeStatuses: { ...s.nodeStatuses, [node]: status } })),

  resetNodeStatuses: () => set({ nodeStatuses: {}, activeInteraction: null, runError: null }),

  setRunError: (msg) => set({ runError: msg }),

  setInspectingNode: (path) => set({ inspectingNode: path }),

  setActiveInteraction: (interaction) => set({ activeInteraction: interaction }),

  pushDrill: (subgraph) =>
    set((s) => ({ drillPath: [...s.drillPath, subgraph] })),

  popDrill: () =>
    set((s) => ({ drillPath: s.drillPath.slice(0, -1) })),

  resetDrill: () => set({ drillPath: [] }),
}))
