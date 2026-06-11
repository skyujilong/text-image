# 交互面板层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现所有人工干预交互组件：PortraitSelector / FullbodySelector 图片选择抽屉、VoiceCardDraw 语音选择抽屉、VoiceParamsManual 参数表单抽屉、NewCharacterDecision 角色决策抽屉，以及启动 Run 的配置表单。

**Architecture:** 所有侧边抽屉由 Zustand `activeInteraction.node` 驱动，在 `RunPage` 中集中条件渲染；各抽屉通过 `api.resumeRun()` 提交后清除 `activeInteraction`；启动配置表单集成 react-hook-form + zod，使用 `api.validatePath()` 做路径实时校验，使用 `api.getNovelConfig()` 自动填充字段。

**Tech Stack:** React, shadcn/ui (Sheet/Dialog/Button/Input/Textarea/Form), react-hook-form, zod, @xyflow/react（已在 Plan D 安装）

**前置条件:**
- Plan C（FastAPI 后端）已完成
- Plan D（React Flow 画布）已完成：Zustand store、`useRunStream` hook、页面骨架已就位

---

## 文件结构

```
web/src/
├── components/
│   └── panels/
│       ├── InteractionDispatcher.tsx    # 根据 activeInteraction.node 渲染对应抽屉
│       ├── PortraitSelector.tsx         # portrait_selector 抽屉
│       ├── FullbodySelector.tsx         # fullbody_selector 抽屉
│       ├── VoiceCardDraw.tsx            # voice_card_draw 抽屉
│       ├── VoiceParamsManual.tsx        # voice_params_manual 抽屉
│       └── NewCharacterDecision.tsx     # detect_new_characters 抽屉
└── components/
    └── forms/
        └── StartRunForm.tsx             # 启动 Run 配置表单
```

---

## Task 1：安装 shadcn/ui 交互组件

**Files:** 无（仅安装）

- [ ] **Step 1：安装 Sheet、Dialog、Form 等组件**

```bash
cd /Users/nbe01/workspace/text-image/web
npx shadcn@latest add sheet dialog form input textarea label select card
```

Expected：`src/components/ui/` 下生成对应文件。

- [ ] **Step 2：验证 TypeScript 无错误**

```bash
npx tsc --noEmit
```

Expected：无输出。

- [ ] **Step 3：Commit**

```bash
cd /Users/nbe01/workspace/text-image
git add web/src/components/ui/
git commit -m "chore: 安装 shadcn/ui Sheet/Dialog/Form/Input/Textarea 组件"
```

---

## Task 2：PortraitSelector 抽屉

**Files:**
- Create: `web/src/components/panels/PortraitSelector.tsx`

`portrait_selector` 节点的 interrupt payload 格式：
```json
{ "candidates": ["path/to/img1.png", "path/to/img2.png"], "type": "portrait_selection" }
```
resume value：`int`（所选图片的索引）

- [ ] **Step 1：实现 `web/src/components/panels/PortraitSelector.tsx`**

```typescript
import { useState } from 'react'
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetFooter,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'
import { cn } from '@/lib/utils'

interface Props {
  runId: string
  candidates: string[]
  open: boolean
  onClose: () => void
}

export default function PortraitSelector({ runId, candidates, open, onClose }: Props) {
  const [selected, setSelected] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const { setActiveInteraction } = useRunStore()

  const handleConfirm = async () => {
    if (selected === null) return
    setLoading(true)
    try {
      await api.resumeRun(runId, selected)
      setActiveInteraction(null)
      onClose()
    } catch (e) {
      console.error('resume failed', e)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-[480px] sm:max-w-[480px]">
        <SheetHeader>
          <SheetTitle>选择头像（portrait_selector）</SheetTitle>
        </SheetHeader>

        <div className="grid grid-cols-2 gap-3 py-4 overflow-y-auto max-h-[70vh]">
          {candidates.map((src, i) => (
            <div
              key={i}
              className={cn(
                'border-2 rounded cursor-pointer overflow-hidden',
                selected === i ? 'border-blue-500' : 'border-gray-200 hover:border-gray-400'
              )}
              onClick={() => setSelected(i)}
            >
              <img
                src={`/api/files/${encodeURIComponent(src)}`}
                alt={`候选头像 ${i + 1}`}
                className="w-full h-auto object-cover"
                onError={(e) => {
                  (e.target as HTMLImageElement).src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg"/>'
                }}
              />
              <div className="text-xs text-center py-1 bg-gray-50">
                {selected === i ? '✓ 已选' : `图 ${i + 1}`}
              </div>
            </div>
          ))}
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={onClose} disabled={loading}>
            取消
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={selected === null || loading}
          >
            {loading ? '提交中...' : '确认选择'}
          </Button>
          {/* 重新生成：依赖后端先为 portrait_selector 补充条件回路，当前置灰 */}
          <Button variant="secondary" disabled title="后端尚未支持重新生成">
            重新生成
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
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
git add web/src/components/panels/PortraitSelector.tsx
git commit -m "feat: 添加 PortraitSelector 抽屉（图片选择 + 索引 resume）"
```

---

## Task 3：FullbodySelector 抽屉

**Files:**
- Create: `web/src/components/panels/FullbodySelector.tsx`

payload 格式与 PortraitSelector 相同，resume value 为 `int`（索引）。

- [ ] **Step 1：实现 `web/src/components/panels/FullbodySelector.tsx`**

```typescript
import { useState } from 'react'
import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetFooter,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'
import { cn } from '@/lib/utils'

interface Props {
  runId: string
  candidates: string[]
  open: boolean
  onClose: () => void
}

export default function FullbodySelector({ runId, candidates, open, onClose }: Props) {
  const [selected, setSelected] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const { setActiveInteraction } = useRunStore()

  const handleConfirm = async () => {
    if (selected === null) return
    setLoading(true)
    try {
      await api.resumeRun(runId, selected)
      setActiveInteraction(null)
      onClose()
    } catch (e) {
      console.error('resume failed', e)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-[560px] sm:max-w-[560px]">
        <SheetHeader>
          <SheetTitle>选择全身立绘（fullbody_selector）</SheetTitle>
        </SheetHeader>

        <div className="grid grid-cols-2 gap-3 py-4 overflow-y-auto max-h-[70vh]">
          {candidates.map((src, i) => (
            <div
              key={i}
              className={cn(
                'border-2 rounded cursor-pointer overflow-hidden',
                selected === i ? 'border-blue-500' : 'border-gray-200 hover:border-gray-400'
              )}
              onClick={() => setSelected(i)}
            >
              <img
                src={`/api/files/${encodeURIComponent(src)}`}
                alt={`候选立绘 ${i + 1}`}
                className="w-full h-auto object-cover"
                onError={(e) => {
                  (e.target as HTMLImageElement).src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg"/>'
                }}
              />
              <div className="text-xs text-center py-1 bg-gray-50">
                {selected === i ? '✓ 已选' : `图 ${i + 1}`}
              </div>
            </div>
          ))}
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={onClose} disabled={loading}>
            取消
          </Button>
          <Button onClick={handleConfirm} disabled={selected === null || loading}>
            {loading ? '提交中...' : '确认选择'}
          </Button>
          {/* 重新生成：依赖后端先为 fullbody_selector 补充条件回路，当前置灰 */}
          <Button variant="secondary" disabled title="后端尚未支持重新生成">
            重新生成
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
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
git add web/src/components/panels/FullbodySelector.tsx
git commit -m "feat: 添加 FullbodySelector 抽屉"
```

---

## Task 4：VoiceCardDraw 抽屉

**Files:**
- Create: `web/src/components/panels/VoiceCardDraw.tsx`

`voice_card_draw` 节点的 interrupt payload 格式（后端占位，字段待后端确认后可调整）：
```json
{
  "candidates": [
    { "index": 0, "seed": 123, "label": "音色A", "sample_path": "path/to/sample.wav" }
  ],
  "type": "voice_card_draw"
}
```
resume value：`int`（所选 index），全部拒绝时 resume 特殊标记（由后端节点定义，此处暂用 `-1`）。

- [ ] **Step 1：实现 `web/src/components/panels/VoiceCardDraw.tsx`**

```typescript
import { useState } from 'react'
import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetFooter,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'
import { cn } from '@/lib/utils'

interface VoiceCandidate {
  index: number
  seed: number
  label: string
  sample_path?: string
}

interface Props {
  runId: string
  candidates: VoiceCandidate[]
  open: boolean
  onClose: () => void
}

export default function VoiceCardDraw({ runId, candidates, open, onClose }: Props) {
  const [selected, setSelected] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const { setActiveInteraction } = useRunStore()

  const handleConfirm = async (resumeValue: number) => {
    setLoading(true)
    try {
      await api.resumeRun(runId, resumeValue)
      setActiveInteraction(null)
      onClose()
    } catch (e) {
      console.error('resume failed', e)
    } finally {
      setLoading(false)
    }
  }

  const handleRejectAll = () => handleConfirm(-1)  // -1 = 全部拒绝，后端 voice_card_draw 重抽

  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-[440px] sm:max-w-[440px]">
        <SheetHeader>
          <SheetTitle>选择语音音色（voice_card_draw）</SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-3 py-4 overflow-y-auto max-h-[70vh]">
          {candidates.map((c) => (
            <div
              key={c.index}
              className={cn(
                'border-2 rounded p-3 cursor-pointer',
                selected === c.index ? 'border-blue-500 bg-blue-50' : 'border-gray-200 hover:border-gray-400'
              )}
              onClick={() => setSelected(c.index)}
            >
              <div className="font-medium text-sm">{c.label}</div>
              <div className="text-xs text-gray-400">seed: {c.seed}</div>
              {c.sample_path && (
                <audio
                  controls
                  src={`/api/files/${encodeURIComponent(c.sample_path)}`}
                  className="mt-2 w-full h-8"
                  onClick={(e) => e.stopPropagation()}
                />
              )}
            </div>
          ))}
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={handleRejectAll} disabled={loading}>
            全部拒绝（重抽）
          </Button>
          <Button
            onClick={() => selected !== null && handleConfirm(selected)}
            disabled={selected === null || loading}
          >
            {loading ? '提交中...' : '确认选择'}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
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
git add web/src/components/panels/VoiceCardDraw.tsx
git commit -m "feat: 添加 VoiceCardDraw 抽屉（试听 + 确认 + 全部拒绝重抽）"
```

---

## Task 5：VoiceParamsManual 抽屉

**Files:**
- Create: `web/src/components/panels/VoiceParamsManual.tsx`

`voice_params_manual` 节点的 interrupt payload（后端占位，字段以当前已知参数推测）：
```json
{ "type": "voice_params_manual", "current_params": { "speed": 1.0, "pitch": 0, "temperature": 0.3 } }
```
resume value：`{ speed: number, pitch: number, temperature: number }`

- [ ] **Step 1：实现 `web/src/components/panels/VoiceParamsManual.tsx`**

```typescript
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetFooter,
} from '@/components/ui/sheet'
import {
  Form, FormControl, FormField, FormItem, FormLabel, FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

const schema = z.object({
  speed: z.coerce.number().min(0.5).max(2.0),
  pitch: z.coerce.number().min(-12).max(12),
  temperature: z.coerce.number().min(0).max(1),
})

type FormValues = z.infer<typeof schema>

interface Props {
  runId: string
  currentParams?: Partial<FormValues>
  open: boolean
  onClose: () => void
}

export default function VoiceParamsManual({ runId, currentParams, open, onClose }: Props) {
  const { setActiveInteraction } = useRunStore()
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      speed: currentParams?.speed ?? 1.0,
      pitch: currentParams?.pitch ?? 0,
      temperature: currentParams?.temperature ?? 0.3,
    },
  })

  const onSubmit = async (values: FormValues) => {
    try {
      await api.resumeRun(runId, values)
      setActiveInteraction(null)
      onClose()
    } catch (e) {
      console.error('resume failed', e)
    }
  }

  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-[400px] sm:max-w-[400px]">
        <SheetHeader>
          <SheetTitle>语音参数设置（voice_params_manual）</SheetTitle>
        </SheetHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4 py-4">
            <FormField
              control={form.control}
              name="speed"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>语速（0.5 ~ 2.0）</FormLabel>
                  <FormControl>
                    <Input type="number" step="0.1" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="pitch"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>音调（-12 ~ 12）</FormLabel>
                  <FormControl>
                    <Input type="number" step="1" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="temperature"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>随机度（0 ~ 1）</FormLabel>
                  <FormControl>
                    <Input type="number" step="0.05" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <SheetFooter>
              <Button type="button" variant="outline" onClick={onClose}>
                取消
              </Button>
              <Button type="submit" disabled={form.formState.isSubmitting}>
                {form.formState.isSubmitting ? '提交中...' : '确认'}
              </Button>
            </SheetFooter>
          </form>
        </Form>
      </SheetContent>
    </Sheet>
  )
}
```

- [ ] **Step 2：安装 `@hookform/resolvers`**

```bash
cd /Users/nbe01/workspace/text-image/web
npm install @hookform/resolvers
```

- [ ] **Step 3：验证 TypeScript 无错误**

```bash
npx tsc --noEmit
```

Expected：无输出。

- [ ] **Step 4：Commit**

```bash
cd /Users/nbe01/workspace/text-image
git add web/src/components/panels/VoiceParamsManual.tsx web/package.json web/package-lock.json
git commit -m "feat: 添加 VoiceParamsManual 抽屉（zod 表单验证）"
```

---

## Task 6：NewCharacterDecision 抽屉

**Files:**
- Create: `web/src/components/panels/NewCharacterDecision.tsx`

`detect_new_characters` 节点的 interrupt payload（后端占位）：
```json
{
  "type": "new_character_decision",
  "pending_characters": [
    { "name": "李白", "first_appearance": "第3章" },
    { "name": "杜甫", "first_appearance": "第3章" }
  ]
}
```
resume value：`{ decisions: Record<string, "keep" | "ignore"> }`

- [ ] **Step 1：实现 `web/src/components/panels/NewCharacterDecision.tsx`**

```typescript
import { useState } from 'react'
import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetFooter,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'
import { cn } from '@/lib/utils'

interface PendingCharacter {
  name: string
  first_appearance: string
}

interface Props {
  runId: string
  pendingCharacters: PendingCharacter[]
  open: boolean
  onClose: () => void
}

type Decision = 'keep' | 'ignore'

export default function NewCharacterDecision({ runId, pendingCharacters, open, onClose }: Props) {
  const [decisions, setDecisions] = useState<Record<string, Decision>>(() =>
    Object.fromEntries(pendingCharacters.map((c) => [c.name, 'keep']))
  )
  const [loading, setLoading] = useState(false)
  const { setActiveInteraction } = useRunStore()

  const toggle = (name: string) =>
    setDecisions((d) => ({ ...d, [name]: d[name] === 'keep' ? 'ignore' : 'keep' }))

  const handleConfirm = async () => {
    setLoading(true)
    try {
      await api.resumeRun(runId, { decisions })
      setActiveInteraction(null)
      onClose()
    } catch (e) {
      console.error('resume failed', e)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-[420px] sm:max-w-[420px]">
        <SheetHeader>
          <SheetTitle>新角色决策（detect_new_characters）</SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-3 py-4 overflow-y-auto max-h-[70vh]">
          {pendingCharacters.map((c) => (
            <div
              key={c.name}
              className="flex items-center justify-between border rounded px-3 py-2"
            >
              <div>
                <div className="font-medium text-sm">{c.name}</div>
                <div className="text-xs text-gray-400">首次出现：{c.first_appearance}</div>
              </div>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant={decisions[c.name] === 'keep' ? 'default' : 'outline'}
                  onClick={() => setDecisions((d) => ({ ...d, [c.name]: 'keep' }))}
                >
                  保留
                </Button>
                <Button
                  size="sm"
                  variant={decisions[c.name] === 'ignore' ? 'destructive' : 'outline'}
                  onClick={() => setDecisions((d) => ({ ...d, [c.name]: 'ignore' }))}
                >
                  忽略
                </Button>
              </div>
            </div>
          ))}
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={onClose} disabled={loading}>
            取消
          </Button>
          <Button onClick={handleConfirm} disabled={loading}>
            {loading ? '提交中...' : '确认'}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
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
git add web/src/components/panels/NewCharacterDecision.tsx
git commit -m "feat: 添加 NewCharacterDecision 抽屉（保留/忽略新角色决策）"
```

---

## Task 7：InteractionDispatcher

**Files:**
- Create: `web/src/components/panels/InteractionDispatcher.tsx`
- Modify: `web/src/pages/RunPage.tsx`

根据 `activeInteraction.node` 条件渲染对应抽屉。

- [ ] **Step 1：实现 `web/src/components/panels/InteractionDispatcher.tsx`**

```typescript
import { useRunStore } from '@/store/runStore'
import PortraitSelector from './PortraitSelector'
import FullbodySelector from './FullbodySelector'
import VoiceCardDraw from './VoiceCardDraw'
import VoiceParamsManual from './VoiceParamsManual'
import NewCharacterDecision from './NewCharacterDecision'

interface Props {
  runId: string
}

export default function InteractionDispatcher({ runId }: Props) {
  const { activeInteraction, setActiveInteraction } = useRunStore()

  if (!activeInteraction) return null

  const { node, payload } = activeInteraction
  const p = payload as Record<string, unknown>
  const onClose = () => setActiveInteraction(null)

  if (node === 'portrait_selector') {
    return (
      <PortraitSelector
        runId={runId}
        candidates={(p.candidates as string[]) ?? []}
        open
        onClose={onClose}
      />
    )
  }

  if (node === 'fullbody_selector') {
    return (
      <FullbodySelector
        runId={runId}
        candidates={(p.candidates as string[]) ?? []}
        open
        onClose={onClose}
      />
    )
  }

  if (node === 'voice_card_draw') {
    return (
      <VoiceCardDraw
        runId={runId}
        candidates={(p.candidates as Parameters<typeof VoiceCardDraw>[0]['candidates']) ?? []}
        open
        onClose={onClose}
      />
    )
  }

  if (node === 'voice_params_manual') {
    return (
      <VoiceParamsManual
        runId={runId}
        currentParams={p.current_params as Parameters<typeof VoiceParamsManual>[0]['currentParams']}
        open
        onClose={onClose}
      />
    )
  }

  if (node === 'detect_new_characters') {
    return (
      <NewCharacterDecision
        runId={runId}
        pendingCharacters={(p.pending_characters as Parameters<typeof NewCharacterDecision>[0]['pendingCharacters']) ?? []}
        open
        onClose={onClose}
      />
    )
  }

  return null
}
```

- [ ] **Step 2：更新 `web/src/pages/RunPage.tsx` 挂载 dispatcher**

将 `RunPage.tsx` 替换为：

```typescript
import { useState } from 'react'
import Sidebar from '@/components/layout/Sidebar'
import MainContent from '@/components/layout/MainContent'
import InteractionDispatcher from '@/components/panels/InteractionDispatcher'
import { useRunStore } from '@/store/runStore'

export default function RunPage() {
  const [showNewRunForm, setShowNewRunForm] = useState(false)
  const { currentRunId } = useRunStore()

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar onNewRun={() => setShowNewRunForm(true)} />
      <MainContent
        showNewRunForm={showNewRunForm}
        newRunFormSlot={
          <div className="text-gray-400">
            启动配置表单将在 Task 8 实现
          </div>
        }
      />
      {currentRunId && <InteractionDispatcher runId={currentRunId} />}
    </div>
  )
}
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
git add web/src/components/panels/InteractionDispatcher.tsx web/src/pages/RunPage.tsx
git commit -m "feat: 添加 InteractionDispatcher，RunPage 挂载抽屉调度器"
```

---

## Task 8：启动配置表单

**Files:**
- Create: `web/src/components/forms/StartRunForm.tsx`
- Modify: `web/src/pages/RunPage.tsx`

- [ ] **Step 1：实现 `web/src/components/forms/StartRunForm.tsx`**

```typescript
import { useEffect } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import {
  Form, FormControl, FormField, FormItem, FormLabel, FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

const schema = z.object({
  novel_dir: z.string().min(1, '请输入小说目录').refine(
    async (dir) => {
      if (!dir) return false
      const res = await api.validatePath(dir)
      return res.exists
    },
    { message: '目录不存在' }
  ),
  novel_title: z.string().default(''),
  worldview: z.string().default(''),
  start_chapter: z.coerce.number().int().min(1).default(1),
  end_chapter: z.coerce.number().int().min(1).optional().nullable(),
})

type FormValues = z.infer<typeof schema>

interface Props {
  onStarted: (runId: string) => void
  onCancel: () => void
}

export default function StartRunForm({ onStarted, onCancel }: Props) {
  const { upsertRun, setCurrentRunId, resetNodeStatuses, resetDrill } = useRunStore()
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    mode: 'onBlur',
    defaultValues: {
      novel_dir: '',
      novel_title: '',
      worldview: '',
      start_chapter: 1,
      end_chapter: null,
    },
  })

  const novelDir = form.watch('novel_dir')
  const dirValid = form.formState.dirtyFields.novel_dir && !form.formState.errors.novel_dir

  // 目录有效后自动加载配置
  useEffect(() => {
    if (!dirValid || !novelDir) return
    api.getNovelConfig(novelDir)
      .then((cfg) => {
        if (cfg.novel_title) form.setValue('novel_title', cfg.novel_title as string)
        if (cfg.worldview) form.setValue('worldview', cfg.worldview as string)
      })
      .catch(() => {/* 目录存在但无 novel.json，忽略 */})
  }, [dirValid, novelDir])

  const onSubmit = async (values: FormValues) => {
    const { run_id } = await api.startRun({
      novel_dir: values.novel_dir,
      novel_title: values.novel_title,
      worldview: values.worldview,
      start_chapter: values.start_chapter,
      end_chapter: values.end_chapter ?? undefined,
    })
    upsertRun({
      run_id,
      novel_dir: values.novel_dir,
      novel_title: values.novel_title || run_id.slice(0, 8),
      status: 'pending',
      created_at: new Date().toISOString(),
    })
    setCurrentRunId(run_id)
    resetNodeStatuses()
    resetDrill()
    onStarted(run_id)
  }

  const configDisabled = !dirValid

  return (
    <div className="w-full max-w-lg p-6 bg-white rounded-xl shadow">
      <h2 className="text-lg font-semibold mb-4">新建 Run</h2>
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
          <FormField
            control={form.control}
            name="novel_dir"
            render={({ field }) => (
              <FormItem>
                <FormLabel>小说目录</FormLabel>
                <FormControl>
                  <Input placeholder="/path/to/your/novel" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="novel_title"
            render={({ field }) => (
              <FormItem>
                <FormLabel>小说标题</FormLabel>
                <FormControl>
                  <Input disabled={configDisabled} placeholder="（选择目录后自动填充）" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="worldview"
            render={({ field }) => (
              <FormItem>
                <FormLabel>世界观设定</FormLabel>
                <FormControl>
                  <Textarea
                    disabled={configDisabled}
                    placeholder="（选择目录后自动填充）"
                    rows={4}
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <div className="flex gap-4">
            <FormField
              control={form.control}
              name="start_chapter"
              render={({ field }) => (
                <FormItem className="flex-1">
                  <FormLabel>起始章节</FormLabel>
                  <FormControl>
                    <Input type="number" min={1} disabled={configDisabled} {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="end_chapter"
              render={({ field }) => (
                <FormItem className="flex-1">
                  <FormLabel>结束章节（留空=全部）</FormLabel>
                  <FormControl>
                    <Input
                      type="number"
                      min={1}
                      disabled={configDisabled}
                      {...field}
                      value={field.value ?? ''}
                      onChange={(e) => field.onChange(e.target.value ? Number(e.target.value) : null)}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <Button type="button" variant="outline" onClick={onCancel}>
              取消
            </Button>
            <Button type="submit" disabled={form.formState.isSubmitting || configDisabled}>
              {form.formState.isSubmitting ? '启动中...' : '开始运行 →'}
            </Button>
          </div>
        </form>
      </Form>
    </div>
  )
}
```

- [ ] **Step 2：更新 `web/src/pages/RunPage.tsx` 接入真实表单**

```typescript
import { useState } from 'react'
import Sidebar from '@/components/layout/Sidebar'
import MainContent from '@/components/layout/MainContent'
import InteractionDispatcher from '@/components/panels/InteractionDispatcher'
import StartRunForm from '@/components/forms/StartRunForm'
import { useRunStore } from '@/store/runStore'

export default function RunPage() {
  const [showNewRunForm, setShowNewRunForm] = useState(false)
  const { currentRunId } = useRunStore()

  const handleStarted = (runId: string) => {
    setShowNewRunForm(false)
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar onNewRun={() => setShowNewRunForm(true)} />
      <MainContent
        showNewRunForm={showNewRunForm}
        newRunFormSlot={
          <StartRunForm
            onStarted={handleStarted}
            onCancel={() => setShowNewRunForm(false)}
          />
        }
      />
      {currentRunId && <InteractionDispatcher runId={currentRunId} />}
    </div>
  )
}
```

- [ ] **Step 3：验证 TypeScript 无错误**

```bash
cd /Users/nbe01/workspace/text-image/web
npx tsc --noEmit
```

Expected：无输出。

- [ ] **Step 4：在浏览器做端到端验证（后端已启动）**

```bash
# 终端 1：启动后端
cd /Users/nbe01/workspace/text-image
uv run uvicorn api.main:app --reload --port 8000

# 终端 2：启动前端
cd web
npm run dev
```

验证流程：
1. 打开 `http://localhost:5173`
2. 点击"+ 新建 Run"，表单显示
3. 输入一个**存在的**本地目录路径，失焦后"小说标题"字段应自动填充（如目录有 `config/novel.json`）
4. 输入一个**不存在**的路径，失焦后显示"目录不存在"验证错误
5. 填写有效信息，点击"开始运行 →"，左侧历史栏出现新 Run 条目，主区切换到 React Flow 视图

- [ ] **Step 5：Commit**

```bash
cd /Users/nbe01/workspace/text-image
git add web/src/components/forms/ web/src/pages/RunPage.tsx
git commit -m "feat: 添加 StartRunForm 启动配置表单（zod 验证 + 目录自动填充）"
```

---

## Task 9：生产构建验证

- [ ] **Step 1：生产构建**

```bash
cd /Users/nbe01/workspace/text-image/web
npm run build
```

Expected：`dist/` 生成，无 TypeScript/构建错误。

- [ ] **Step 2：验证 FastAPI 能 serve 静态文件（可选，Plan C 未包含）**

在 `api/main.py` 末尾追加（确认 `dist/` 存在后执行）：

```python
from fastapi.staticfiles import StaticFiles
import os

_DIST = os.path.join(os.path.dirname(__file__), '..', 'web', 'dist')
if os.path.exists(_DIST):
    app.mount('/', StaticFiles(directory=_DIST, html=True), name='static')
```

- [ ] **Step 3：最终 Commit**

```bash
cd /Users/nbe01/workspace/text-image
git add web/ api/main.py
git commit -m "feat: 前端构建验证通过，FastAPI 挂载 web/dist 静态文件"
```

---

## Plan E 完成检查清单

- [ ] `npx tsc --noEmit` 无 TypeScript 错误
- [ ] `npm run build` 构建成功
- [ ] 浏览器打开，填写有效目录路径，表单字段自动填充
- [ ] 无效路径显示"目录不存在"验证错误
- [ ] 提交表单后，左侧历史栏新增条目，主区切换到 React Flow
- [ ] SSE 连接建立后，节点颜色随执行状态变化
- [ ] 当 `portrait_selector` 触发 interrupt，PortraitSelector 抽屉自动弹出
- [ ] 选择图片后确认，抽屉关闭，resume 调用后节点继续执行
- [ ] `VoiceCardDraw` 试听按钮通过 `/api/files/` 播放音频
- [ ] `NewCharacterDecision` 每角色可独立保留/忽略
