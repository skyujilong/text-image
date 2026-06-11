# React 前端画布层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建 React 前端应用（`web/`），实现 React Flow 双层节点画布（顶层子图 + 下钻内部节点）、SSE 实时状态订阅、Zustand 全局状态、左侧历史栏和页面骨架。

**Architecture:** Vite + React + TypeScript 脚手架在 `web/` 目录；`/api` 代理到 FastAPI `:8000`；Zustand store 管理 runs/nodeStatuses/activeInteraction/drillPath；`useRunStream` hook 订阅 SSE 并 dispatch 状态；React Flow 渲染两类自定义节点（SubgraphNode / InternalNode），`drillPath` 状态栈切换视图。

**Tech Stack:** Vite 5, React 18, TypeScript 5, React Flow (`@xyflow/react`), Zustand, shadcn/ui, Tailwind CSS, react-hook-form, zod

**前置条件:** Plan C（FastAPI 后端）已完成，`POST /runs`、`GET /runs`、`GET /runs/{id}/stream` 可用。

---

## 文件结构

```
web/
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.ts
├── postcss.config.js
├── components.json                   # shadcn/ui 配置
└── src/
    ├── main.tsx
    ├── App.tsx
    ├── store/
    │   └── runStore.ts               # Zustand store
    ├── hooks/
    │   └── useRunStream.ts           # SSE 订阅 hook
    ├── api/
    │   └── client.ts                 # fetch 封装（/api 前缀）
    ├── components/
    │   ├── ui/                       # shadcn/ui 组件（按需 add）
    │   ├── flow/
    │   │   ├── FlowCanvas.tsx        # React Flow 画布容器
    │   │   ├── SubgraphNode.tsx      # 顶层子图节点卡片
    │   │   └── InternalNode.tsx      # 下钻内部节点卡片
    │   └── layout/
    │       ├── Sidebar.tsx           # 左侧历史 Runs 栏
    │       └── MainContent.tsx       # 主内容区（画布或表单）
    └── pages/
        └── RunPage.tsx               # 根页面
```

---

## Task 1：Vite 脚手架与依赖安装

**Files:**
- Create: `web/package.json`
- Create: `web/vite.config.ts`
- Create: `web/tsconfig.json`
- Create: `web/index.html`
- Create: `web/src/main.tsx`
- Create: `web/src/App.tsx`

- [ ] **Step 1：使用 Vite 创建项目骨架**

```bash
cd /Users/nbe01/workspace/text-image
npm create vite@latest web -- --template react-ts
cd web
```

Expected：`web/` 目录生成，包含 `package.json`、`vite.config.ts`、`tsconfig.json`、`index.html` 等。

- [ ] **Step 2：安装核心依赖**

```bash
cd /Users/nbe01/workspace/text-image/web
npm install @xyflow/react zustand react-hook-form zod
npm install -D tailwindcss postcss autoprefixer
npx tailwindcss init -p
```

Expected：`node_modules/` 生成，`tailwind.config.js` 和 `postcss.config.js` 创建。

- [ ] **Step 3：安装 shadcn/ui**

```bash
cd /Users/nbe01/workspace/text-image/web
npx shadcn@latest init
```

过程中选择：
- Style: Default
- Base color: Slate
- CSS variables: Yes

Expected：`components.json`、`src/lib/utils.ts`、`src/index.css` 更新。

- [ ] **Step 4：配置 Vite 代理**

编辑 `web/vite.config.ts`，将内容替换为：

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
})
```

- [ ] **Step 5：配置 `tsconfig.json` 路径别名**

确保 `tsconfig.json` 的 `compilerOptions` 包含：

```json
{
  "compilerOptions": {
    "baseUrl": ".",
    "paths": { "@/*": ["./src/*"] },
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true
  },
  "include": ["src"]
}
```

- [ ] **Step 6：验证启动**

```bash
cd /Users/nbe01/workspace/text-image/web
npm run dev
```

Expected：控制台输出 `Local: http://localhost:5173/`，浏览器打开看到默认 Vite+React 页面。Ctrl+C 停止。

- [ ] **Step 7：Commit**

```bash
cd /Users/nbe01/workspace/text-image
git add web/
git commit -m "chore: 初始化 Vite+React+TypeScript+shadcn/ui 前端项目"
```

---

## Task 2：API 客户端封装

**Files:**
- Create: `web/src/api/client.ts`

- [ ] **Step 1：实现 `web/src/api/client.ts`**

```typescript
const BASE = '/api'

export interface RunMeta {
  run_id: string
  novel_dir: string
  novel_title: string
  status: 'pending' | 'running' | 'waiting_human' | 'done' | 'error'
  created_at: string
}

export interface StartRunParams {
  novel_dir: string
  novel_title?: string
  worldview?: string
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

  validatePath: (path: string) =>
    request<{ exists: boolean }>(`/validate/path?path=${encodeURIComponent(path)}`),

  getNovelConfig: (dir: string) =>
    request<Record<string, unknown>>(`/novels/config?dir=${encodeURIComponent(dir)}`),

  listNovels: () => request<{ dirs: string[] }>('/novels/list'),
}
```

- [ ] **Step 2：验证 TypeScript 无错误**

```bash
cd /Users/nbe01/workspace/text-image/web
npx tsc --noEmit
```

Expected：无输出（无错误）。

- [ ] **Step 3：Commit**

```bash
cd /Users/nbe01/workspace/text-image
git add web/src/api/
git commit -m "feat: 添加 API 客户端封装（api/client.ts）"
```

---

## Task 3：Zustand Store

**Files:**
- Create: `web/src/store/runStore.ts`

- [ ] **Step 1：实现 `web/src/store/runStore.ts`**

```typescript
import { create } from 'zustand'
import type { RunMeta } from '@/api/client'

export type NodeStatus = 'pending' | 'running' | 'waiting_human' | 'done' | 'error'

export interface ActiveInteraction {
  node: string
  payload: unknown
}

interface RunStore {
  // Runs 元信息
  runs: Record<string, RunMeta>
  currentRunId: string | null

  // 节点状态（当前 run）
  nodeStatuses: Record<string, NodeStatus>

  // 人工交互
  activeInteraction: ActiveInteraction | null

  // React Flow 下钻路径栈
  // [] = 顶层子图视图
  // ["chapter_loop_subgraph"] = 下钻到 chapter_loop 内部
  drillPath: string[]

  // Actions
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
    set({
      runs: Object.fromEntries(runs.map((r) => [r.run_id, r])),
    }),

  upsertRun: (run) =>
    set((s) => ({ runs: { ...s.runs, [run.run_id]: run } })),

  setCurrentRunId: (id) =>
    set({ currentRunId: id }),

  setNodeStatus: (node, status) =>
    set((s) => ({ nodeStatuses: { ...s.nodeStatuses, [node]: status } })),

  resetNodeStatuses: () => set({ nodeStatuses: {}, activeInteraction: null }),

  setActiveInteraction: (interaction) =>
    set({ activeInteraction: interaction }),

  pushDrill: (subgraph) =>
    set((s) => ({ drillPath: [...s.drillPath, subgraph] })),

  popDrill: () =>
    set((s) => ({ drillPath: s.drillPath.slice(0, -1) })),

  resetDrill: () => set({ drillPath: [] }),
}))
```

- [ ] **Step 2：验证 TypeScript 无错误**

```bash
cd /Users/nbe01/workspace/text-image/web
npx tsc --noEmit
```

Expected：无输出。

- [ ] **Step 3：Commit**

```bash
cd /Users/nbe01/workspace/text-image
git add web/src/store/
git commit -m "feat: 添加 Zustand store（runs/nodeStatuses/activeInteraction/drillPath）"
```

---

## Task 4：SSE Hook

**Files:**
- Create: `web/src/hooks/useRunStream.ts`

- [ ] **Step 1：实现 `web/src/hooks/useRunStream.ts`**

```typescript
import { useEffect, useRef } from 'react'
import { useRunStore } from '@/store/runStore'

export function useRunStream(runId: string | null) {
  const { setNodeStatus, setActiveInteraction, upsertRun } = useRunStore()
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!runId) return

    esRef.current?.close()
    const es = new EventSource(`/api/runs/${runId}/stream`)
    esRef.current = es

    es.onmessage = (e) => {
      let event: Record<string, unknown>
      try {
        event = JSON.parse(e.data)
      } catch {
        return
      }

      const type = event.type as string

      if (type === 'node_status') {
        const node = event.node as string
        const status = event.status as string

        if (status === 'waiting_human') {
          setNodeStatus(node, 'waiting_human')
          setActiveInteraction({ node, payload: event.payload })
        } else {
          setNodeStatus(node, status as 'running' | 'done' | 'error')
        }
      }

      if (type === 'run_complete') {
        upsertRun({
          run_id: runId,
          novel_dir: '',
          novel_title: '',
          status: 'done',
          created_at: new Date().toISOString(),
        })
        es.close()
      }

      if (type === 'run_error') {
        upsertRun({
          run_id: runId,
          novel_dir: '',
          novel_title: '',
          status: 'error',
          created_at: new Date().toISOString(),
        })
        es.close()
      }
    }

    es.onerror = () => es.close()

    return () => {
      es.close()
      esRef.current = null
    }
  }, [runId])
}
```

- [ ] **Step 2：验证 TypeScript 无错误**

```bash
cd /Users/nbe01/workspace/text-image/web
npx tsc --noEmit
```

Expected：无输出。

- [ ] **Step 3：Commit**

```bash
cd /Users/nbe01/workspace/text-image
git add web/src/hooks/
git commit -m "feat: 添加 useRunStream SSE hook（订阅节点状态、人工交互事件）"
```

---

## Task 5：自定义 React Flow 节点

**Files:**
- Create: `web/src/components/flow/SubgraphNode.tsx`
- Create: `web/src/components/flow/InternalNode.tsx`

- [ ] **Step 1：实现 `web/src/components/flow/SubgraphNode.tsx`**

```typescript
import { memo } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { useRunStore, type NodeStatus } from '@/store/runStore'
import { cn } from '@/lib/utils'

const STATUS_COLORS: Record<NodeStatus, string> = {
  pending: 'border-gray-300 bg-gray-50',
  running: 'border-blue-400 bg-blue-50 animate-pulse',
  waiting_human: 'border-orange-400 bg-orange-50',
  done: 'border-green-400 bg-green-50',
  error: 'border-red-400 bg-red-50',
}

export interface SubgraphNodeData {
  label: string
  subgraphId: string
}

function SubgraphNode({ data }: NodeProps) {
  const nodeData = data as SubgraphNodeData
  const { nodeStatuses, pushDrill } = useRunStore()
  const status = (nodeStatuses[nodeData.subgraphId] ?? 'pending') as NodeStatus

  return (
    <div
      className={cn(
        'rounded-lg border-2 px-4 py-3 cursor-pointer min-w-[160px] text-center',
        STATUS_COLORS[status]
      )}
      onDoubleClick={() => pushDrill(nodeData.subgraphId)}
    >
      <Handle type="target" position={Position.Left} />
      <div className="font-semibold text-sm">{nodeData.label}</div>
      <div className="text-xs text-gray-500 mt-1">{status}</div>
      <div className="text-xs text-gray-400 mt-1">双击下钻</div>
      <Handle type="source" position={Position.Right} />
    </div>
  )
}

export default memo(SubgraphNode)
```

- [ ] **Step 2：实现 `web/src/components/flow/InternalNode.tsx`**

```typescript
import { memo } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { useRunStore, type NodeStatus } from '@/store/runStore'
import { cn } from '@/lib/utils'

const STATUS_COLORS: Record<NodeStatus, string> = {
  pending: 'border-gray-300 bg-gray-50',
  running: 'border-blue-400 bg-blue-50 animate-pulse',
  waiting_human: 'border-orange-400 bg-orange-50',
  done: 'border-green-400 bg-green-50',
  error: 'border-red-400 bg-red-50',
}

export interface InternalNodeData {
  label: string
  nodeId: string
}

function InternalNode({ data }: NodeProps) {
  const nodeData = data as InternalNodeData
  const { nodeStatuses } = useRunStore()
  const status = (nodeStatuses[nodeData.nodeId] ?? 'pending') as NodeStatus

  return (
    <div
      className={cn(
        'rounded border-2 px-3 py-2 min-w-[140px] text-center',
        STATUS_COLORS[status]
      )}
    >
      <Handle type="target" position={Position.Left} />
      <div className="font-medium text-xs">{nodeData.label}</div>
      <div className="text-xs text-gray-400">{status}</div>
      <Handle type="source" position={Position.Right} />
    </div>
  )
}

export default memo(InternalNode)
```

- [ ] **Step 3：验证 TypeScript 无错误**

```bash
cd /Users/nbe01/workspace/text-image/web
npx tsc --noEmit
```

Expected：无输出。

- [ ] **Step 4：Commit**

```bash
cd /Users/nbe01/workspace/text-image
git add web/src/components/flow/
git commit -m "feat: 添加 SubgraphNode/InternalNode 自定义 React Flow 节点"
```

---

## Task 6：FlowCanvas 画布容器

**Files:**
- Create: `web/src/components/flow/FlowCanvas.tsx`

图结构定义：顶层有两个子图节点（`init_subgraph` → `chapter_loop_subgraph`），下钻后展示该子图内部节点。内部节点布局使用静态坐标（无需自动布局库）。

- [ ] **Step 1：实现 `web/src/components/flow/FlowCanvas.tsx`**

```typescript
import { useCallback } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useRunStore } from '@/store/runStore'
import SubgraphNode from './SubgraphNode'
import InternalNode from './InternalNode'

const nodeTypes = {
  subgraph: SubgraphNode,
  internal: InternalNode,
}

// 顶层图：两个子图节点
const TOP_NODES: Node[] = [
  {
    id: 'init_subgraph',
    type: 'subgraph',
    position: { x: 100, y: 150 },
    data: { label: '角色初始化', subgraphId: 'init_subgraph' },
  },
  {
    id: 'chapter_loop_subgraph',
    type: 'subgraph',
    position: { x: 380, y: 150 },
    data: { label: '章节处理循环', subgraphId: 'chapter_loop_subgraph' },
  },
]

const TOP_EDGES: Edge[] = [
  { id: 'e1', source: 'init_subgraph', target: 'chapter_loop_subgraph' },
]

// chapter_loop_subgraph 内部节点（来自 chapter.py）
const CHAPTER_INTERNAL_NODES: Node[] = [
  { id: 'load_chapter', type: 'internal', position: { x: 50, y: 50 }, data: { label: 'load_chapter', nodeId: 'load_chapter' } },
  { id: 'adapt_script', type: 'internal', position: { x: 250, y: 50 }, data: { label: 'adapt_script', nodeId: 'adapt_script' } },
  { id: 'review_script_llm', type: 'internal', position: { x: 450, y: 50 }, data: { label: 'review_script_llm', nodeId: 'review_script_llm' } },
  { id: 'review_script_human', type: 'internal', position: { x: 650, y: 50 }, data: { label: 'review_script_human', nodeId: 'review_script_human' } },
  { id: 'detect_new_characters', type: 'internal', position: { x: 850, y: 50 }, data: { label: 'detect_new_characters', nodeId: 'detect_new_characters' } },
  { id: 'character_setup_subgraph', type: 'internal', position: { x: 850, y: 180 }, data: { label: 'character_setup_subgraph', nodeId: 'character_setup_subgraph' } },
  { id: 'generate_storyboard', type: 'internal', position: { x: 1050, y: 50 }, data: { label: 'generate_storyboard', nodeId: 'generate_storyboard' } },
  { id: 'generate_images', type: 'internal', position: { x: 1250, y: 50 }, data: { label: 'generate_images', nodeId: 'generate_images' } },
  { id: 'synthesize_audio', type: 'internal', position: { x: 1250, y: 180 }, data: { label: 'synthesize_audio', nodeId: 'synthesize_audio' } },
  { id: 'build_timeline', type: 'internal', position: { x: 1450, y: 50 }, data: { label: 'build_timeline', nodeId: 'build_timeline' } },
  { id: 'human_export_decision', type: 'internal', position: { x: 1650, y: 50 }, data: { label: 'human_export_decision', nodeId: 'human_export_decision' } },
]

const CHAPTER_INTERNAL_EDGES: Edge[] = [
  { id: 'ce1', source: 'load_chapter', target: 'adapt_script' },
  { id: 'ce2', source: 'adapt_script', target: 'review_script_llm' },
  { id: 'ce3', source: 'review_script_llm', target: 'review_script_human' },
  { id: 'ce4', source: 'review_script_human', target: 'detect_new_characters' },
  { id: 'ce5', source: 'detect_new_characters', target: 'character_setup_subgraph' },
  { id: 'ce6', source: 'detect_new_characters', target: 'generate_storyboard' },
  { id: 'ce7', source: 'character_setup_subgraph', target: 'generate_storyboard' },
  { id: 'ce8', source: 'generate_storyboard', target: 'synthesize_audio' },
  { id: 'ce9', source: 'synthesize_audio', target: 'generate_images' },
  { id: 'ce10', source: 'generate_images', target: 'build_timeline' },
  { id: 'ce11', source: 'build_timeline', target: 'human_export_decision' },
]

// init_subgraph 内部节点（来自 setup.py 的角色设定子图）
const SETUP_INTERNAL_NODES: Node[] = [
  { id: 'setup_dispatcher', type: 'internal', position: { x: 50, y: 100 }, data: { label: 'setup_dispatcher', nodeId: 'setup_dispatcher' } },
  { id: 'check_needs_visual', type: 'internal', position: { x: 250, y: 100 }, data: { label: 'check_needs_visual', nodeId: 'check_needs_visual' } },
  { id: 'generate_portrait_candidates', type: 'internal', position: { x: 450, y: 50 }, data: { label: 'generate_portrait', nodeId: 'generate_portrait_candidates' } },
  { id: 'portrait_selector', type: 'internal', position: { x: 650, y: 50 }, data: { label: 'portrait_selector', nodeId: 'portrait_selector' } },
  { id: 'fix_character_visual', type: 'internal', position: { x: 850, y: 50 }, data: { label: 'fix_character_visual', nodeId: 'fix_character_visual' } },
  { id: 'generate_fullbody_candidates', type: 'internal', position: { x: 1050, y: 50 }, data: { label: 'generate_fullbody', nodeId: 'generate_fullbody_candidates' } },
  { id: 'fullbody_selector', type: 'internal', position: { x: 1250, y: 50 }, data: { label: 'fullbody_selector', nodeId: 'fullbody_selector' } },
  { id: 'voice_params_choice', type: 'internal', position: { x: 1450, y: 100 }, data: { label: 'voice_params_choice', nodeId: 'voice_params_choice' } },
  { id: 'voice_card_draw', type: 'internal', position: { x: 1650, y: 50 }, data: { label: 'voice_card_draw', nodeId: 'voice_card_draw' } },
  { id: 'voice_params_manual', type: 'internal', position: { x: 1650, y: 180 }, data: { label: 'voice_params_manual', nodeId: 'voice_params_manual' } },
  { id: 'fix_character_profile', type: 'internal', position: { x: 1850, y: 100 }, data: { label: 'fix_character_profile', nodeId: 'fix_character_profile' } },
]

const SETUP_INTERNAL_EDGES: Edge[] = [
  { id: 'se1', source: 'setup_dispatcher', target: 'check_needs_visual' },
  { id: 'se2', source: 'check_needs_visual', target: 'generate_portrait_candidates' },
  { id: 'se3', source: 'generate_portrait_candidates', target: 'portrait_selector' },
  { id: 'se4', source: 'portrait_selector', target: 'fix_character_visual' },
  { id: 'se5', source: 'fix_character_visual', target: 'generate_fullbody_candidates' },
  { id: 'se6', source: 'generate_fullbody_candidates', target: 'fullbody_selector' },
  { id: 'se7', source: 'fullbody_selector', target: 'voice_params_choice' },
  { id: 'se8', source: 'voice_params_choice', target: 'voice_card_draw' },
  { id: 'se9', source: 'voice_params_choice', target: 'voice_params_manual' },
  { id: 'se10', source: 'voice_card_draw', target: 'fix_character_profile' },
  { id: 'se11', source: 'voice_params_manual', target: 'fix_character_profile' },
  { id: 'se12', source: 'fix_character_profile', target: 'setup_dispatcher' },
]

const DRILL_MAP: Record<string, { nodes: Node[]; edges: Edge[] }> = {
  chapter_loop_subgraph: { nodes: CHAPTER_INTERNAL_NODES, edges: CHAPTER_INTERNAL_EDGES },
  init_subgraph: { nodes: SETUP_INTERNAL_NODES, edges: SETUP_INTERNAL_EDGES },
}

export default function FlowCanvas() {
  const { drillPath, popDrill } = useRunStore()
  const currentSubgraph = drillPath[drillPath.length - 1]

  const { nodes, edges } = currentSubgraph
    ? (DRILL_MAP[currentSubgraph] ?? { nodes: [], edges: [] })
    : { nodes: TOP_NODES, edges: TOP_EDGES }

  return (
    <div className="relative w-full h-full">
      {drillPath.length > 0 && (
        <div className="absolute top-3 left-3 z-10 flex items-center gap-2 bg-white rounded shadow px-3 py-1 text-sm">
          <button onClick={popDrill} className="text-blue-600 hover:underline">
            ← 返回
          </button>
          <span className="text-gray-400">/</span>
          <span>{currentSubgraph}</span>
        </div>
      )}
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  )
}
```

- [ ] **Step 2：验证 TypeScript 无错误**

```bash
cd /Users/nbe01/workspace/text-image/web
npx tsc --noEmit
```

Expected：无输出。

- [ ] **Step 3：Commit**

```bash
cd /Users/nbe01/workspace/text-image
git add web/src/components/flow/FlowCanvas.tsx
git commit -m "feat: 添加 FlowCanvas（顶层子图视图 + 下钻切换，面包屑导航）"
```

---

## Task 7：布局组件与页面骨架

**Files:**
- Create: `web/src/components/layout/Sidebar.tsx`
- Create: `web/src/components/layout/MainContent.tsx`
- Create: `web/src/pages/RunPage.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/main.tsx`

- [ ] **Step 1：安装 shadcn Badge 和 Button 组件**

```bash
cd /Users/nbe01/workspace/text-image/web
npx shadcn@latest add badge button separator
```

Expected：`src/components/ui/badge.tsx`、`button.tsx`、`separator.tsx` 生成。

- [ ] **Step 2：实现 `web/src/components/layout/Sidebar.tsx`**

```typescript
import { useEffect } from 'react'
import { useRunStore } from '@/store/runStore'
import { api } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const STATUS_BADGE: Record<string, string> = {
  pending: 'bg-gray-100 text-gray-700',
  running: 'bg-blue-100 text-blue-700',
  waiting_human: 'bg-orange-100 text-orange-700',
  done: 'bg-green-100 text-green-700',
  error: 'bg-red-100 text-red-700',
}

interface SidebarProps {
  onNewRun: () => void
}

export default function Sidebar({ onNewRun }: SidebarProps) {
  const { runs, currentRunId, setRuns, setCurrentRunId, resetNodeStatuses, resetDrill } = useRunStore()

  useEffect(() => {
    api.listRuns().then(setRuns).catch(console.error)
  }, [])

  const handleSelectRun = (runId: string) => {
    setCurrentRunId(runId)
    resetNodeStatuses()
    resetDrill()
  }

  const sorted = Object.values(runs).sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  )

  return (
    <aside className="w-56 border-r flex flex-col h-full">
      <div className="p-3 border-b">
        <Button className="w-full" size="sm" onClick={onNewRun}>
          + 新建 Run
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto">
        {sorted.map((run) => (
          <div
            key={run.run_id}
            className={cn(
              'px-3 py-2 cursor-pointer hover:bg-gray-50 border-b',
              currentRunId === run.run_id && 'bg-blue-50'
            )}
            onClick={() => handleSelectRun(run.run_id)}
          >
            <div className="text-sm font-medium truncate">
              {run.novel_title || run.run_id.slice(0, 8)}
            </div>
            <Badge className={cn('text-xs mt-1', STATUS_BADGE[run.status])}>
              {run.status}
            </Badge>
          </div>
        ))}
      </div>
    </aside>
  )
}
```

- [ ] **Step 3：实现 `web/src/components/layout/MainContent.tsx`**

```typescript
import FlowCanvas from '@/components/flow/FlowCanvas'
import { useRunStore } from '@/store/runStore'
import { useRunStream } from '@/hooks/useRunStream'

interface MainContentProps {
  showNewRunForm: boolean
  newRunFormSlot: React.ReactNode
}

export default function MainContent({ showNewRunForm, newRunFormSlot }: MainContentProps) {
  const { currentRunId } = useRunStore()
  useRunStream(currentRunId)

  if (showNewRunForm) {
    return (
      <main className="flex-1 flex items-center justify-center bg-gray-50">
        {newRunFormSlot}
      </main>
    )
  }

  if (!currentRunId) {
    return (
      <main className="flex-1 flex items-center justify-center bg-gray-50 text-gray-400">
        请从左侧选择一个 Run，或新建 Run
      </main>
    )
  }

  return (
    <main className="flex-1 relative">
      <FlowCanvas />
    </main>
  )
}
```

- [ ] **Step 4：实现 `web/src/pages/RunPage.tsx`**

```typescript
import { useState } from 'react'
import Sidebar from '@/components/layout/Sidebar'
import MainContent from '@/components/layout/MainContent'

export default function RunPage() {
  const [showNewRunForm, setShowNewRunForm] = useState(false)

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar onNewRun={() => setShowNewRunForm(true)} />
      <MainContent
        showNewRunForm={showNewRunForm}
        newRunFormSlot={
          <div className="text-gray-400">
            启动配置表单将在 Plan E 实现
          </div>
        }
      />
    </div>
  )
}
```

- [ ] **Step 5：更新 `web/src/App.tsx`**

```typescript
import RunPage from '@/pages/RunPage'

export default function App() {
  return <RunPage />
}
```

- [ ] **Step 6：更新 `web/src/main.tsx`**

```typescript
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
```

- [ ] **Step 7：验证 TypeScript 无错误**

```bash
cd /Users/nbe01/workspace/text-image/web
npx tsc --noEmit
```

Expected：无输出。

- [ ] **Step 8：在浏览器验证 UI**

```bash
cd /Users/nbe01/workspace/text-image/web
npm run dev
```

打开 `http://localhost:5173`，验证：
- 左侧侧边栏显示"+ 新建 Run"按钮
- 主内容区显示空状态提示"请从左侧选择一个 Run"
- 点击"+ 新建 Run"切换到表单占位区
- 如果后端已启动（Plan C），历史 Runs 能从 `GET /api/runs` 加载并显示

- [ ] **Step 9：Commit**

```bash
cd /Users/nbe01/workspace/text-image
git add web/src/
git commit -m "feat: 添加页面骨架（Sidebar/MainContent/RunPage）和 React Flow 画布集成"
```

---

## Task 8：构建验证

- [ ] **Step 1：生产构建检查**

```bash
cd /Users/nbe01/workspace/text-image/web
npm run build
```

Expected：`dist/` 目录生成，无 TypeScript/构建错误。

- [ ] **Step 2：验证 `npm run lint`（如脚手架带 ESLint）**

```bash
npm run lint
```

Expected：无错误（可有 warning）。

- [ ] **Step 3：Commit**

```bash
cd /Users/nbe01/workspace/text-image
git add web/
git commit -m "chore: 前端构建验证通过"
```

---

## Plan D 完成检查清单

- [ ] `npx tsc --noEmit` 无 TypeScript 错误
- [ ] `npm run build` 构建成功
- [ ] 浏览器打开 `http://localhost:5173` 能看到左侧历史栏 + 主内容区布局
- [ ] 点击"+ 新建 Run"切换到表单占位区
- [ ] 如后端启动，左侧历史栏能加载 runs 列表
- [ ] 选中 Run 后，React Flow 画布展示顶层两个子图节点
- [ ] 双击子图节点，下钻到内部节点视图，面包屑"← 返回"可返回顶层
- [ ] 节点颜色随 `nodeStatuses` 变化（需结合 SSE 验证）
