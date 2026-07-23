import { useCallback, useEffect, useRef } from 'react'
import { api, fileUrl, type RenderShot } from '@/api/client'
import type { NodeStatus } from '@/store/runStore'
import { useRunStore } from '@/store/runStore'

export function useRunStream(runId: string | null) {
  const {
    setNodeStatus,
    setActiveInteraction,
    patchRunStatus,
    setRunError,
    streamGeneration,
    batchSetNodeStatuses,
    setDelegatedScope,
    claimNodeStatuses,
  } = useRunStore()
  const esRef = useRef<EventSource | null>(null)
  // 安全网轮询用：仅订阅本 run 的状态，活跃态才开轮询。
  const runStatus = useRunStore((s) => (runId ? s.runs[runId]?.status : undefined))

  // 从 checkpoint 历史恢复节点展示状态 + 回写权威 run 级状态。唯一的状态调和入口，
  // 每次 SSE 建连（onopen）与活跃期轮询都复用：SSE 队列无重放，断连/丢事件期间的变更
  // 只能靠 /current-state 重建补回。
  const restoreState = useCallback((id: string) => {
    // 记录发起前的 interaction 引用：若 fetch 期间实时 SSE 已把弹窗设好、或面板 resume 已清空，
    // 引用会变——说明实时态比这份快照新，快照不得越权覆盖（关 resume-vs-poll / 早中断竞态）。
    const interactionBefore = useRunStore.getState().activeInteraction
    api.getRunCurrentState(id)
      .then((state) => {
        batchSetNodeStatuses(state.node_statuses as Record<string, NodeStatus>)
        // 重建委派 scope 锁定态：委派进行中刷新后仍需锁定 plan tab
        setDelegatedScope(state.delegated_scope ?? null)
        // 回写权威 run 级状态：找回丢失的 run_complete/run_error，侧栏不再永久卡「运行中」。
        // 'unknown'（run meta 缺失）不回写，避免污染。
        if (state.status !== 'unknown') patchRunStatus(id, state.status)

        // 仅当 fetch 期间无更新的实时态时才用快照调和 interaction。
        const interactionUnchanged = useRunStore.getState().activeInteraction === interactionBefore
        if (interactionUnchanged) {
          if (state.active_interaction) {
            setActiveInteraction({
              scope: state.active_interaction.scope,
              thread_id: state.active_interaction.thread_id,
              node: state.active_interaction.node,
              payload: state.active_interaction.payload,
            })
          } else {
            // 快照无 pending interrupt，清残留弹窗：
            // 1) 不属于本 run（切 run 遗留）——本兜底原本的职责；
            // 2) 属于本 run 但快照 status 非 waiting_human——后端先落库 waiting_human 再发 interrupt
            //    事件，故非 waiting_human 即权威「无待处理」，清掉本 run 残留面板（治双 tab 一边
            //    resume 后另一边面板残留、以及 resume 后自身面板卡住）。唯一竞态反例（快照取自
            //    interrupt 落库前、响应又落在面板设定后）已被上面的 interactionBefore 守卫拦下。
            // thread_id 归属：main scope === run_id；子图 scope === `${run_id}::plan`。
            const cur = interactionBefore
            const belongsToThisRun =
              cur != null && (cur.thread_id === id || cur.thread_id.startsWith(`${id}::`))
            if (!belongsToThisRun || state.status !== 'waiting_human') {
              setActiveInteraction(null)
            }
          }
        }
        console.log('[restore] run_id=%s status=%s restored %d node statuses', id, state.status, Object.keys(state.node_statuses).length)
      })
      .catch((err) => console.warn('[restore] failed to restore run state:', err))
  }, [batchSetNodeStatuses, setActiveInteraction, setDelegatedScope, patchRunStatus])

  useEffect(() => {
    if (!runId) return
    // 切 run 换主：一次性清掉上一 run 的节点状态/交互，避免跨 run 串色。
    // 同 run 重挂载（RunPage↔RenderWorkbench 路由切换、StrictMode）不清空、无闪烁。
    claimNodeStatuses(runId)

    // closed：本 effect 已被清理（切 run / 卸载），任何回调都不得再重连
    // completed：收到 run_complete / run_deleted 主动关闭，属正常结束，不应触发误重连
    let closed = false
    let completed = false
    let attempt = 0
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined

    const connect = () => {
      if (closed) return
      esRef.current?.close()
      const es = new EventSource(`/api/runs/${runId}/stream`)
      esRef.current = es

      es.onopen = () => {
        // 每次建连都 restore：首连、切 run、streamGeneration bump（retry/重跑）、
        // 手动退避重连、浏览器自动重连——onopen 是唯一覆盖全部重连路径的收敛点。
        // SSE 无重放，统一在此从 checkpoint 补回断连/建连窗口内丢失的变更。
        attempt = 0
        console.log('[SSE] 连接已建立:', runId)
        restoreState(runId)
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
          // render_image 事件不带 orientation/edit_model，沿用已有值（避免增量更新丢字段）
          edit_model: prev?.edit_model ?? '4step',
          orientation: prev?.orientation ?? 'square',
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
        patchRunStatus(runId, 'done')
        // 标记正常结束，避免随后的 onerror 误触发重连
        completed = true
        esRef.current?.close()
      }

      if (type === 'run_error') {
        const msg = event.message as string | undefined
        console.error('[SSE] 运行出错:', msg)
        setRunError(msg ?? '未知错误')
        patchRunStatus(runId, 'error')
        // 出错时不关闭 SSE，用户重试后可以继续接收事件
      }

      if (type === 'run_deleted') {
        // 另一 tab 删除了本 run：视同结束，关流不重连。本 tab 的 store 清理由删除方
        // 的 removeRun / 下次 listRuns 负责，此处只需停止心跳/重连风暴。
        console.log('[SSE] run 已被删除:', runId)
        completed = true
        esRef.current?.close()
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
  // setNodeStatus/setActiveInteraction/patchRunStatus/setRunError/claimNodeStatuses 是 zustand 稳定引用，无需入依赖
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, streamGeneration, restoreState])

  // 安全网轮询：连接名义正常但事件被丢（如僵尸消费者旧账、代理静默截断）时，
  // 靠周期性 restore 找回丢失的 interrupt / run_complete。活跃态才轮询，转终态自动停。
  useEffect(() => {
    if (!runId) return
    if (runStatus !== 'running' && runStatus !== 'waiting_human') return
    const timer = setInterval(() => restoreState(runId), 12000)
    return () => clearInterval(timer)
  }, [runId, runStatus, restoreState])
}
