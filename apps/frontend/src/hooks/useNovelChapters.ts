import { api, type ChapterFile } from '@/api/client'
import { useRunResource } from './useRunResource'

const EMPTY: ChapterFile[] = []

/** 拉取某 run 小说的逐章文件列表；切 run 时重置并重取。 */
export function useNovelChapters(runId: string | null) {
  const { data: chapters, loading, error } = useRunResource(runId, api.listRunChapters, EMPTY)
  return { chapters, loading, error }
}
