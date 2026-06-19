const BASE = '/api'

export interface GraphSchemaNode {
  id: string
  label: string
  type: 'subgraph' | 'internal'
}

export interface GraphSchemaEdge {
  id: string
  source: string
  target: string
  conditional: boolean
  label: string | null
  is_back_edge: boolean
}

export interface GraphSchema {
  nodes: GraphSchemaNode[]
  edges: GraphSchemaEdge[]
}

export interface RunMeta {
  run_id: string
  novel_dir: string
  novel_title: string
  status: 'pending' | 'running' | 'waiting_human' | 'done' | 'error'
  created_at: string
  params: Record<string, unknown>
  parent_run_id?: string | null
  fork_source_checkpoint_id?: string | null
}

export interface CheckpointEntry {
  checkpoint_id: string
  step: number
  node: string | null
  created_at: string | null
  next: string[]
  checkpoint_ns: string
}

export interface StartRunParams {
  novel_dir: string
  novel_title?: string
  genre?: string
  writing_style?: string
  target_audience?: string
  core_tone?: string
  chapter_word_count?: string
  total_word_count?: string
  core_theme?: string
  world_building?: string
  core_conflicts?: string
  overall_outline?: string
  character_profiles?: string
  start_chapter?: number
  end_chapter?: number | null
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`HTTP ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  startRun: (params: StartRunParams) =>
    request<{ run_id: string }>('/runs', { method: 'POST', body: JSON.stringify(params) }),

  listRuns: () => request<RunMeta[]>('/runs'),

  resumeRun: (runId: string, resumeValue: unknown) =>
    request<{ ok: boolean }>(`/runs/${runId}/resume`, {
      method: 'POST',
      body: JSON.stringify({ resume_value: resumeValue }),
    }),

  retryRun: (runId: string) =>
    request<{ ok: boolean }>(`/runs/${runId}/retry`, { method: 'POST' }),

  restartFrom: (runId: string, nodePath: string) =>
    request<{ ok: boolean }>(`/runs/${runId}/restart-from`, {
      method: 'POST',
      body: JSON.stringify({ node_path: nodePath }),
    }),

  // 从某 checkpoint 分叉出独立新 run（保留原 run 历史）
  forkRun: (runId: string, checkpointId: string | null) =>
    request<{ run_id: string }>(`/runs/${runId}/fork`, {
      method: 'POST',
      body: JSON.stringify({ checkpoint_id: checkpointId }),
    }),

  // 重命名 run
  updateRun: (runId: string, novelTitle: string) =>
    request<{ ok: boolean }>(`/runs/${runId}`, {
      method: 'PATCH',
      body: JSON.stringify({ novel_title: novelTitle }),
    }),

  validatePath: (path: string) =>
    request<{ exists: boolean }>(`/validate/path?path=${encodeURIComponent(path)}`),

  getNovelConfig: (dir: string) =>
    request<Record<string, unknown>>(`/novels/config?dir=${encodeURIComponent(dir)}`),

  listNovels: () => request<{ dirs: string[] }>('/novels/list'),

  getGraphSchema: (subgraphId?: string) =>
    request<GraphSchema>(subgraphId ? `/graph/schema/${subgraphId}` : '/graph/schema'),

  getNodeState: (runId: string, nodePath: string) =>
    request<{ node: string; values: Record<string, unknown> }>(
      `/runs/${runId}/state?node_path=${encodeURIComponent(nodePath)}`
    ),

  getCheckpoints: (runId: string) =>
    request<CheckpointEntry[]>(`/runs/${runId}/checkpoints`),
}
