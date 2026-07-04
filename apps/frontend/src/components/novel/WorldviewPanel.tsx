import { useMemo } from 'react'
import { Loader } from 'lucide-react'
import { api } from '@/api/client'
import { useRunResource } from '@/hooks/useRunResource'

interface WorldviewPanelProps {
  runId: string
}

/** 左侧 Sidebar「世界观」Tab：展示本 run 世界观设定文本。 */
export default function WorldviewPanel({ runId }: WorldviewPanelProps) {
  const { data: worldview, loading, error } = useRunResource(runId, api.getRunWorldview, '')

  const paragraphs = useMemo(
    () => worldview.split(/\n+/).map((p) => p.trim()).filter(Boolean),
    [worldview],
  )

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center gap-2 text-xs text-muted-foreground">
        <Loader className="size-3.5 animate-spin" />
        加载世界观
      </div>
    )
  }
  if (error) {
    return (
      <div className="flex-1 flex items-center justify-center text-xs text-destructive px-4 text-center">
        {error}
      </div>
    )
  }
  if (paragraphs.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-xs text-muted-foreground px-4 text-center">
        暂无世界观设定
      </div>
    )
  }

  return (
    <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5">
      <div className="max-w-3xl">
        {paragraphs.map((p, i) => (
          <p key={i} className="mb-4 leading-8 text-[15px] text-foreground whitespace-pre-wrap break-words">
            {p}
          </p>
        ))}
      </div>
    </div>
  )
}
