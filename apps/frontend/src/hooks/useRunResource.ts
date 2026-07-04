import { useEffect, useState } from 'react'

/**
 * 通用「随 run 拉取只读资源」hook：切 run 时在渲染阶段重置（React 官方
 * 「随 prop 变化重置 state」写法，避免在 effect 内同步 setState 触发级联渲染），
 * effect 只在异步回调里 setState。
 *
 * fetcher 必须是稳定引用（如 `api.getRunWorldview`），否则 effect 会反复重跑。
 */
export function useRunResource<T>(
  runId: string | null,
  fetcher: (runId: string) => Promise<T>,
  empty: T,
) {
  const [data, setData] = useState<T>(empty)
  const [loading, setLoading] = useState<boolean>(!!runId)
  const [error, setError] = useState<string | null>(null)
  const [trackedRun, setTrackedRun] = useState(runId)

  if (runId !== trackedRun) {
    setTrackedRun(runId)
    setData(empty)
    setError(null)
    setLoading(!!runId)
  }

  useEffect(() => {
    if (!runId) return
    let cancelled = false
    fetcher(runId)
      .then((d) => { if (!cancelled) setData(d) })
      .catch(() => { if (!cancelled) setError('加载失败') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [runId, fetcher])

  return { data, loading, error }
}
