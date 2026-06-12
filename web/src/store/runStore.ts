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

  setRuns: (runs: RunMeta[]) => void
  upsertRun: (run: RunMeta) => void
  setCurrentRunId: (id: string | null) => void
  setNodeStatus: (node: string, status: NodeStatus) => void
  resetNodeStatuses: () => void
  setActiveInteraction: (interaction: ActiveInteraction | null) => void
  pushDrill: (subgraph: string) => void
  popDrill: () => void
  resetDrill: () => void
}

export const useRunStore = create<RunStore>((set) => ({
  runs: {},
  currentRunId: null,
  nodeStatuses: {},
  activeInteraction: null,
  drillPath: [],

  setRuns: (runs) =>
    set({ runs: Object.fromEntries(runs.map((r) => [r.run_id, r])) }),

  upsertRun: (run) =>
    set((s) => ({ runs: { ...s.runs, [run.run_id]: run } })),

  setCurrentRunId: (id) => set({ currentRunId: id }),

  setNodeStatus: (node, status) =>
    set((s) => ({ nodeStatuses: { ...s.nodeStatuses, [node]: status } })),

  resetNodeStatuses: () => set({ nodeStatuses: {}, activeInteraction: null }),

  setActiveInteraction: (interaction) => set({ activeInteraction: interaction }),

  pushDrill: (subgraph) =>
    set((s) => ({ drillPath: [...s.drillPath, subgraph] })),

  popDrill: () =>
    set((s) => ({ drillPath: s.drillPath.slice(0, -1) })),

  resetDrill: () => set({ drillPath: [] }),
}))
