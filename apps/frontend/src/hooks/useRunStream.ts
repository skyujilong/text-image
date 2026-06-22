import { useEffect, useRef } from 'react'
import { api, fileUrl, type RenderShot } from '@/api/client'
import type { NodeStatus } from '@/store/runStore'
import { useRunStore } from '@/store/runStore'

export function useRunStream(runId: string | null) {
  const { setNodeStatus, setActiveInteraction, upsertRun, setRunError, streamGeneration, batchSetNodeStatuses } = useRunStore()
  const esRef = useRef<EventSource | null>(null)

  // 切换 run 或刷新页面后，从 checkpoint 历史恢复节点展示状态
  useEffect(() => {
    if (!runId) return
    api.getRunCurrentState(runId)
      .then((state) => {
        batchSetNodeStatuses(state.node_statuses as Record<string, NodeStatus>)
        if (state.active_interaction) {
          setActiveInteraction({
            node: state.active_interaction.node,
            payload: state.active_interaction.payload,
          })
        } else {
          setActiveInteraction(null)
        }
        console.log('[restore] run_id=%s status=%s restored %d node statuses', runId, state.status, Object.keys(state.node_statuses).length)
      })
      .catch((err) => console.warn('[restore] failed to restore run state:', err))
  // runs[runId] 依赖确保刷新列表后（如 fork）也能正确恢复
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])

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

      if (type === 'render_image') {
        // 单张渲染结果增量更新看板（逐张冒出）。事件携带绝对路径，需转 URL。
        // 直接 merge 进 store 已有 shot，累积候选（reroll 追加，旧候选保留）。
        const shotId = event.shot_id as number
        const status = event.status as RenderShot['status']
        const { renderBoard, upsertRenderShot } = useRunStore.getState()
        const prev = renderBoard[shotId]
        const candidate = event.candidate as string | undefined
        const selected = event.selected as string | undefined
        const candidates = prev ? [...prev.candidates] : []
        if (candidate && !candidates.some((c) => c.path === candidate)) {
          candidates.push({ path: candidate, url: fileUrl(candidate) })
        }
        upsertRenderShot({
          storyboard_id: shotId,
          workflow: prev?.workflow ?? 'qwen_t2i',
          prompt: (event.prompt as string | undefined) ?? prev?.prompt ?? '',
          subjects: prev?.subjects ?? [],
          status,
          error: (event.error as string | undefined) ?? null,
          candidates,
          selected: selected ?? prev?.selected ?? null,
          selected_url: selected ? fileUrl(selected) : prev?.selected_url ?? null,
        })
      }

      if (type === 'run_complete') {
        console.log('[SSE] 运行完成:', runId)
        upsertRun({
          run_id: runId,
          novel_dir: '',
          novel_title: '',
          status: 'done',
          created_at: new Date().toISOString(),
          params: {},
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
          params: {},
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
