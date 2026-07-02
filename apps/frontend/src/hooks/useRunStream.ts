import { useCallback, useEffect, useRef } from 'react'
import { api, fileUrl, type RenderShot } from '@/api/client'
import type { NodeStatus } from '@/store/runStore'
import { useRunStore } from '@/store/runStore'

export function useRunStream(runId: string | null) {
  const { setNodeStatus, setActiveInteraction, upsertRun, setRunError, streamGeneration, batchSetNodeStatuses, setDelegatedScope } = useRunStore()
  const esRef = useRef<EventSource | null>(null)

  // 从 checkpoint 历史恢复节点展示状态。初次加载与 SSE 重连成功后都复用：
  // SSE 队列无重放，断连期间丢失的状态变更只能靠 checkpoint 重建补回。
  const restoreState = useCallback((id: string) => {
    api.getRunCurrentState(id)
      .then((state) => {
        batchSetNodeStatuses(state.node_statuses as Record<string, NodeStatus>)
        // 重建委派 scope 锁定态：委派进行中刷新后仍需锁定 plan tab
        setDelegatedScope((state.delegated_scope as 'main' | 'plan' | null) ?? null)
        if (state.active_interaction) {
          setActiveInteraction({
            scope: state.active_interaction.scope,
            thread_id: state.active_interaction.thread_id,
            node: state.active_interaction.node,
            payload: state.active_interaction.payload,
          })
        } else {
          // 早中断竞态：本条 /current-state 快照可能取自 interrupt 落库前（active_interaction 为空），
          // 而 SSE 此刻已把实时 interrupt 弹窗设好。无条件清空会把更新的实时态覆盖成 null（弹窗闪没）。
          // 故仅清「不属于本 run 的残留弹窗」（切 run 后遗留）——这正是本兜底原本的职责；
          // 属于本 run 的弹窗交给权威的 SSE 流管理，快照不越权覆盖。
          // thread_id 归属：main scope === run_id；子图 scope === `${run_id}::plan`。
          const cur = useRunStore.getState().activeInteraction
          const belongsToThisRun =
            cur != null && (cur.thread_id === id || cur.thread_id.startsWith(`${id}::`))
          if (!belongsToThisRun) {
            setActiveInteraction(null)
          }
        }
        console.log('[restore] run_id=%s status=%s restored %d node statuses', id, state.status, Object.keys(state.node_statuses).length)
      })
      .catch((err) => console.warn('[restore] failed to restore run state:', err))
  }, [batchSetNodeStatuses, setActiveInteraction, setDelegatedScope])

  // 切换 run 或刷新页面后，从 checkpoint 历史恢复节点展示状态
  useEffect(() => {
    if (!runId) return
    restoreState(runId)
  // runs[runId] 依赖确保刷新列表后（如 fork）也能正确恢复
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])

  useEffect(() => {
    if (!runId) return

    // closed：本 effect 已被清理（切 run / 卸载），任何回调都不得再重连
    // completed：收到 run_complete 主动关闭，属正常结束，不应触发误重连
    // firstConnect：区分首次建连与重连，仅重连成功后才补拉 current-state
    let closed = false
    let completed = false
    let firstConnect = true
    let attempt = 0
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined

    const connect = () => {
      if (closed) return
      esRef.current?.close()
      const es = new EventSource(`/api/runs/${runId}/stream`)
      esRef.current = es

      es.onopen = () => {
        attempt = 0
        if (firstConnect) {
          firstConnect = false
          console.log('[SSE] 连接已建立:', runId)
        } else {
          // 重连成功：SSE 无重放，补拉 checkpoint 状态找回断连期间丢失的变更
          console.log('[SSE] 重连成功:', runId)
          restoreState(runId)
        }
      }

      es.onmessage = onMessage

      es.onerror = (err) => {
        // 浏览器仍在自重连（CONNECTING）时不插手；只有连接已被判定为 CLOSED
        // （如代理回 ECONNREFUSED 等硬错误，EventSource 不再自动重连）才手动退避重建。
        if (closed || completed) return
        if (es.readyState === EventSource.CLOSED) {
          es.close()
          const delay = Math.min(1000 * 2 ** attempt, 15000)
          attempt += 1
          console.error('[SSE] 连接已断开，%dms 后重连 (第 %d 次):', delay, attempt, err)
          reconnectTimer = setTimeout(connect, delay)
        } else {
          console.error('[SSE] 连接错误（浏览器自动重连中）:', err)
        }
      }
    }

    const onMessage = (e: MessageEvent) => {
      let event: Record<string, unknown>
      try {
        event = JSON.parse(e.data)
      } catch {
        return
      }

      const type = event.type as string

      if (type === 'node_status' || type === 'interrupt') {
        const nodePath = event.node_path as string
        const status = event.status as string
        const hasNode = event.node !== undefined
        const scope = event.scope as string
        const threadId = event.thread_id as string
        console.log('[SSE] 节点状态:', scope, nodePath, status, 'node=', event.node, 'hasPayload=', event.payload !== undefined)

        if (status === 'waiting_human') {
          setNodeStatus(nodePath, 'waiting_human')
          console.log('[SSE] waiting_human hasNode=', hasNode, hasNode ? '→ 将弹窗' : '→ 无 node（祖先事件），不弹窗')
          // R5：只有带 node 字段的叶子事件才触发交互弹窗。
          // _emit_enveloped(propagate=True) 会对每个祖先 key 发同 status 事件，但祖先事件
          // 不带 node 字段（仅叶子带），故此处 node 判定天然过滤祖先事件，避免重复弹窗。
          if (hasNode) {
            setActiveInteraction({
              scope,
              thread_id: threadId,
              node: event.node as string,
              payload: event.payload ?? null,
            })
          }
        } else {
          setNodeStatus(nodePath, status as 'running' | 'done' | 'error')
        }
      }

      if (type === 'delegate') {
        // 委派生命周期事件：active → 锁定该 scope tab；done → 解锁回退到主流程
        const scope = event.scope as 'main' | 'plan'
        const status = event.status as 'active' | 'done'
        console.log('[SSE] 委派:', scope, status)
        setDelegatedScope(status === 'active' ? scope : null)
      }

      if (type === 'render_image') {
        // 单张渲染结果增量更新看板（逐张冒出）。事件携带绝对路径，需转 URL。
        // 直接 merge 进 store 已有 shot，累积候选（reroll 追加，旧候选保留）。
        const chapterId = event.chapter_id as string
        const shotId = event.shot_id as number
        if (!chapterId) {
          console.warn('[SSE] render_image 事件缺少 chapter_id，忽略', event)
          return
        }
        const status = event.status as RenderShot['status']
        const { renderBoard, upsertRenderShot } = useRunStore.getState()
        const chapterBoard = renderBoard[chapterId] ?? {}
        const prev = chapterBoard[shotId]
        const candidate = event.candidate as string | undefined
        const selected = event.selected as string | undefined
        const candidates = prev ? [...prev.candidates] : []
        if (candidate && !candidates.some((c) => c.path === candidate)) {
          candidates.push({ path: candidate, url: fileUrl(candidate) })
        }
        upsertRenderShot(chapterId, {
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
        // 标记正常结束，避免随后的 onerror 误触发重连
        completed = true
        esRef.current?.close()
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

    connect()

    return () => {
      console.log('[SSE] 清理连接:', runId)
      closed = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      esRef.current?.close()
      esRef.current = null
    }
  // setNodeStatus/setActiveInteraction/upsertRun/setRunError 是 zustand 稳定引用，无需入依赖
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, streamGeneration, restoreState])
}
