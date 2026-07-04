import { useEffect, useMemo, useState } from 'react'
import { Loader } from 'lucide-react'
import { api } from '@/api/client'

/** 单章正文体：按 stem 拉取并排版。挂载即绑定一个 stem（父层 key=stem 保证切章重挂载）。 */
export default function ChapterProse({ runId, stem }: { runId: string; stem: string }) {
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .getRunChapterText(runId, stem)
      .then((res) => { if (!cancelled) setText(res.text) })
      .catch(() => { if (!cancelled) setError('正文加载失败') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [runId, stem])

  const paragraphs = useMemo(
    () => text.split(/\n+/).map((p) => p.trim()).filter(Boolean),
    [text],
  )

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader className="size-4 animate-spin" />
        加载中
      </div>
    )
  }
  if (error) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-destructive">{error}</div>
    )
  }
  return (
    <div className="flex-1 overflow-y-auto px-6 py-5">
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
