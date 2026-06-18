import { useEffect, useRef } from 'react'
import { useRunStore } from '@/store/runStore'

export function useRunStream(runId: string | null) {
  const { setNodeStatus, setActiveInteraction, upsertRun, setRunError, streamGeneration } = useRunStore()
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!runId) return

    esRef.current?.close()
    const es = new EventSource(`/api/runs/${runId}/stream`)
    esRef.current = es

    es.onopen = () => {
      console.log('[SSE] 连接已建立:', runId)
    }

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
        console.log('[SSE] 节点状态:', statusKey, status)

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
        console.log('[SSE] 运行完成:', runId)
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
        console.error('[SSE] 运行出错:', msg)
        setRunError(msg ?? '未知错误')
        upsertRun({
          run_id: runId,
          novel_dir: '',
          novel_title: '',
          status: 'error',
          created_at: new Date().toISOString(),
        })
        // 出错时不关闭 SSE，用户重试后可以继续接收事件
      }
    }

    es.onerror = (err) => {
      console.error('[SSE] 连接错误:', err)
      // 出错时不要立即关闭，让浏览器自动重连
    }

    return () => {
      console.log('[SSE] 清理连接:', runId)
      es.close()
      esRef.current = null
    }
  }, [runId, streamGeneration])
}
