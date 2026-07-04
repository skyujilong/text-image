import { useCallback, useEffect, useState } from 'react'
import { api, type WorkDir } from '@/api/client'

/** 工作目录注册表（后端持久化）：加载、添加、删除。 */
export function useWorkDirs() {
  const [workDirs, setWorkDirs] = useState<WorkDir[]>([])
  const [ready, setReady] = useState(false)

  const reload = useCallback(async () => {
    const list = await api.listWorkDirs()
    setWorkDirs(list)
    setReady(true)
  }, [])

  // 挂载即拉取：setState 全在 .then 回调（异步边界），不在 effect 体内同步 setState。
  useEffect(() => {
    let cancelled = false
    api
      .listWorkDirs()
      .then((list) => { if (!cancelled) { setWorkDirs(list); setReady(true) } })
      .catch(() => { if (!cancelled) setReady(true) })
    return () => { cancelled = true }
  }, [])

  const add = useCallback(
    async (path: string, label = '') => {
      const wd = await api.addWorkDir(path, label)
      await reload()
      return wd
    },
    [reload],
  )

  const remove = useCallback(
    async (id: number) => {
      await api.deleteWorkDir(id)
      await reload()
    },
    [reload],
  )

  return { workDirs, ready, reload, add, remove }
}
