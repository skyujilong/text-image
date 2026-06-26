import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

interface Props {
  runId: string
  operation?: string
  chapterId?: string
  message?: string
}

/**
 * 服务器就绪确认面板：租赁服务器场景下，在耗时操作前人工确认。
 * resume "ready" → 继续流程。
 * 由右侧常驻区渲染（body-only，无 Sheet 包装）。
 */
export default function ServerReadyPanel({ runId, operation, chapterId, message }: Props) {
  const { setActiveInteraction, activeInteraction } = useRunStore()

  const handleReady = async () => {
    if (!activeInteraction) return
    try {
      await api.resumeRun(runId, activeInteraction.scope, activeInteraction.thread_id, 'ready')
      setActiveInteraction(null)
    } catch (e) {
      console.error('resume failed', e)
    }
  }

  // 操作类型的显示名
  const operationLabels: Record<string, string> = {
    audio_synthesis: 'TTS 音频合成',
    image_render: 'ComfyUI 图像渲染',
  }

  const operationLabel = operation ? operationLabels[operation] || operation : '服务器'

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6">
        <h2 className="text-lg font-semibold text-foreground">
          服务器就绪确认
          {chapterId && <span className="text-sm font-normal text-muted-foreground ml-2">· {chapterId}</span>}
        </h2>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        <div className="flex flex-col gap-4">
          <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
            <p className="text-sm text-amber-800 font-medium">⚠️ 即将开始 {operationLabel}</p>
            <p className="text-xs text-amber-600 mt-1">
              {message || '请确认远程服务器已启动并配置完成后再继续。'}
            </p>
          </div>

          <div className="text-sm text-muted-foreground">
            <p>• 确认 TTS 服务或 ComfyUI 服务可正常连接</p>
            <p>• 确认服务器显存/内存资源充足</p>
            <p>• 确认模型文件已加载到位</p>
          </div>
        </div>
      </div>

      <div className="flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2 px-6 pb-6 gap-2">
        <Button onClick={handleReady}>
          服务器已就绪，继续
        </Button>
      </div>
    </div>
  )
}
