import { useEffect, useState } from 'react'
import { Clock, Download, Loader2, FileVideo } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { api, fileUrl } from '@/api/client'
import { groupLabel } from '@/lib/chapterLabel'

interface TimelineEntry {
  storyboard_id: number
  text: string
  speaker: string
  start_time: number
  end_time: number
  image_path: string
}

interface Props {
  runId: string
  chapterId: string
}

/**
 * 时间轴预览 + 导出组件：生成时间轴、展示表格、导出剪映草稿。
 */
export default function TimelinePreview({ runId, chapterId }: Props) {
  const [building, setBuilding] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [entries, setEntries] = useState<TimelineEntry[] | null>(null)
  const [exportPath, setExportPath] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setEntries(null)
    setExportPath(null)
    setError(null)
    api.getTimeline(runId, chapterId)
      .then((res) => {
        if (res.timeline && Array.isArray(res.timeline)) {
          setEntries(res.timeline as TimelineEntry[])
        }
      })
      .catch(() => {})
  }, [runId, chapterId])

  const handleBuild = async () => {
    setBuilding(true)
    setError(null)
    try {
      await api.buildTimeline(runId, chapterId)
      const res = await api.getTimeline(runId, chapterId)
      if (res.timeline && Array.isArray(res.timeline)) {
        setEntries(res.timeline as TimelineEntry[])
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBuilding(false)
    }
  }

  const handleExport = async () => {
    setExporting(true)
    setError(null)
    try {
      const res = await api.exportDraft(runId)
      setExportPath(res.export_path ?? null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="flex flex-col h-full p-4 gap-4 overflow-y-auto">
      <div className="flex items-center gap-2">
        <Clock className="size-4" />
        <span className="text-sm font-medium">时间轴 · {groupLabel(chapterId)}</span>
      </div>

      <div className="flex items-center gap-2">
        <Button onClick={handleBuild} disabled={building} variant="outline" size="sm">
          {building ? <Loader2 className="size-4 animate-spin" /> : <Clock className="size-4" />}
          生成时间轴
        </Button>
        <Button onClick={handleExport} disabled={exporting} variant="outline" size="sm">
          {exporting ? <Loader2 className="size-4 animate-spin" /> : <Download className="size-4" />}
          导出剪映草稿
        </Button>
      </div>

      {error && <p className="text-xs text-destructive">{error}</p>}

      {exportPath && (
        <div className="flex items-center gap-2 text-xs text-green-600 rounded border border-green-200 bg-green-50 p-2">
          <FileVideo className="size-4" />
          导出完成：{exportPath}
        </div>
      )}

      {/* Timeline table */}
      {entries && entries.length > 0 ? (
        <div className="border border-border rounded overflow-hidden">
          <table className="w-full text-xs">
            <thead className="bg-accent text-muted-foreground">
              <tr>
                <th className="px-2 py-1.5 text-left">镜头</th>
                <th className="px-2 py-1.5 text-left">说话人</th>
                <th className="px-2 py-1.5 text-left">文本</th>
                <th className="px-2 py-1.5 text-right">开始</th>
                <th className="px-2 py-1.5 text-right">结束</th>
                <th className="px-2 py-1.5 text-center">图片</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="px-2 py-1.5 font-mono">{entry.storyboard_id}</td>
                  <td className="px-2 py-1.5">{entry.speaker}</td>
                  <td className="px-2 py-1.5 max-w-xs truncate">{entry.text}</td>
                  <td className="px-2 py-1.5 text-right">{entry.start_time?.toFixed(2) ?? '-'}</td>
                  <td className="px-2 py-1.5 text-right">{entry.end_time?.toFixed(2) ?? '-'}</td>
                  <td className="px-2 py-1.5 text-center">
                    {entry.image_path ? (
                      <img
                        src={fileUrl(entry.image_path)}
                        alt="shot"
                        className="size-10 object-cover rounded inline-block"
                      />
                    ) : (
                      <span className="text-muted-foreground">-</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        !building && (
          <p className="text-sm text-muted-foreground">
            尚未生成时间轴，点击「生成时间轴」按钮开始
          </p>
        )
      )}
    </div>
  )
}
