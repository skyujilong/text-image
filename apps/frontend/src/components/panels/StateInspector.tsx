import { useEffect, useState } from 'react'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { api } from '@/api/client'

interface Props {
  open: boolean
  nodePath: string | null
  runId: string | null
  onClose: () => void
}

export default function StateInspector({ open, nodePath, runId, onClose }: Props) {
  const [data, setData] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open || !nodePath || !runId) return
    setLoading(true)
    api.getNodeState(runId, nodePath)
      .then((r) => setData(r.values))
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [open, nodePath, runId])

  return (
    <Sheet open={open} onOpenChange={(v) => !v && onClose()}>
      <SheetContent side="right" className="w-[70%] sm:max-w-[70%] overflow-y-auto">
        <SheetHeader>
          <SheetTitle>State: {nodePath}</SheetTitle>
        </SheetHeader>
        {loading && <div className="text-sm text-gray-400 mt-4">加载中...</div>}
        {!loading && data && (
          <pre className="mt-4 text-xs bg-gray-50 rounded p-3 overflow-auto whitespace-pre-wrap break-all">
            {JSON.stringify(data, null, 2)}
          </pre>
        )}
        {!loading && !data && (
          <div className="text-sm text-gray-400 mt-4">暂无数据（节点尚未执行或无 state 写入）</div>
        )}
      </SheetContent>
    </Sheet>
  )
}
