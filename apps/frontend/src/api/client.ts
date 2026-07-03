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
  chapter_id?: string | null
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

/** 渲染工作台章节列表项（GET /runs/{id}/render/chapters）。 */
export interface RenderChapter {
  chapter_id: string
  status: string
  has_script: boolean
  has_storyboard: boolean
  storyboard_count?: number
  chapter_text_path?: string
  storyboard?: Array<Record<string, unknown>>
}

/** 音频合成状态（GET /runs/{id}/render/chapter/{ch_id}/audio）。 */
export interface AudioStatus {
  chapter_id: string
  status: string
  audio_path: string | null
}

/** 时间轴数据（GET /runs/{id}/render/chapter/{ch_id}/timeline）。 */
export interface TimelineData {
  chapter_id: string
  timeline: unknown | null
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

/** 用户自定义解说方案预设（后端 data/narration_presets.json）。 */
export interface NarrationPreset {
  id: string
  name: string
  base_scheme: string
  adapt_script_template: string
  scene_change_template: string
  created_at: string
}

export interface CreateNarrationPresetBody {
  name: string
  base_scheme: string
  adapt_script_template: string
  scene_change_template: string
}

/** 提示词自进化的三个受记录阶段（= "模块"）。 */
export type EvolutionStage = 'adapt_script' | 'storyboard' | 'initial_characters'

/** per-run 提示词配置：本 run 实际生效模板 vs 该题材内置预设原文（GET /runs/{id}/prompt-config）。 */
export interface PromptConfig {
  scheme_key: string
  scheme_label: string
  templates: { adapt_script: string; scene_change: string }
  defaults: { adapt_script: string; scene_change: string }
}

/** 一次「人类审阅一版生成物」事件（GET /runs/{id}/generation-events）。 */
export interface GenerationEvent {
  id: number
  run_id: string
  scope: string
  chapter_id: string | null
  stage: EvolutionStage
  attempt: number
  scheme_key: string | null
  decision: 'pass' | 'revise'
  feedback: string
  output: unknown
  created_at: string
}

/** 摩擦度排行一行（GET /prompt-evolution/friction）。 */
export interface FrictionStat {
  stage: string
  scheme_key: string | null
  revise_count: number
  pass_count: number
  total: number
}

/** 规则可注入的模板阶段（rule stage）。 */
export type RuleStage = 'adapt_script' | 'scene_change'
export type RuleStatus = 'candidate' | 'active' | 'retired'

/** 校正规则台账一条（GET /prompt-evolution/rules）。 */
export interface LearnedRule {
  id: number
  scheme_key: string
  stage: RuleStage
  rule_text: string
  status: RuleStatus
  source_feedback_sample: string
  hits: number
  created_at: string
  adopted_at: string | null
  retired_at: string | null
}

/** 归纳结果（POST /prompt-evolution/propose）。 */
export interface ProposeResult {
  candidates: LearnedRule[]
  feedback_count: number
  message: string
}

/** 审阅面板 payload.type，作为 run 内归纳/合并接口的 stage 入参（服务端映射到规则 stage）。 */
export type ReviewPanelType = 'script_review' | 'storyboard_review'

/** run 内归纳结果（POST /runs/{run_id}/prompt-evolution/analyze）。proposed 为未落库的候选预览。 */
export interface RunProposeResult {
  proposed: { rule: string; source: string }[]
  feedback_count: number
  scheme_key: string
  stage: RuleStage
  message: string
}

/** 内置题材方案（GET /prompt-evolution/schemes）。 */
export interface SchemeOption {
  key: string
  label: string
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

  restartFrom: (runId: string, scope: string, checkpointId: string, node: string) =>
    request<{ ok: boolean }>(`/runs/${runId}/restart-from`, {
      method: 'POST',
      body: JSON.stringify({ scope, checkpoint_id: checkpointId, node }),
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
  // 渲染看板：获取指定章节的渲染状态
  getRenderState: (runId: string, chapterId: string) =>
    request<RenderBoard>(`/runs/${runId}/render/chapter/${chapterId}/state`),

  // 改词重抽单张：prompt 为空则沿用旧提示词；新候选追加，旧候选保留
  rerollShot: (runId: string, shotId: number, chapterId: string, prompt?: string) =>
    request<{ ok: boolean }>(`/runs/${runId}/render/reroll`, {
      method: 'POST',
      body: JSON.stringify({ shot_id: shotId, chapter_id: chapterId, prompt: prompt ?? null }),
    }),

  // 选定某候选为该 shot 的终图
  selectCandidate: (runId: string, shotId: number, chapterId: string, candidate: string) =>
    request<{ ok: boolean }>(`/runs/${runId}/render/select`, {
      method: 'POST',
      body: JSON.stringify({ shot_id: shotId, chapter_id: chapterId, candidate }),
    }),

  // ─── 渲染工作台 ─────────────────────────────────────────────
  // 章节列表 + 渲染状态（后端返回 {chapters: [...]}，解包为裸数组）
  getRenderChapters: (runId: string) =>
    request<{ chapters: RenderChapter[] }>(`/runs/${runId}/render/chapters`).then((r) => r.chapters),

  // 渲染预览：只读返回分镜规格信息，不触发渲染会话。用于初始展示。
  getRenderPreview: (runId: string, chapterId: string) =>
    request<RenderBoard>(`/runs/${runId}/render/chapter/${chapterId}/preview`),

  // 启动某章节渲染。force=true 时强制中断其他章节的渲染并切换
  startChapterRender: (runId: string, chapterId: string, force?: boolean) =>
    request<{ ok: boolean }>(`/runs/${runId}/render/chapter/${chapterId}/start?force_switch=${force ? 'true' : 'false'}`, {
      method: 'POST',
    }),

  // 提交 TTS 合成
  synthesizeAudio: (runId: string, chapterId: string, audioConfig: Record<string, unknown>) =>
    request<{ ok: boolean }>(`/runs/${runId}/render/chapter/${chapterId}/audio`, {
      method: 'POST',
      body: JSON.stringify(audioConfig),
    }),

  // 查询音频合成状态
  getAudioStatus: (runId: string, chapterId: string) =>
    request<AudioStatus>(`/runs/${runId}/render/chapter/${chapterId}/audio`),

  // 生成时间轴
  buildTimeline: (runId: string, chapterId: string) =>
    request<{ ok: boolean }>(`/runs/${runId}/render/chapter/${chapterId}/timeline`, {
      method: 'POST',
    }),

  // 获取时间轴数据
  getTimeline: (runId: string, chapterId: string) =>
    request<TimelineData>(`/runs/${runId}/render/chapter/${chapterId}/timeline`),

  // 导出剪映草稿
  exportDraft: (runId: string) =>
    request<{ export_path: string; chapters_status: Record<string, string> }>(`/runs/${runId}/render/export`, {
      method: 'POST',
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

  // ─── 解说方案用户预设（跨 run 持久化，见 docs/narration-scheme.md）───────
  listNarrationPresets: () => request<NarrationPreset[]>('/narration-presets'),

  createNarrationPreset: (body: CreateNarrationPresetBody) =>
    request<NarrationPreset>('/narration-presets', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  deleteNarrationPreset: (id: string) =>
    request<{ ok: boolean }>(`/narration-presets/${id}`, { method: 'DELETE' }),

  // ─── 提示词自进化 · per-run 检视 ──────────────────────────────
  // 本 run 实际生效模板 vs 内置预设原文（供"调整 vs 原始"对比）
  getPromptConfig: (runId: string) =>
    request<PromptConfig>(`/runs/${runId}/prompt-config`),

  // 本 run 的审阅事件时间线（含被审输出、决策、修改意见）
  getGenerationEvents: (runId: string) =>
    request<GenerationEvent[]>(`/runs/${runId}/generation-events`),

  // ─── 提示词自进化 · 进化台（跨 run 全局）──────────────────────
  getEvolutionSchemes: () => request<SchemeOption[]>('/prompt-evolution/schemes'),

  getFriction: () => request<FrictionStat[]>('/prompt-evolution/friction'),

  listRules: (params?: { scheme_key?: string; stage?: string; status?: string }) => {
    const qs = new URLSearchParams()
    if (params?.scheme_key) qs.set('scheme_key', params.scheme_key)
    if (params?.stage) qs.set('stage', params.stage)
    if (params?.status) qs.set('status', params.status)
    const q = qs.toString()
    return request<LearnedRule[]>(`/prompt-evolution/rules${q ? `?${q}` : ''}`)
  },

  proposeRules: (schemeKey: string, stage: RuleStage) =>
    request<ProposeResult>('/prompt-evolution/propose', {
      method: 'POST',
      body: JSON.stringify({ scheme_key: schemeKey, stage }),
    }),

  createRule: (schemeKey: string, stage: RuleStage, ruleText: string) =>
    request<{ ok: boolean }>('/prompt-evolution/rules', {
      method: 'POST',
      body: JSON.stringify({ scheme_key: schemeKey, stage, rule_text: ruleText }),
    }),

  adoptRule: (id: number) =>
    request<{ ok: boolean }>(`/prompt-evolution/rules/${id}/adopt`, { method: 'POST' }),

  rejectRule: (id: number) =>
    request<{ ok: boolean }>(`/prompt-evolution/rules/${id}/reject`, { method: 'POST' }),

  retireRule: (id: number) =>
    request<{ ok: boolean }>(`/prompt-evolution/rules/${id}/retire`, { method: 'POST' }),

  // ─── 提示词自进化 · 环②③ run 内版（本 run 审阅面板内触发）──────────
  /** 归纳本 run 该阶段历次打回 → 候选规则预览（无副作用，不落库）。 */
  analyzeRunRules: (runId: string, panelType: ReviewPanelType) =>
    request<RunProposeResult>(`/runs/${runId}/prompt-evolution/analyze`, {
      method: 'POST',
      body: JSON.stringify({ stage: panelType }),
    }),

  /** 人工确认后合并进本 run 的校正清单；alsoGlobal 时另写一份全局候选。 */
  mergeRunRules: (runId: string, panelType: ReviewPanelType, rules: string[], alsoGlobal = true) =>
    request<{ ok: boolean; merged: number; global_candidates: number }>(
      `/runs/${runId}/prompt-evolution/merge`,
      {
        method: 'POST',
        body: JSON.stringify({ stage: panelType, rules, also_global: alsoGlobal }),
      },
    ),
}
