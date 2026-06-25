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

export interface RunCurrentState {
  status: string
  node_statuses: Record<string, string>
  active_interaction: {
    scope: string
    thread_id: string
    node: string
    path: string
    payload: unknown
  } | null
}

export interface CheckpointEntry {
  checkpoint_id: string
  step: number
  node: string | null
  created_at: string | null
  next: string[]
  scope: string
  thread_id: string
}

/** 渲染看板单个候选图。 */
export interface RenderCandidate {
  path: string
  url: string
}

/** 渲染看板单个换图点 shot。 */
export interface RenderShot {
  storyboard_id: number
  workflow: 'qwen_t2i' | 'qwen_edit'
  prompt: string
  subjects: string[]
  status: 'pending' | 'rendering' | 'done' | 'error'
  error: string | null
  candidates: RenderCandidate[]
  selected: string | null
  selected_url: string | null
}

/** 渲染看板（GET /runs/{id}/render/state）。 */
export interface RenderBoard {
  chapter_id: string
  shots: RenderShot[]
  all_done: boolean
  pending: string[]
}

// dots.tts 音色预设（GET /voices 返回项）。audio_url 指向 dots 服务端音频，前端仅展示名称。
export interface VoicePreset {
  name: string
  audio_url: string
  prompt_text: string | null
  created_at: string
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

/** 绝对路径 → 前端可访问的 /files URL（SSE render_image 事件携带绝对路径，需转 URL）。
 * 按 '/' 分段编码后再用 '/' 拼接——保留路径分隔符。后端 _file_url 用 urllib quote（默认 safe='/'，
 * 不编码 '/'），/files/{path:path} 路由按字面 '/' 匹配；若整体 encodeURIComponent 把 '/' 变 %2F，
 * 会与后端 GET /render/state 给的 URL 不一致（缓存未命中，且部分代理/ASGI 对 %2F 直接 404）。 */
export function fileUrl(absPath: string): string {
  const encoded = absPath
    .replace(/^\/+/, '')
    .split('/')
    .map(encodeURIComponent)
    .join('/')
  return `${BASE}/files/${encoded}`
}

export const api = {
  startRun: (params: StartRunParams) =>
    request<{ run_id: string }>('/runs', { method: 'POST', body: JSON.stringify(params) }),

  listRuns: () => request<RunMeta[]>('/runs'),

  resumeRun: (runId: string, scope: string, threadId: string, resumeValue: unknown) =>
    request<{ ok: boolean }>(`/runs/${runId}/resume`, {
      method: 'POST',
      body: JSON.stringify({ scope, thread_id: threadId, resume_value: resumeValue }),
    }),

  retryRun: (runId: string) =>
    request<{ ok: boolean }>(`/runs/${runId}/retry`, { method: 'POST' }),

  restartFrom: (runId: string, scope: string, node: string) =>
    request<{ ok: boolean }>(`/runs/${runId}/restart-from`, {
      method: 'POST',
      body: JSON.stringify({ scope, node }),
    }),

  // 从某 checkpoint 分叉出独立新 run（保留原 run 历史）
  forkRun: (runId: string, scope: string, checkpointId: string | null) =>
    request<{ run_id: string }>(`/runs/${runId}/fork`, {
      method: 'POST',
      body: JSON.stringify({ scope, checkpoint_id: checkpointId }),
    }),

  // 重命名 run
  updateRun: (runId: string, novelTitle: string) =>
    request<{ ok: boolean }>(`/runs/${runId}`, {
      method: 'PATCH',
      body: JSON.stringify({ novel_title: novelTitle }),
    }),

  // 删除废弃 run（清理 checkpoint + 记录，不动 novel_dir）；running 状态后端会 409
  deleteRun: (runId: string) =>
    request<{ ok: boolean }>(`/runs/${runId}`, { method: 'DELETE' }),

  validatePath: (path: string) =>
    request<{ exists: boolean }>(`/validate/path?path=${encodeURIComponent(path)}`),

  getNovelConfig: (dir: string) =>
    request<Record<string, unknown>>(`/novels/config?dir=${encodeURIComponent(dir)}`),

  listNovels: () => request<{ dirs: string[] }>('/novels/list'),

  getGraphSchema: (scope?: string) =>
    request<GraphSchema>(`/graph/schema?scope=${encodeURIComponent(scope || 'main')}`),

  getNodeState: (runId: string, scope: string, nodePath: string) =>
    request<{ node: string; values: Record<string, unknown> }>(
      `/runs/${runId}/state?scope=${encodeURIComponent(scope)}&node_path=${encodeURIComponent(nodePath)}`
    ),

  getCheckpoints: (runId: string) =>
    request<CheckpointEntry[]>(`/runs/${runId}/checkpoints`),

  getRunCurrentState: (runId: string) =>
    request<RunCurrentState>(`/runs/${runId}/current-state`),

  // ─── 图片渲染（抽卡）─────────────────────────────────────────
  // 渲染看板：每个换图点的提示词 + 候选图 URL + 选定终图 + 状态
  getRenderState: (runId: string) =>
    request<RenderBoard>(`/runs/${runId}/render/state`),

  // 改词重抽单张：prompt 为空则沿用旧提示词；新候选追加，旧候选保留
  rerollShot: (runId: string, shotId: number, prompt?: string) =>
    request<{ ok: boolean }>(`/runs/${runId}/render/reroll`, {
      method: 'POST',
      body: JSON.stringify({ shot_id: shotId, prompt: prompt ?? null }),
    }),

  // 选定某候选为该 shot 的终图
  selectCandidate: (runId: string, shotId: number, candidate: string) =>
    request<{ ok: boolean }>(`/runs/${runId}/render/select`, {
      method: 'POST',
      body: JSON.stringify({ shot_id: shotId, candidate }),
    }),

  // 上传文件（如角色三视图）到 run 的 novel_dir/characters，按 {小说名}-{人物名}.ext 命名。
  // 仅本地落盘（不调 ComfyUI）；返回 { path }，前端拿 path 后 resume { tri_views: {name: path}, skipped: [...] } 给 batch_upload_tri_view 节点。
  uploadFile: async (runId: string, file: File, subdir: string, characterName: string) => {
    const form = new FormData()
    form.append('run_id', runId)
    form.append('subdir', subdir)
    form.append('character_name', characterName)
    form.append('file', file)
    const res = await fetch(`${BASE}/upload`, { method: 'POST', body: form })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(`HTTP ${res.status}: ${text}`)
    }
    return res.json() as Promise<{ path: string }>
  },

  // ─── 音色（dots.tts voices，经后端代理）─────────────────────
  // 列出 dots.tts 已保存的音色预设，供 configure_audio 面板下拉选择已有音色
  listVoices: () => request<VoicePreset[]>('/voices'),

  // 上传参考音频创建音色预设（multipart）；成功后该音色加入列表，可被「选择已有音色」引用。
  // dots 校验失败（格式/名称/大小）由后端透传为 400，错误信息直接抛出供面板展示。
  createVoice: async (name: string, file: File, promptText?: string) => {
    const form = new FormData()
    form.append('name', name)
    form.append('audio', file)
    if (promptText) form.append('prompt_text', promptText)
    const res = await fetch(`${BASE}/voices`, { method: 'POST', body: form })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(`HTTP ${res.status}: ${text}`)
    }
    return res.json() as Promise<VoicePreset>
  },
}
