import { useState } from 'react'
import { Loader } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useNovelChapters } from '@/hooks/useNovelChapters'
import ChapterProse from './ChapterProse'

/** 章节阅读（抽屉内「章节」Tab）：左目录 + 右正文，主从并置。 */
export default function ChapterReader({ runId }: { runId: string }) {
  const { chapters, loading, error } = useNovelChapters(runId)
  const [stem, setStem] = useState<string | null>(null)

  return (
    <div className="flex-1 min-h-0 flex">
      {/* 目录 */}
      <div className="w-44 shrink-0 border-r border-border overflow-y-auto p-2 flex flex-col gap-0.5">
        {loading && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground p-2">
            <Loader className="size-3.5 animate-spin" />
            加载章节
          </div>
        )}
        {error && <div className="text-xs text-destructive p-2">{error}</div>}
        {!loading && !error && chapters.length === 0 && (
          <div className="text-xs text-muted-foreground p-2">暂无章节</div>
        )}
        {chapters.map((ch) => (
          <button
            key={ch.stem}
            onClick={() => setStem(ch.stem)}
            className={cn(
              'px-3 py-2 rounded-md text-left text-sm truncate transition-colors',
              ch.stem === stem ? 'bg-accent text-foreground' : 'text-muted-foreground hover:bg-accent',
            )}
          >
            {ch.label}
          </button>
        ))}
      </div>
      {/* 正文 */}
      <div className="flex-1 min-w-0 flex flex-col">
        {stem ? (
          <ChapterProse key={stem} runId={runId} stem={stem} />
        ) : (
          <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">
            选择左侧章节阅读原文
          </div>
        )}
      </div>
    </div>
  )
}
