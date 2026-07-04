import { useCallback, useState } from 'react'
import { api, type FsListing } from '@/api/client'

/** 服务器端目录浏览器：逐层进入 / 返回上一级。省略路径打开时定位到 home。 */
export function useDirBrowser() {
  const [listing, setListing] = useState<FsListing | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const go = useCallback(async (path?: string) => {
    setLoading(true)
    setError(null)
    try {
      setListing(await api.listFs(path))
    } catch (e) {
      setError(e instanceof Error ? e.message : '无法打开该目录')
    } finally {
      setLoading(false)
    }
  }, [])

  const up = useCallback(() => {
    if (listing && listing.parent !== listing.path) void go(listing.parent)
  }, [listing, go])

  const atRoot = !!listing && listing.parent === listing.path

  return { listing, loading, error, atRoot, go, up }
}
