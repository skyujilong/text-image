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
        const hasNode = event.node !== undefined
        console.log('[SSE] 节点状态:', statusKey, status, 'node=', event.node, 'hasPayload=', event.payload !== undefined)

        if (status === 'waiting_human') {
          setNodeStatus(statusKey, 'waiting_human')
          console.log('[SSE] waiting_human hasNode=', hasNode, hasNode ? '→ 将弹窗' : '→ 无 node（祖先事件），不弹窗')
          // R5：只有带 node 字段的叶子事件才触发交互弹窗。
          // _emit(propagate=True) 会对每个祖先 key 发同 status 事件，但祖先事件
          // 不带 node 字段（仅叶子带），故此处 node 判定天然过滤祖先事件，避免重复弹窗。
          if (hasNode) {
            setActiveInteraction({
              node: event.node as string,
              payload: event.payload ?? null,
            })
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
