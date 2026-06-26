import { cn } from '@/lib/utils'
import type { RenderChapter } from '@/api/client'

const STATUS_META: Record<string, { label: string; dot: string; badge: string }> = {
  planned: { label: '待渲染', dot: 'bg-gray-400', badge: 'bg-gray-100 text-gray-600' },
  pending: { label: '待渲染', dot: 'bg-gray-400', badge: 'bg-gray-100 text-gray-600' },
  rendering: { label: '生图中', dot: 'bg-blue-500', badge: 'bg-blue-100 text-blue-700' },
  audio: { label: '音频中', dot: 'bg-orange-500', badge: 'bg-orange-100 text-orange-700' },
  rendered: { label: '已完成', dot: 'bg-green-500', badge: 'bg-green-100 text-green-700' },
  done: { label: '已完成', dot: 'bg-green-500', badge: 'bg-green-100 text-green-700' },
  exported: { label: '已导出', dot: 'bg-emerald-600', badge: 'bg-emerald-100 text-emerald-700' },
}

const FALLBACK = STATUS_META['planned']

interface Props {
  chapters: RenderChapter[]
  selectedId: string | null
  onSelect: (id: string) => void
}

export default function ChapterList({ chapters, selectedId, onSelect }: Props) {
  if (chapters.length === 0) {
    return (
      <div className="flex items-center justify-center text-xs text-muted-foreground p-4 text-center">
        暂无章节
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-0.5 overflow-y-auto p-2">
      {chapters.map((ch) => {
        const meta = STATUS_META[ch.status] ?? FALLBACK
        const isActive = ch.chapter_id === selectedId
        return (
          <button
            key={ch.chapter_id}
            onClick={() => onSelect(ch.chapter_id)}
            className={cn(
              'flex items-center gap-2 px-3 py-2 rounded-md text-left transition-colors',
              isActive ? 'bg-sidebar-accent' : 'hover:bg-accent'
            )}
          >
            <span className={cn('size-2 rounded-full shrink-0', meta.dot)} />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate">{ch.chapter_id}</div>
              <div className="flex items-center gap-1.5 mt-0.5">
                <span className={cn('text-[10px] px-1 rounded', meta.badge)}>{meta.label}</span>
                {ch.has_storyboard && (
                  <span className="text-[10px] text-muted-foreground">
                    {ch.storyboard_count ?? 0} 镜头
                  </span>
                )}
              </div>
            </div>
          </button>
        )
      })}
    </div>
  )
}
