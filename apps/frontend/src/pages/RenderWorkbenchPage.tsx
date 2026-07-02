import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, Loader2, ImageIcon, AudioLines, Clock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'
import { useRunStream } from '@/hooks/useRunStream'
import { cn } from '@/lib/utils'
import { groupLabel } from '@/lib/chapterLabel'
import ChapterList from '@/components/render-workbench/ChapterList'
import ImageRenderBoard from '@/components/render-workbench/ImageRenderBoard'
import AudioSynthesisPanel from '@/components/render-workbench/AudioSynthesisPanel'
import TimelinePreview from '@/components/render-workbench/TimelinePreview'

export default function RenderWorkbenchPage() {
  const { runId } = useParams<{ runId: string }>()
  const navigate = useNavigate()
  const { renderChapters, setRenderChapters, setCurrentRunId, runs } = useRunStore()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'images' | 'audio' | 'timeline'>('images')

  useRunStream(runId ?? null)

  useEffect(() => {
    if (runId) setCurrentRunId(runId)
  }, [runId, setCurrentRunId])

  useEffect(() => {
    if (!runId) return
    setLoading(true)
    setError(null)
    api.getRenderChapters(runId)
      .then((chapters) => {
        setRenderChapters(chapters)
        if (chapters.length > 0 && !selectedId) {
          setSelectedId(chapters[0].chapter_id)
        }
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])

  const run = runId ? runs[runId] : undefined
  const runTitle = run?.novel_title || runId?.slice(0, 8) || '未知'

  const stats = {
    total: renderChapters.length,
    done: renderChapters.filter((c) => ['rendered', 'exported', 'done'].includes(c.status)).length,
    inProgress: renderChapters.filter((c) => ['rendering', 'images_done', 'audio_done'].includes(c.status)).length,
    pending: renderChapters.filter((c) => ['planned', 'pending', 'processing'].includes(c.status)).length,
  }

  const selectedChapter = renderChapters.find((c) => c.chapter_id === selectedId)

  return (
    <div className="flex h-screen overflow-hidden flex-col">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-4 h-12 border-b border-border bg-background shrink-0">
        <Button variant="ghost" size="sm" onClick={() => navigate(`/runs/${runId}`)}>
          <ArrowLeft className="size-4" />
          返回规划
        </Button>
        <span className="text-sm font-medium">{runTitle}</span>
        <div className="ml-auto flex items-center gap-3 text-xs text-muted-foreground">
          <span>共 {stats.total} 章</span>
          <span className="text-green-600">完成 {stats.done}</span>
          <span className="text-blue-600">进行中 {stats.inProgress}</span>
          <span className="text-gray-500">待渲染 {stats.pending}</span>
        </div>
      </div>

      {/* Main content */}
      <div className="flex flex-1 min-h-0">
        {/* Left: Chapter list */}
        <div className="w-64 border-r border-border bg-sidebar shrink-0 flex flex-col">
          <div className="px-3 py-2 text-xs font-semibold text-muted-foreground border-b border-sidebar-border">
            章节列表
          </div>
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="size-4 animate-spin text-muted-foreground" />
            </div>
          ) : error ? (
            <div className="px-3 py-4 text-xs text-destructive text-center">{error}</div>
          ) : (
            <ChapterList chapters={renderChapters} selectedId={selectedId} onSelect={setSelectedId} />
          )}
        </div>

        {/* Middle: Work area */}
        <div className="flex-1 overflow-hidden bg-background flex flex-col">
          {selectedChapter ? (
            selectedChapter.has_storyboard ? (
              <>
                {/* Tab bar */}
                <div className="flex items-center gap-1 px-4 border-b border-border shrink-0">
                  {([
                    { key: 'images' as const, label: '图片', Icon: ImageIcon },
                    { key: 'audio' as const, label: '音频', Icon: AudioLines },
                    { key: 'timeline' as const, label: '时间轴', Icon: Clock },
                  ]).map(({ key, label, Icon }) => (
                    <button
                      key={key}
                      onClick={() => setActiveTab(key)}
                      className={cn(
                        'flex items-center gap-1.5 px-3 py-2 text-sm border-b-2 transition-colors',
                        activeTab === key
                          ? 'border-primary text-foreground'
                          : 'border-transparent text-muted-foreground hover:text-foreground'
                      )}
                    >
                      <Icon className="size-4" />
                      {label}
                    </button>
                  ))}
                </div>
                {/* Tab content */}
                <div className="flex-1 overflow-hidden">
                  {activeTab === 'images' && runId && (
                    <ImageRenderBoard
                      key={selectedChapter.chapter_id}
                      runId={runId}
                      chapterId={selectedChapter.chapter_id}
                      storyboard={(selectedChapter.storyboard as Array<Record<string, unknown>>) ?? []}
                    />
                  )}
                  {activeTab === 'audio' && runId && (
                    <AudioSynthesisPanel
                      key={selectedChapter.chapter_id}
                      runId={runId}
                      chapterId={selectedChapter.chapter_id}
                    />
                  )}
                  {activeTab === 'timeline' && runId && (
                    <TimelinePreview
                      key={selectedChapter.chapter_id}
                      runId={runId}
                      chapterId={selectedChapter.chapter_id}
                    />
                  )}
                </div>
              </>
            ) : ['pending', 'processing'].includes(selectedChapter.status) ? (
              <div className="p-6 flex flex-col items-start gap-4">
                <h2 className="text-lg font-semibold">{groupLabel(selectedChapter.chapter_id)}</h2>
                <div className="flex flex-col gap-2">
                  <p className="text-sm text-muted-foreground">
                    该章节尚未完成规划（脚本和分镜还未生成）
                  </p>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => navigate(`/runs/${runId}`)}
                  >
                    前往规划页面
                  </Button>
                </div>
              </div>
            ) : (
              <div className="p-6 flex flex-col items-start gap-4">
                <h2 className="text-lg font-semibold">{groupLabel(selectedChapter.chapter_id)}</h2>
                <p className="text-sm text-muted-foreground">
                  该章节暂无分镜数据，无法渲染
                </p>
              </div>
            )
          ) : (
            <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
              请选择一个章节
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
