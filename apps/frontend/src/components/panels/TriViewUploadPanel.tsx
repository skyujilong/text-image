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
}
interface Props {
  runId: string
  character: Character
  open: boolean
  onClose: () => void
}

/**
 * 三视图上传面板：上传一张三视图 → POST /upload 拿 comfyui_name → resume {comfyui_name}。
 * 小角色可跳过 → resume {skip: true}。
 * 实际上传在 API 层完成（R1：upload_tri_view 节点零副作用）。
 */
export default function TriViewUploadPanel({ runId, character, open, onClose }: Props) {
  const { setActiveInteraction } = useRunStore()
  const [file, setFile] = useState<File | null>(null)
  const [loading, setLoading] = useState(false)

  const charName = character.name ?? '未命名角色'

  const handleUpload = async () => {
    if (!file) return
    setLoading(true)
    try {
      const { comfyui_name } = await api.uploadFile(
        runId,
        file,
        `characters/${charName}`
      )
      await api.resumeRun(runId, { comfyui_name })
      setActiveInteraction(null)
      onClose()
    } catch (e) {
      console.error('upload/resume failed', e)
    } finally {
      setLoading(false)
    }
  }

  const handleSkip = async () => {
    setLoading(true)
    try {
      await api.resumeRun(runId, { skip: true })
      setActiveInteraction(null)
      onClose()
    } catch (e) {
      console.error('resume failed', e)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Sheet open={open} onOpenChange={(o: boolean) => !o && onClose()}>
      <SheetContent side="right" className="w-[440px] sm:max-w-[440px]">
        <SheetHeader>
          <SheetTitle>上传三视图（upload_tri_view · {charName}）</SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-3 py-4">
          {character.appearance && (
            <p className="text-sm text-gray-500">外观：{character.appearance}</p>
          )}
          <p className="text-xs text-gray-400">
            上传一张三视图（正面/侧面/背面），用于渲染阶段场景图作角色参考。小角色可跳过。
          </p>
          <Input
            type="file"
            accept="image/*"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={handleSkip} disabled={loading}>
            跳过（小角色）
          </Button>
          <Button onClick={handleUpload} disabled={!file || loading}>
            {loading ? '上传中...' : '上传并确认'}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
