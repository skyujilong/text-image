import { useEffect, useRef } from 'react'
import { useRunStore } from '@/store/runStore'

export function useRunStream(runId: string | null) {
  const { setNodeStatus, setActiveInteraction, upsertRun, setRunError } = useRunStore()
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!runId) return

    esRef.current?.close()
    const es = new EventSource(`/api/runs/${runId}/stream`)
    esRef.current = es

    es.onmessage = (e) => {
      let event: Record<string, unknown>
      try {
        event = JSON.parse(e.data)
      } catch {
        return
      }

      const type = event.type as string

      if (type === 'node_status') {
        const statusKey = event.status_key as string
        const status = event.status as string

        if (status === 'waiting_human') {
          setNodeStatus(statusKey, 'waiting_human')
          if (event.node !== undefined) {
            setActiveInteraction({ node: event.node as string, payload: event.payload })
          }
        } else {
          setNodeStatus(statusKey, status as 'running' | 'done' | 'error')
        }
      }

      if (type === 'run_complete') {
        upsertRun({
          run_id: runId,
          novel_dir: '',
          novel_title: '',
          status: 'done',
          created_at: new Date().toISOString(),
        })
        es.close()
      }

      if (type === 'run_error') {
        const msg = event.message as string | undefined
        setRunError(msg ?? '未知错误')
        upsertRun({
          run_id: runId,
          novel_dir: '',
          novel_title: '',
          status: 'error',
          created_at: new Date().toISOString(),
        })
        // 不关闭 SSE，保持连接以便用户重试后继续接收事件
      }
    }

    es.onerror = () => es.close()

    return () => {
      es.close()
      esRef.current = null
    }
  }, [runId])
}
