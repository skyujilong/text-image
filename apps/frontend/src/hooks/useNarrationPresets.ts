import { useCallback, useEffect, useState } from 'react'
import { api, type CreateNarrationPresetBody, type NarrationPreset } from '@/api/client'

/**
 * 用户自定义解说方案预设的拉取/保存/删除（跨 run 持久化）。
 * 挂载时拉一次（状态仅在 await 后更新，避免 effect 内同步 setState）；
 * save/delete 后本地乐观更新列表（后端为唯一真源，失败抛给调用方处理）。
 */
export function useNarrationPresets() {
  const [presets, setPresets] = useState<NarrationPreset[]>([])

  useEffect(() => {
    let cancelled = false
    api
      .listNarrationPresets()
      .then((list) => {
        if (!cancelled) setPresets(list)
      })
      .catch((e) => console.error('load narration presets failed', e))
    return () => {
      cancelled = true
    }
  }, [])

  const refresh = useCallback(async () => {
    try {
      setPresets(await api.listNarrationPresets())
    } catch (e) {
      console.error('load narration presets failed', e)
    }
  }, [])

  const savePreset = useCallback(async (body: CreateNarrationPresetBody) => {
    const created = await api.createNarrationPreset(body)
    setPresets((prev) => [...prev, created])
    return created
  }, [])

  const deletePreset = useCallback(async (id: string) => {
    await api.deleteNarrationPreset(id)
    setPresets((prev) => prev.filter((p) => p.id !== id))
  }, [])

  return { presets, refresh, savePreset, deletePreset }
}
