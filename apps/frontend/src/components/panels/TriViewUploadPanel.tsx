import { useState } from 'react'
import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetFooter,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

interface Character {
  name?: string
  appearance?: string
  tri_view_prompt?: string
}
interface Props {
  runId: string
  characters: Character[]
  open: boolean
  onClose: () => void
}

interface RowState {
  file: File | null
  skipped: boolean
  uploadedPath?: string
  uploading: boolean
}

/**
 * 批量三视图上传面板：一次为全部角色上传三视图 → 逐个 POST /upload 收集本地相对路径
 * → resume {tri_views: {name: path}, skipped: [name,...]}。
 * 每个角色可二选一：选文件上传 或 勾选跳过（小角色）。未决角色会禁用提交。
 * 实际上传在 API 层完成（R1：batch_upload_tri_view 节点零副作用，且不调 ComfyUI，推迟到渲染阶段）。
 */
export default function TriViewUploadPanel({ runId, characters, open, onClose }: Props) {
  const { setActiveInteraction } = useRunStore()
  const [rows, setRows] = useState<Record<string, RowState>>(() => {
    const init: Record<string, RowState> = {}
    for (const c of characters) {
      init[c.name ?? '未命名角色'] = { file: null, skipped: false, uploading: false }
    }
    return init
  })
  const [submitting, setSubmitting] = useState(false)

  const setRow = (name: string, patch: Partial<RowState>) =>
    setRows((prev) => ({ ...prev, [name]: { ...prev[name], ...patch } }))

  const allResolved = characters.every((c) => {
    const name = c.name ?? '未命名角色'
    const row = rows[name]
    return row && (row.file || row.skipped)
  })

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      const tri_views: Record<string, string> = {}
      const skipped: string[] = []
      for (const c of characters) {
        const name = c.name ?? '未命名角色'
        const row = rows[name]
        if (row.skipped) {
          skipped.push(name)
          continue
        }
        if (!row.file) continue
        setRow(name, { uploading: true })
        const { path } = await api.uploadFile(runId, row.file, 'characters', name)
        tri_views[name] = path
        setRow(name, { uploading: false, uploadedPath: path })
      }
      await api.resumeRun(runId, { tri_views, skipped })
      setActiveInteraction(null)
      onClose()
    } catch (e) {
      console.error('upload/resume failed', e)
    } finally {
      setSubmitting(false)
    }
  }

  const handleSkipAll = async () => {
    setSubmitting(true)
    try {
      const skipped = characters.map((c) => c.name ?? '未命名角色')
      await api.resumeRun(runId, { tri_views: {}, skipped })
      setActiveInteraction(null)
      onClose()
    } catch (e) {
      console.error('resume failed', e)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Sheet open={open} onOpenChange={(o: boolean) => !o && onClose()}>
      <SheetContent side="right" className="w-[520px] sm:max-w-[520px] overflow-y-auto">
        <SheetHeader>
          <SheetTitle>批量上传三视图（batch_upload_tri_view · {characters.length} 个角色）</SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-4 py-4">
          <p className="text-xs text-gray-400">
            为每个角色上传一张三视图（正面/侧面/背面），用于渲染阶段场景图作角色参考；小角色可勾选跳过。
          </p>
          {characters.map((c) => {
            const name = c.name ?? '未命名角色'
            const row = rows[name]
            return (
              <div key={name} className="flex flex-col gap-2 rounded border p-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">{name}</span>
                  <label className="flex items-center gap-1 text-xs text-gray-500">
                    <input
                      type="checkbox"
                      checked={row?.skipped ?? false}
                      onChange={(e) => setRow(name, { skipped: e.target.checked, file: null })}
                    />
                    跳过（小角色）
                  </label>
                </div>
                {c.appearance && <p className="text-xs text-gray-500">外观：{c.appearance}</p>}
                {c.tri_view_prompt && (
                  <p className="text-xs text-gray-400">三视图参考：{c.tri_view_prompt}</p>
                )}
                <Input
                  type="file"
                  accept="image/*"
                  disabled={row?.skipped || submitting}
                  onChange={(e) => setRow(name, { file: e.target.files?.[0] ?? null })}
                />
                {row?.uploadedPath && (
                  <p className="text-xs text-green-600">已上传：{row.uploadedPath}</p>
                )}
                {row?.uploading && <p className="text-xs text-gray-400">上传中...</p>}
              </div>
            )
          })}
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={handleSkipAll} disabled={submitting}>
            全部跳过
          </Button>
          <Button onClick={handleSubmit} disabled={!allResolved || submitting}>
            {submitting ? '上传中...' : '上传并确认'}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
